#!/usr/bin/env python3
"""
zone_manager_node_v3.py  (v1 self-report 방식 + 대기열 슬롯 지정)

zone_manager_node.py(v1)를 복사해 만든 독립 파일입니다. 기존 파일은 수정하지 않았고,
토픽 이름공간도 /zone_manager_v3/* 로 분리했으므로 v1/v2 매니저와 동시에 켜도 섞이지 않습니다.

[v1과의 차이]
- v1 : 점유 중이면 대기열에 "순서"만 등록
- v3 : 대기열 순번 k 에 맞춰 "대기 슬롯 좌표"를 로봇에게 알려 준다.
       슬롯 k = 앵커 + k * 간격 * 후방벡터  (k=0 이 앵커, 줄의 맨 앞)

[토픽] (모두 std_msgs/String)
  구독  /zone_manager_v3/request_entry   "<robot_id>:<token>"
  구독  /zone_manager_v3/notify_exit     "<robot_id>"
  구독  /zone_manager_v3/cancel_entry    "<robot_id>"            (대기 포기)
  발행  /zone_manager_v3/grant_entry     "<robot_id>:<token>"
  발행  /zone_manager_v3/status          "free" | "occupied_by:<robot_id>"
  발행  /zone_manager_v3/queue_slot      "<robot_id>:<k>:<x>,<y>,<yaw>"   (k=-1 이면 대기열 가득 참)

[전체 동작 요약]
  1) 로봇이 위험구역 앞에서 request_entry 를 보낸다 (1초마다 재전송 = "나 아직 기다리는 중").
  2) 구역이 비어 있으면 즉시 grant_entry (슬롯 이동 없이 바로 진입).
  3) 점유 중이면 FIFO 대기열에 넣고, 순번 k 에 해당하는 슬롯 좌표를 queue_slot 으로 알려 준다.
  4) 점유자가 notify_exit 하면 선두 대기자에게 grant_entry, 나머지는 한 칸 앞 슬롯으로 전진시킨다.

[구성]
  - 순수 로직 : normalize_dir / slot_pose / ZoneQueue  (ROS·락 없음, 단위 테스트 대상)
  - ROS 노드  : ZoneManagerNodeV3  (토픽 입출력, 락, 타이머, 슬롯 발행 타이밍 제어)

[안전장치]
- max_hold_sec        : 점유자가 notify_exit 를 못 보내도 강제 해제 (v1과 동일)
- queue_stale_sec     : 대기자의 request_entry 재전송이 끊기면 큐에서 제거
- queue_max           : 대기자 수 상한 (넘으면 k=-1 거부, 슬롯 겹침 금지)
- queue_advance_delay : 앞 로봇이 앵커를 떠날 시간을 주고 나서 뒤 로봇을 전진시킴
"""

import math
import threading
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String


# ==============================================================================
# 순수 로직 (ROS 없이 테스트 가능)
# ==============================================================================

# ---후방 벡터 정규화---
# [기능] 후방 벡터(대기열이 뻗어 나가는 방향)를 길이 1의 단위 벡터로 정규화한다.
#        길이가 0이거나 숫자가 아니거나 원소가 부족하면 default 를 돌려줘 노드가 죽지 않게 한다.
def normalize_dir(back_dir, default=(-1.0, 0.0)):
    """후방 벡터를 단위 벡터로. 길이가 0이거나 형식이 틀리면 default."""
    try:
        dx, dy = float(back_dir[0]), float(back_dir[1])
    except (TypeError, ValueError, IndexError):
        return default
    norm = math.hypot(dx, dy)        # 벡터 길이 = √(dx²+dy²)
    if norm < 1e-9:                  # 길이가 거의 0이면 방향이 없는 것 → default
        return default
    return (dx / norm, dy / norm)    # 길이를 1로 맞춤 (단위 벡터) - 방향을 일정하게 유지시켜주는 수식


