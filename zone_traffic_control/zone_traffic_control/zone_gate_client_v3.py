#!/usr/bin/env python3
"""
zone_gate_client_v3.py  (주석 정리본 - 로직은 원본과 동일)

==================== 한눈에 보는 전체 그림 ====================
비유: 이 파일은 "식당 입구의 번호표 기계 + 안내 직원"과 로봇 사이의 소통 창구입니다.

  로봇(patrol 노드)  <-->  ZoneGateClient  <-->  zone_manager_node_v3 (구역 관리자)

  [로봇이 하고 싶은 일]            [이 파일의 담당 함수]
  1) "들어가도 되나요?" 요청       -> send_request()
  2) 요청 계속 보내기(생존 신고)    -> resend_if_due()
  3) "허가 나왔나요?" 확인         -> granted()
  4) "어디서 기다릴까요?" 확인      -> current_slot / queue_full
  5) "그만 기다릴게요" 취소        -> cancel_entry()
  6) "구역에서 나왔어요" 통보       -> notify_exit()

==================== 이 파일의 3개 구성 ====================
  A. 순수 함수들 (parse_*, choose_entry_target) : ROS 없이도 테스트 가능한 "판단/해석" 담당
  B. RetryTimer                                : "1초 지났나?"만 알려주는 작은 타이머
  C. ZoneGateClient                            : 실제로 ROS 토픽을 주고받는 메인 클래스
"""

import time
import uuid

import rclpy
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

# 토픽 이름 앞에 붙는 공통 접두사 (이름공간). 예: '/zone_manager_v3/request_entry'
NS = '/zone_manager_v3'

# 슬롯 번호 k가 -1이면 "대기열이 가득 찼다"는 약속된 신호
QUEUE_FULL_K = -1


# =====================================================================
# A. 순수 로직 (ROS 없이 테스트 가능)
# =====================================================================
def parse_slot_msg(data):
    """
    [기능] 매니저가 보낸 문자열을 숫자로 풀어서 해석한다.
    [입력 예] 'robot1:2:1.5,3.0,0.0'
    [출력 예] ('robot1', 2, 1.5, 3.0, 0.0)  = (로봇ID, 슬롯번호k, x, y, yaw)
    [실패 시] 형식이 틀리면 None
    """
    try:
        # split(':', 2) -> ':' 기준으로 "최대 2번만" 자름 -> 3조각
        #   'robot1:2:1.5,3.0,0.0' -> ['robot1', '2', '1.5,3.0,0.0']
        robot_id, k_str, pose_str = data.split(':', 2)

        # pose_str.split(',') -> ['1.5', '3.0', '0.0']
        # (float(v) for v in ...) -> 각각 실수로 변환
        # 앞의 x, y, yaw = ... 는 "언패킹": 3개 값을 3개 변수에 한 번에 담기
        x, y, yaw = (float(v) for v in pose_str.split(','))

        return robot_id, int(k_str), x, y, yaw
    except (ValueError, AttributeError):
        # ValueError    : 조각 개수가 안 맞거나 숫자 변환 실패
        # AttributeError: data가 문자열이 아니라 .split()이 없을 때
        return None


def parse_grant_msg(data):
    """
    [기능] 허가 메시지 'robot_id:token' 을 (robot_id, token) 으로 쪼갠다.
    [출력 예] 'robot1:ab12cd34' -> ('robot1', 'ab12cd34'),  형식 오류 -> None
    """
    try:
        # split(':', 1) -> 첫 번째 ':' 에서만 1번 자름 (토큰에 ':'가 섞여도 안전)
        robot_id, token = data.split(':', 1)
    except (ValueError, AttributeError):
        return None
    return robot_id, token