# ---대기 슬롯 좌표 계산---
# [기능] 대기 슬롯 k 번의 목표 자세 (x, y, yaw) 를 계산한다.
#        k=0 은 앵커 그 자체, k 가 1 늘 때마다 후방 벡터 방향으로 spacing(m)만큼 멀어진다.
#        yaw 는 모든 슬롯이 앵커와 동일(줄 서는 로봇들이 같은 방향을 봄).
def slot_pose(k, anchor, back_dir, spacing):
    """슬롯 k 의 (x, y, yaw). k=0 이 앵커, k 가 커질수록 후방 벡터 쪽으로 spacing 씩 멀어짐."""
    ax, ay, ayaw = anchor
    dx, dy = back_dir
    return (ax + k * spacing * dx, ay + k * spacing * dy, ayaw)
    # anchor: 고정위치 / back_dir: 후방 증분 / spacing: 간격


class ZoneQueue:
    """
    점유자 1명 + FIFO 대기열. ROS 와 락에 의존하지 않는다 (호출자가 락을 잡고 사용).
    시간(now)은 인자로 받아서 테스트에서 가짜 시계를 쓸 수 있다.

    상태:
      occupant       : 지금 구역을 점유(허가받음)한 robot_id, 없으면 None
      occupant_since : 점유 시작 시각 (max_hold_sec 판단용)
      queue          : 대기자 목록. 인덱스가 곧 슬롯 번호 k (0 = 앵커 = 줄의 맨 앞)
    """

    # ---큐 상태 초기화---
    # [기능] 큐 상태를 초기화한다. 점유자 없음, 대기열 비어 있음,
    #        대기 인원 상한(queue_max)과 대기자 생존 판정 시간(stale_sec)을 저장한다.
    def __init__(self, queue_max=3, stale_sec=5.0):
        self.queue_max = queue_max
        self.stale_sec = stale_sec
        self.occupant = None
        self.occupant_since = None
        # [[robot_id, token, last_seen], ...]  last_seen = 마지막 request_entry 수신 시각
        self.queue = []

    # ---대기 순서 조회---
    # [기능] 현재 대기자의 robot_id 목록을 순서대로 반환한다. (리스트 인덱스 = 슬롯 번호 k)
    def order(self):
        """대기자 robot_id 목록 (인덱스 = 슬롯 번호 k)."""
        return [e[0] for e in self.queue]

    # ---점유자 기록(내부용)---
    # [기능] (내부용) 점유자를 지정하고 점유 시작 시각을 기록한다.
    #        시작 시각은 hold_exceeded() 의 강제 해제 판단에 쓰인다.
    def _set_occupant(self, robot_id, now):
        """점유자를 지정하고 점유 시작 시각을 기록 (내부용)."""
        self.occupant = robot_id
        self.occupant_since = now

    # ---진입 요청 판단 (허가/대기/거부)---
    # [기능] 로봇의 진입 요청을 규칙에 따라 판단한다. (핵심 로직)
    #        점유자 재요청 -> 재허가 / 빈 구역 -> 즉시 점유 / 이미 대기 중 -> 순번 유지 /
    #        대기열 가득 -> 거부 / 그 외 -> 맨 뒤 등록.
    def request(self, robot_id, token, now):
        """
        request_entry 처리의 핵심 규칙. 규칙 적용 순서가 중요하다:
          1) 이미 점유자 -> 재허가 (grant 메시지 유실 대비)
          2) 빈 구역 + 대기자 없음 -> 즉시 점유
          3) 이미 대기 중 -> 순번은 그대로, 토큰/생존 시각만 갱신 (순번을 새치기로 바꾸지 않음)
          4) 대기열 가득 -> 거부 (큐에 넣지 않아 슬롯이 겹치지 않음)
          5) 그 외 -> 줄의 맨 뒤에 등록

        반환:
          ('grant', robot_id, token) : 지금 바로 허가 (빈 구역 + 대기자 없음, 또는 점유자의 재요청)
          ('queued',)                : 대기열에 등록(또는 갱신)
          ('full',)                  : 대기열 가득 참 (큐에 넣지 않음)
        """
        if self.occupant == robot_id:                  # 1) 이미 점유자
            return ('grant', robot_id, token)            # 값 3개를 묶음(튜플) 하나로 반환
        if self.occupant is None and not self.queue:   # 2) 빈 구역 + 대기자 없음
            self._set_occupant(robot_id, now)
            return ('grant', robot_id, token)
        for e in self.queue:                           # 3) 이미 대기 중
            if e[0] == robot_id:
                e[1], e[2] = token, now                  # 토큰 최신화 + 살아 있음 표시
                return ('queued',)
        if len(self.queue) >= self.queue_max:          # 4) 대기열 가득
            return ('full',)
        self.queue.append([robot_id, token, now])      # 5) 맨 뒤 등록
        return ('queued',)                               # 칸이 1개인 튜플(쉼표가 있어야 튜플)

    # ---점유 해제 + 선두 승격---
    # [기능] 현재 점유를 해제하고, 대기자가 있으면 선두를 새 점유자로 승격시킨다.
    #        승격된 경우 ('grant', id, token) 을, 대기자가 없으면 None 을 반환한다.
    def release_and_advance(self, now):
        """점유 해제. 대기자가 있으면 선두를 점유자로 올리고 ('grant', id, token), 없으면 None."""
        self.occupant = None
        self.occupant_since = None
        if self.queue:
            robot_id, token, _ = self.queue.pop(0)
            self._set_occupant(robot_id, now)
            return ('grant', robot_id, token)
        return None

    # ---이탈 통보 처리 (점유자 확인)---
    # [기능] 로봇의 구역 이탈 통보(notify_exit)를 처리한다.
    #        통보한 로봇이 실제 점유자일 때만 해제하고 다음 대기자를 승격한다. (남의 락 해제 방지)
    def exit(self, robot_id, now):
        """notify_exit. 점유자가 아니면 (False, None), 맞으면 (True, 다음 grant 또는 None)."""
        if self.occupant != robot_id:
            return False, None
        return True, self.release_and_advance(now)

    # ---대기 포기 (큐에서 제거)---
    # [기능] 대기 중인 로봇을 큐에서 제거한다. (대기 포기)
    #        점유자의 락은 건드리지 않으며, 제거에 성공하면 True 를 반환한다.
    def cancel(self, robot_id):
        """대기 포기(cancel_entry). 큐에서 제거했으면 True (점유자 락은 건드리지 않음)."""
        for i, e in enumerate(self.queue):
            if e[0] == robot_id:
                del self.queue[i]
                return True
        return False

    # ---유령 대기자 정리---
    # [기능] request_entry 재전송이 stale_sec 넘게 끊긴 대기자를 큐에서 제거하고 그 id 목록을 반환한다.
    #        죽은/통신 두절 로봇이 슬롯을 계속 차지하는 "유령 대기자" 문제를 막는다.
    def expire_stale(self, now):
        """
        request_entry 재전송이 stale_sec 넘게 없는 대기자를 제거하고 id 목록을 반환.
        (로봇이 죽거나 통신이 끊겨 유령 대기자가 슬롯을 차지하는 것을 막는다. 점유자는 대상 아님)
        """
        gone = [e[0] for e in self.queue if now - e[2] > self.stale_sec]
        if gone:
            self.queue = [e for e in self.queue if e[0] not in gone]
        return gone

    # ---점유 시간 초과 검사---
    # [기능] 점유자가 max_hold_sec 보다 오래 점유 중인지 검사한다.
    #        초과 시 경과 초를, 아니면 None 을 반환한다. (notify_exit 유실 시 강제 해제용)
    def hold_exceeded(self, now, max_hold_sec):
        """점유 시간이 max_hold_sec 를 넘었으면 경과 초, 아니면 None. (notify_exit 유실 대비 안전장치)"""
        if self.occupant is None or self.occupant_since is None:
            return None
        held = now - self.occupant_since
        return held if held > max_hold_sec else None