def choose_entry_target(granted, slot, door):
    """
    [기능] "지금 로봇이 어디로 가야 하지?"를 결정하는 규칙표 (patrol 노드 공용).

    우선순위 (위에서부터 먼저 걸리는 것이 승리):
      1순위  허가받음          -> ('door', door)        문으로 진입
      2순위  슬롯 있음 + k=-1  -> ('full', None)        대기열 가득 -> 포기
      3순위  슬롯 있음 + k>=0  -> ('slot', (x, y, yaw)) 배정된 대기 장소로 이동
      4순위  아직 응답 없음     -> ('door', door)        일단 문 쪽으로 먼저 출발

    왜 '허가'가 슬롯보다 우선? 예전 슬롯 메시지가 남아있어도,
    허가를 받았다면 대기 장소가 아니라 문으로 가야 하기 때문.
    """
    if granted:                       # 1순위: 허가받음
        return 'door', door
    if slot is not None:              # 슬롯 정보가 도착한 경우
        k, x, y, yaw = slot           # 튜플 언패킹
        if k == QUEUE_FULL_K:         # 2순위: 대기열 가득
            return 'full', None
        return 'slot', (x, y, yaw)    # 3순위: 대기 슬롯으로 이동
    return 'door', door               # 4순위: 아직 아무 응답 없음


class RetryTimer:
    """
    [기능] "마지막으로 보낸 지 period초 지났나?"만 판단하는 작은 타이머.
    현재 시간은 직접 읽지 않고 호출자가 now로 넘겨줌 -> 테스트하기 쉬움.
    (이렇게 시간을 바깥에서 주입하는 방식을 '의존성 주입'이라 부름)
    """

    def __init__(self, period):
        self.period = period   # 재전송 주기(초)
        self._last = None      # 마지막 발행 시각. None = 아직 한 번도 안 보냄

    def reset(self):
        """기록 지우기 -> 다음 due() 는 무조건 True"""
        self._last = None

    def mark(self, now):
        """'방금 보냈음' 기록"""
        self._last = now

    def due(self, now):
        """보낼 때가 됐나? (처음이거나, 마지막 발행 후 period 이상 지났으면 True)"""
        return self._last is None or (now - self._last) >= self.period