# ==============================================================================
# 노드
# ==============================================================================
class ZoneManagerNodeV3(Node):
    """
    ZoneQueue 를 ROS 토픽/타이머에 연결하는 노드.
    ZoneQueue 의 판단 결과를 grant_entry / queue_slot / status 토픽으로 바꿔 내보내고,
    "슬롯을 언제 발행할지"(즉시 vs 지연 전진)를 책임진다.
    모든 상태 접근은 self._lock 아래에서 한다 (콜백/타이머가 서로 다른 스레드에서 돌 수 있음).
    """

    # ---노드 초기화 (파라미터·통신·타이머 등록)---
    # [기능] 노드 초기화.
    #        (1) 파라미터 선언/로드 (앵커·후방벡터는 잘못되면 로그 후 기본값 대체)
    #        (2) ZoneQueue·락·슬롯 발행 기록 생성
    #        (3) 구독자 3개(request/exit/cancel), 발행자 3개(grant/status/slot) 생성
    #        (4) 주기 타이머 3개(감시 1초 / 전진 반영 0.2초 / 재발행 1초) 등록
    def __init__(self):
        super().__init__('zone_manager_node_v3')

        # ---------------- 파라미터 ----------------
        self.declare_parameter('robot_ids', ['pinky1', 'pinky2'])
        self.declare_parameter('max_hold_sec', 120.0)
        # 대기열 (map 좌표계, m / rad)
        self.declare_parameter('queue_anchor', [0.0, 0.0, 0.0])
        self.declare_parameter('queue_back_dir', [-1.0, 0.0])
        self.declare_parameter('queue_spacing', 0.30)
        self.declare_parameter('queue_max', 3)
        self.declare_parameter('queue_stale_sec', 5.0)
        self.declare_parameter('queue_advance_delay_sec', 3.0)

        self.robot_ids = list(self.get_parameter('robot_ids').value)
        self.max_hold_sec = float(self.get_parameter('max_hold_sec').value)

        # 앵커(slot 0)와 후방 벡터는 잘못 설정돼도 노드가 죽지 않도록 로그 후 기본값으로 대체
        anchor = list(self.get_parameter('queue_anchor').value)      # queue_anchor 설정된 값을 읽음
        if len(anchor) != 3:
            self.get_logger().error(f'queue_anchor 는 [x, y, yaw] 여야 합니다: {anchor}')
            anchor = [0.0, 0.0, 0.0]
        self.anchor = tuple(float(v) for v in anchor)

        raw_dir = list(self.get_parameter('queue_back_dir').value)   # queue_back_dir 설정된 값을 읽음
        self.back_dir = normalize_dir(raw_dir)
        if normalize_dir(raw_dir, default=None) is None:
            self.get_logger().error(
                f'queue_back_dir 가 올바르지 않아 기본값 {self.back_dir} 사용: {raw_dir}')

        self.spacing = float(self.get_parameter('queue_spacing').value)   # spacing = queue_spacing 파라미터 값
        self.advance_delay = float(self.get_parameter('queue_advance_delay_sec').value)   # advance_delay = queue_advance_delay_sec 파라미터 값

        # ---------------- 상태 ----------------
        # 큐/점유 상태와 아래 발행 기록은 콜백·타이머가 함께 만지므로 하나의 락으로 보호
        self._lock = threading.Lock()
        self.zq = ZoneQueue(
            queue_max=int(self.get_parameter('queue_max').value),
            stale_sec=float(self.get_parameter('queue_stale_sec').value))
        # robot_id -> 마지막으로 발행한 슬롯 번호. 현재 큐 순번과 비교해 "바뀐 것만" 발행하고,
        # 1초 재발행 타이머가 유실 대비로 이 값을 그대로 다시 보낸다.
        self._sent_k = {}
        # 이 시각 전에는 슬롯을 "앞으로 당기지" 않는다 (선두가 앵커를 떠날 시간 확보)
        self._advance_after = 0.0

        qos = QoSProfile(depth=10,
                         reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST)

        # 입력: 진입 요청 / 이탈 통보 / 대기 취소.  출력: 허가 / 점유 상태 / 슬롯 좌표
        self.create_subscription(String, '/zone_manager_v3/request_entry', self._on_request, qos)
        self.create_subscription(String, '/zone_manager_v3/notify_exit', self._on_exit, qos)
        self.create_subscription(String, '/zone_manager_v3/cancel_entry', self._on_cancel, qos)
        self.grant_pub = self.create_publisher(String, '/zone_manager_v3/grant_entry', qos)
        self.status_pub = self.create_publisher(String, '/zone_manager_v3/status', qos)
        self.slot_pub = self.create_publisher(String, '/zone_manager_v3/queue_slot', qos)

        # 타이머 3개: 감시(1초) / 보류된 전진 반영(0.2초) / 슬롯 재발행(1초)
        self.create_timer(1.0, self._check_timeout)      # 점유 시간 / stale 감시
        self.create_timer(0.2, self._sync_slots)         # 지연된 슬롯 전진 반영
        self.create_timer(1.0, self._republish_slots)    # 유실 대비 재발행

        self.get_logger().info(
            f'Zone manager v3 시작 (robots={self.robot_ids}, max_hold_sec={self.max_hold_sec}, '
            f'anchor={self.anchor}, back_dir={self.back_dir}, spacing={self.spacing}, '
            f'queue_max={self.zq.queue_max}, stale={self.zq.stale_sec}s, '
            f'advance_delay={self.advance_delay}s)')

    # ---------------- 콜백 ----------------

    # ---진입 요청 수신 (허가/대기/거부 발행)---
    # [기능] /request_entry ("<robot_id>:<token>") 수신 콜백.
    #        메시지를 파싱해 ZoneQueue.request() 로 판단하고 결과에 따라 동작한다.
    #        grant  -> status 방송 + grant_entry 발행
    #        queued -> (신규면 로그) 슬롯 번호 동기화
    #        full   -> 경고 로그 + k=-1 슬롯 발행으로 거부 통보
    def _on_request(self, msg: String):
        """request_entry 수신: ZoneQueue 판단 결과(grant/queued/full)에 따라 알맞은 토픽 발행."""
        try:
            robot_id, token = msg.data.split(':', 1)
        except ValueError:
            self.get_logger().warn(f'잘못된 요청 형식: {msg.data}')
            return

        with self._lock:
            was_queued = robot_id in self.zq.order()
            result = self.zq.request(robot_id, token, time.time())

            if result[0] == 'grant':
                self._publish_status()
                self._grant(robot_id, token)
            elif result[0] == 'queued':
                if not was_queued:
                    self.get_logger().info(
                        f'[{robot_id}] 대기열 등록 (현재 점유: {self.zq.occupant}, '
                        f'대기중: {self.zq.order()})')
                self._sync_slots_locked()
            else:  # 'full'
                self.get_logger().warn(
                    f'[{robot_id}] 대기열 가득 참 (queue_max={self.zq.queue_max}) -> 거부')
                self.slot_pub.publish(String(data=f'{robot_id}:-1:0.0,0.0,0.0'))

    # ---이탈 통보 수신 (락 해제 + 다음 허가)---
    # [기능] /notify_exit ("<robot_id>") 수신 콜백.
    #        현재 점유자가 보낸 통보일 때만 락을 해제하고(_after_release) 다음 선두 허가 + 나머지 전진을 처리한다.
    #        점유자가 아닌 로봇의 통보는 경고 로그만 남기고 무시한다.
    def _on_exit(self, msg: String):
        """notify_exit 수신: 점유자일 때만 락 해제 -> 다음 선두 허가 + 나머지 전진."""
        robot_id = msg.data.strip()
        with self._lock:
            ok, nxt = self.zq.exit(robot_id, time.time())
            if not ok:
                self.get_logger().warn(
                    f'[{robot_id}] notify_exit 수신했지만 현재 점유자는 '
                    f'{self.zq.occupant} 입니다 (무시)')
                return
            self.get_logger().info(f'[{robot_id}] zone 이탈 통보 -> 락 해제')
            self._after_release(nxt)

    # ---대기 취소 수신 (큐 제거 + 뒤 로봇 전진)---
    # [기능] /cancel_entry ("<robot_id>") 수신 콜백.
    #        대기자를 큐에서 빼고 슬롯 기록을 지운 뒤, 뒤 로봇들이 앞으로 당겨지도록
    #        지연 타이머를 걸고 슬롯을 다시 맞춘다. 점유자의 락은 유지된다.
    def _on_cancel(self, msg: String):
        """cancel_entry 수신: 대기자를 큐에서 빼고 뒤 로봇을 전진시킨다 (점유자 락은 유지)."""
        robot_id = msg.data.strip()
        with self._lock:
            if self.zq.cancel(robot_id):
                self.get_logger().info(
                    f'[{robot_id}] 대기 취소 -> 대기열: {self.zq.order()}')
                self._sent_k.pop(robot_id, None)
                self._advance_after = time.time() + self.advance_delay
                self._sync_slots_locked()
            else:
                self.get_logger().warn(f'[{robot_id}] cancel_entry 수신했지만 대기열에 없음 (무시)')

    # ---------------- 주기 로직 ----------------

    # ---주기 감시 (1초: 유령 대기자 + 점유 초과)---
    # [기능] 1초 주기 감시 타이머.
    #        (1) request_entry 재전송이 끊긴 stale 대기자를 제거하고 뒤 로봇을 전진시킨다.
    #        (2) 점유 시간이 max_hold_sec 를 넘으면 notify_exit 유실로 보고 강제 해제한다.
    def _check_timeout(self):
        """1초 주기 감시: (1) stale 대기자 제거 (2) 점유 시간 초과 시 강제 해제."""
        with self._lock:
            now = time.time()

            gone = self.zq.expire_stale(now)
            if gone:
                self.get_logger().warn(
                    f'{gone} request_entry 재전송 끊김 '
                    f'(> {self.zq.stale_sec}s) -> 대기열에서 제거')
                for rid in gone:
                    self._sent_k.pop(rid, None)
                self._advance_after = now + self.advance_delay
                self._sync_slots_locked()

            held = self.zq.hold_exceeded(now, self.max_hold_sec)
            if held is not None:
                self.get_logger().warn(
                    f'[{self.zq.occupant}] {held:.0f}초 동안 notify_exit 없음 '
                    f'-> 강제 해제 (max_hold_sec={self.max_hold_sec})')
                self._after_release(self.zq.release_and_advance(now))

    # ---보류된 전진 반영 (0.2초)---
    # [기능] 0.2초 주기 타이머. advance_delay 때문에 보류해 둔 "전진" 슬롯을
    #        지연 시간이 지난 뒤 실제로 발행하도록 락을 잡고 동기화를 호출한다.
    def _sync_slots(self):
        """0.2초 주기: 지연(advance_delay) 때문에 보류해 둔 "전진" 슬롯을 시간이 되면 발행."""
        with self._lock:
            self._sync_slots_locked()

    # ---유실 대비 재발행 (1초)---
    # [기능] 1초 주기 타이머. 메시지 유실에 대비해 현재 점유 상태(status)와
    #        이미 알려 준 모든 대기자의 슬롯 좌표를 그대로 다시 발행한다.
    def _republish_slots(self):
        """유실 대비: 이미 알려 준 슬롯을 그대로 다시 발행."""
        with self._lock:
            self._publish_status()
            for rid, k in self._sent_k.items():
                self._publish_slot(rid, k)

    # ---------------- 상태 전이 (lock 보유 상태에서 호출) ----------------

    # ---해제 후처리 (허가 + 전진 예약)---
    # [기능] 점유 해제 직후의 공통 후처리 (락 보유 상태에서 호출).
    #        status 방송 -> 승격된 선두에게 grant_entry 발행(슬롯 기록 삭제) ->
    #        나머지 대기자는 advance_delay 후에 앞 슬롯으로 전진하도록 예약.
    def _after_release(self, nxt):
        """해제 직후 처리: 상태 방송, 다음 선두 허가, 나머지는 지연 후 전진."""
        self._publish_status()
        if nxt is not None:
            _, next_id, next_token = nxt
            self._sent_k.pop(next_id, None)       # 선두는 더 이상 대기자가 아님
            self._grant(next_id, next_token)
        self._advance_after = time.time() + self.advance_delay
        self._sync_slots_locked()

    # ---슬롯 번호 동기화 (바뀐 것만 발행)---
    # [기능] 현재 대기열 순번(k)과 마지막으로 발행한 슬롯 번호를 비교해 "바뀐 것만" 발행한다. (락 보유 상태에서 호출)
    #        - 새 대기자 / 뒤로 밀리는 경우(k 증가) : 즉시 발행
    #        - 앞으로 당겨지는 경우(k 감소)         : _advance_after 이후에 발행
    #          (앞 로봇이 앵커를 떠날 시간을 줘서 충돌 방지)
    def _sync_slots_locked(self):
        """
        대기열 순번과 마지막 발행 슬롯을 맞춘다.
        - 새로 들어온 대기자 / 뒤로 밀리는 경우: 즉시 발행
        - 앞으로 당겨지는 경우(k 감소): _advance_after 이후에 발행 (앞 로봇이 떠날 시간)
        """
        now = time.time()
        for k, rid in enumerate(self.zq.order()):
            sent = self._sent_k.get(rid)
            if sent == k:
                continue
            if sent is not None and k < sent and now < self._advance_after:
                continue
            self._sent_k[rid] = k
            self._publish_slot(rid, k)

    # ---------------- 발행 ----------------

    # ---슬롯 좌표 발행---
    # [기능] 슬롯 번호 k 를 map 좌표 (x, y, yaw) 로 변환해
    #        "<robot_id>:<k>:<x>,<y>,<yaw>" 형식으로 /queue_slot 에 발행한다.
    def _publish_slot(self, robot_id, k):
        x, y, yaw = slot_pose(k, self.anchor, self.back_dir, self.spacing)
        self.slot_pub.publish(String(data=f'{robot_id}:{k}:{x:.3f},{y:.3f},{yaw:.3f}'))

    # ---진입 허가 발행---
    # [기능] 해당 로봇에게 진입 허가를 내린다.
    #        "<robot_id>:<token>" 을 /grant_entry 로 발행하고 로그를 남긴다.
    def _grant(self, robot_id, token):
        self.grant_pub.publish(String(data=f'{robot_id}:{token}'))
        self.get_logger().info(f'[{robot_id}] 진입 허가 (token={token})')

    # ---점유 상태 발행---
    # [기능] 구역 점유 상태를 /status 로 방송한다.
    #        점유자가 있으면 "occupied_by:<robot_id>", 없으면 "free".
    def _publish_status(self):
        data = f'occupied_by:{self.zq.occupant}' if self.zq.occupant else 'free'
        self.status_pub.publish(String(data=data))


# ---프로그램 시작점---
# [기능] 프로그램 진입점. rclpy 초기화 -> 노드 생성 -> spin(콜백/타이머 처리) ->
#        Ctrl+C 또는 종료 시 노드 정리 및 rclpy shutdown.
def main():
    rclpy.init()
    node = ZoneManagerNodeV3()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()