# =====================================================================
# C. ROS 클라이언트 (메인 클래스)
# =====================================================================
class ZoneGateClient:
    """
    로봇 1대가 zone_manager_node_v3 와 주고받는 창구. 역할은 세 가지:
      1) 요청/재전송/취소 : request_entry, cancel_entry 발행
                            (매니저는 재전송이 끊기면 '죽은 로봇(stale)'으로 보고 제거)
      2) 응답 수신       : grant_entry(허가 토큰), queue_slot(내 대기 슬롯) 보관
      3) 이탈 통보       : notify_exit 로 락 해제
    patrol 노드는 이 클래스의 상태(granted / current_slot / queue_full)만 읽으면 되고,
    토픽 형식이나 재전송 주기는 몰라도 된다.
    """

    def __init__(self, node, robot_id, retry_period_sec=1.0, clock=time.time):
        """
        node : rclpy Node (BasicNavigator 같은 Node 상속 객체도 가능)
        robot_id : 매니저의 robot_ids 파라미터와 똑같은 문자열 (예: 'robot1')
        clock : 현재 시각(초)을 돌려주는 함수. 기본은 time.time (테스트 땐 가짜 시계 주입)
        """
        self.node = node
        self.robot_id = robot_id
        self.retry_period = retry_period_sec
        self._clock = clock

        # ---- 내부 상태 변수들 (앞에 _ 는 "클래스 안에서만 쓰는 값"이라는 관례) ----
        self._granted_token = None  # 매니저가 허가해 준 토큰 (내 robot_id 것만 저장)
        self._token = None          # 이번 시도에서 내가 만든 토큰 (재전송 때 재사용)
        self._retry = RetryTimer(retry_period_sec)  # 재전송 주기 판단용
        self._slot = None           # (k, x, y, yaw). k=-1 이면 queue_full

        # QoS = 통신 품질 설정
        qos = QoSProfile(depth=10,                                # 최대 10개까지 보관
                         reliability=ReliabilityPolicy.RELIABLE,  # 유실되면 재시도(신뢰성 우선)
                         history=HistoryPolicy.KEEP_LAST)         # 최근 것만 유지

        # ---- 발행자(publisher): 로봇 -> 매니저 ----
        self.request_pub = node.create_publisher(String, f'{NS}/request_entry', qos)  # 진입 요청
        self.exit_pub = node.create_publisher(String, f'{NS}/notify_exit', qos)       # 이탈 통보
        self.cancel_pub = node.create_publisher(String, f'{NS}/cancel_entry', qos)    # 대기 취소

        # ---- 구독자(subscriber): 매니저 -> 로봇 ----
        # 메시지가 올 때마다 마지막 인자의 콜백 함수가 자동 호출됨
        self.grant_sub = node.create_subscription(
            String, f'{NS}/grant_entry', self._on_grant, qos)   # 허가 수신
        self.slot_sub = node.create_subscription(
            String, f'{NS}/queue_slot', self._on_slot, qos)     # 슬롯 수신

    # ------------------------------------------------------------------
    # 수신 콜백 (메시지가 오면 ROS가 자동으로 호출)
    # ------------------------------------------------------------------
    def _on_grant(self, msg: String):
        """허가 메시지 수신: 다른 로봇 앞으로 온 건 무시, 내 것만 저장."""
        parsed = parse_grant_msg(msg.data)
        # parsed가 None이 아니고(and), 첫 값(robot_id)이 내 ID와 같을 때만 저장
        if parsed and parsed[0] == self.robot_id:
            self._granted_token = parsed[1]

    def _on_slot(self, msg: String):
        """슬롯 메시지 수신: 내 robot_id 의 최신 슬롯만 보관."""
        parsed = parse_slot_msg(msg.data)
        if parsed is None:                       # 형식 오류 -> 경고만 남기고 무시
            self.node.get_logger().warn(
                f'[{self.robot_id}] queue_slot 형식 오류 (무시): {msg.data!r}')
            return
        robot_id, k, x, y, yaw = parsed
        if robot_id != self.robot_id:            # 남의 슬롯이면 버림
            return
        self._slot = (k, x, y, yaw)              # 내 것이면 최신값으로 덮어씀

    # ------------------------------------------------------------------
    # 슬롯 상태 (읽기 전용 속성)
    # ------------------------------------------------------------------
    # @property: "값 조회"용 (괄호 없이 읽기) / 동작·인자 필요한 함수는 일반 메서드
        # 사용조건
        # 1) 인자 없음 -> Yes
        # 2) 상태 안 바뀜 -> Yes
        # 3) 값만 조회 -> Yes
    @property
    def current_slot(self):
        """(k, x, y, yaw) 또는 None. k=-1(대기열 가득)일 때도 튜플로 돌려준다."""
        return self._slot

    @property
    def queue_full(self):
        """대기열이 가득 찼는가? (슬롯이 있고 + k가 -1)"""
        return self._slot is not None and self._slot[0] == QUEUE_FULL_K

    # ------------------------------------------------------------------
    # 비블로킹 API: 이동 중에도 쓸 수 있음 (기다리지 않고 바로 리턴)
    #
    # 전형적인 사용 흐름:
    #   reset() -> send_request() -> [이동 루프: resend_if_due() / granted() / current_slot]
    #                              -> 중단하면 cancel_entry()
    # ------------------------------------------------------------------
    def reset(self):
        """새 진입 시도 전에 이전 허가/슬롯/토큰 상태를 전부 지운다 (깨끗한 출발)."""
        self._granted_token = None
        self._token = None
        self._slot = None
        self._retry.reset()

    def send_request(self):
        """
        진입 요청을 1회 발행하고, 이번 시도의 토큰(번호표)을 돌려준다.
        흐름: 토큰 생성 -> 요청 발행 -> 토큰 반환(나중에 granted(token)으로 확인)
        uuid.uuid4().hex[:8] = 무작위 고유 문자열의 앞 8글자 (예: 'a3f9c21b')
        """
        self._token = uuid.uuid4().hex[:8]
        self._publish_request()
        return self._token

    def resend_if_due(self):
        """
        1초(retry_period)가 지났으면 같은 토큰으로 요청을 다시 보낸다. 보냈으면 True.
        왜 재전송? 매니저는 재전송이 끊기면 '죽은 로봇'으로 보고 큐에서 지우기 때문.
        (= 식당에서 "저 아직 기다리고 있어요" 하고 주기적으로 손 흔드는 것)
        """
        if self._token is None:        # 아직 send_request를 안 했으면 보낼 게 없음
            return False
        if self._retry.due(self._clock()):
            self._publish_request()
            return True
        return False

    def granted(self, token=None):
        """
        허가가 왔는가? (매니저가 돌려준 토큰 == 내가 보낸 토큰)
        token을 생략하면 마지막 send_request의 토큰으로 비교.
        """
        token = self._token if token is None else token
        return token is not None and self._granted_token == token

    def cancel_entry(self):
        """대기 포기 (stop/실패/queue_full 시). 매니저가 큐에서 나를 제거한다."""
        self.cancel_pub.publish(String(data=self.robot_id))
        # 내 쪽 상태도 정리
        self._token = None
        self._slot = None
        self._retry.reset()
        self.node.get_logger().info(f'[{self.robot_id}] zone 대기 취소 통보 전송')

    def _publish_request(self):
        """요청 실제 발행 + 재전송 타이머 갱신 (send/resend가 공통으로 사용)."""
        self.request_pub.publish(String(data=f'{self.robot_id}:{self._token}'))
        self._retry.mark(self._clock())   # '방금 보냈음' 기록

    # ------------------------------------------------------------------
    # 블로킹 API (v1 과 동일): 허가가 날 때까지 "멈춰서 기다림"
    # ------------------------------------------------------------------
    def wait_for_entry(self, timeout_sec=None, stop_check=None):
        """
        허가가 날 때까지 블로킹(그 자리에서 대기).

        stop_check : 인자 없이 호출해 True/False를 돌려주는 콜백.
                     True면 즉시 대기 중단 (예: patrol 노드의 stop 명령 처리)
        반환값     : 허가받았으면 True, timeout 또는 stop_check로 중단되면 False

        구조: 바깥 루프(요청 발행) 안에 안쪽 루프(1초간 응답 확인)가 있는 이중 루프
          바깥: 요청 발행 -> 안쪽 진입
          안쪽: 0.1초씩 spin_once 로 메시지 처리 -> 허가/중단/타임아웃 검사 -> 1초 지나면 바깥으로
        """
        token = uuid.uuid4().hex[:8]
        self._granted_token = None
        start = time.time()

        while rclpy.ok():                       # ROS가 살아있는 동안 반복
            if stop_check is not None and stop_check():
                return False

            msg = String()
            msg.data = f'{self.robot_id}:{token}'
            self.request_pub.publish(msg)       # 요청 발행

            end_wait = time.time() + self.retry_period   # 이번 라운드 종료 시각
            while time.time() < end_wait:
                # spin_once: 들어온 메시지를 최대 0.1초 동안 처리 (콜백이 여기서 실행됨!)
                rclpy.spin_once(self.node, timeout_sec=0.1)
                if self._granted_token == token:         # 허가 도착
                    self.node.get_logger().info(
                        f'[{self.robot_id}] zone 진입 허가 받음')
                    return True
                if stop_check is not None and stop_check():   # 외부 중단 요청
                    return False
                if timeout_sec is not None and (time.time() - start) > timeout_sec:
                    return False                              # 시간 초과

        return False

    def notify_exit(self):
        """zone을 완전히 벗어났음을 매니저에 통보 (락 즉시 해제)."""
        msg = String()
        msg.data = self.robot_id
        self.exit_pub.publish(msg)
        self.node.get_logger().info(f'[{self.robot_id}] zone 이탈 통보 전송')