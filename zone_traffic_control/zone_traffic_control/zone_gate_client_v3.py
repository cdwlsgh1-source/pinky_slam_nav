#!/usr/bin/env python3
"""
zone_gate_client_v3.py

zone_manager_node_v3(대기열 슬롯 방식)와 통신하는 로봇 쪽 헬퍼 클래스.
zone_gate_client.py(v1)를 복사해 독립 파일로 만들었습니다 (v1 클래스를 import하지 않음).
복구가 필요하면 patrol 노드가 v1/v2 클라이언트를 쓰도록 되돌리면 됩니다.

v1 과의 차이
  - 토픽 이름공간이 /zone_manager_v3/* 입니다.
  - /zone_manager_v3/queue_slot 을 구독해 내 대기 슬롯을 current_slot 으로 노출합니다.
  - 이동 중에도 쓸 수 있는 비블로킹 API 가 추가되었습니다.
      reset() -> send_request() -> (이동 중) resend_if_due() / granted(token) / current_slot
      포기할 때는 cancel_entry()
  - wait_for_entry() / notify_exit() 는 v1 과 동작이 같습니다.

사용 예 (비블로킹):
    self.gate.reset()
    token = self.gate.send_request()
    while 이동중:
        self.gate.resend_if_due()          # 1초마다 재전송 (stale 제거 방지)
        if self.gate.granted(token): ...   # 허가 -> RED*IN 으로 진행
        slot = self.gate.current_slot      # (k, x, y, yaw) 또는 None
        if self.gate.queue_full: ...       # 대기열 가득 참
    # 중단/실패 시
    self.gate.cancel_entry()
"""

import time
import uuid

import rclpy
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String

NS = '/zone_manager_v3'
QUEUE_FULL_K = -1


# ---------------- 순수 로직 (ROS 없이 테스트 가능) ----------------
def parse_slot_msg(data):
    """'robot_id:k:x,y,yaw' -> (robot_id, k, x, y, yaw). 형식이 틀리면 None."""
    try:
        robot_id, k_str, pose_str = data.split(':', 2)
        x, y, yaw = (float(v) for v in pose_str.split(','))
        return robot_id, int(k_str), x, y, yaw
    except (ValueError, AttributeError):
        return None


def parse_grant_msg(data):
    """'robot_id:token' -> (robot_id, token). 형식이 틀리면 None."""
    try:
        robot_id, token = data.split(':', 1)
    except (ValueError, AttributeError):
        return None
    return robot_id, token


def choose_entry_target(granted, slot, door):
    """
    진입 대기 중 로봇이 "지금 어디로 가야 하는지"를 고른다 (patrol 노드 공용 판단 규칙).
      허가받음            -> ('door', door)       : 문(RED*IN)으로 진입
      대기열 가득(k=-1)   -> ('full', None)       : 포기해야 함
      슬롯 배정(k>=0)     -> ('slot', (x, y, yaw)): 매니저가 알려 준 대기 슬롯으로 이동
      아직 응답 없음      -> ('door', door)       : 빈 구역일 수 있으니 v2처럼 문으로 먼저 출발
    허가가 슬롯보다 항상 우선이다 (슬롯 메시지가 늦게 남아 있어도 허가 후엔 문으로 간다).
    """
    if granted:
        return 'door', door
    if slot is not None:
        k, x, y, yaw = slot
        if k == QUEUE_FULL_K:
            return 'full', None
        return 'slot', (x, y, yaw)
    return 'door', door


class RetryTimer:
    """주기(period)가 지났는지만 판단하는 작은 타이머. 시간은 호출자가 주입한다."""

    def __init__(self, period):
        self.period = period
        self._last = None

    def reset(self):
        self._last = None

    def mark(self, now):
        self._last = now

    def due(self, now):
        return self._last is None or (now - self._last) >= self.period


# ---------------- ROS 클라이언트 ----------------
class ZoneGateClient:
    """
    로봇 1대가 zone_manager_node_v3 와 주고받는 창구. 역할은 세 가지다.
      1) 요청/재전송/취소 : request_entry, cancel_entry 발행 (매니저는 재전송이 끊기면 stale 로 제거)
      2) 응답 수신       : grant_entry(허가 토큰), queue_slot(내 대기 슬롯) 을 받아 보관
      3) 이탈 통보       : notify_exit 로 락 해제
    patrol 노드는 이 클래스의 상태(granted / current_slot / queue_full)만 읽고,
    토픽 형식이나 재전송 주기는 알 필요가 없다.
    """

    def __init__(self, node, robot_id, retry_period_sec=1.0, clock=time.time):
        """
        node: rclpy Node (또는 BasicNavigator처럼 Node를 상속한 객체).
              patrol 코드에서 이미 goToPose 폴링 등에 쓰고 있는 navigator를
              그대로 넘기면 됩니다 (isTaskComplete 가 spin 하므로 콜백이 처리됨).
        robot_id: zone_manager_node_v3의 robot_ids 파라미터와 동일한 문자열
        clock: 현재 시각(초)을 돌려주는 함수 (테스트용 주입)
        """
        self.node = node
        self.robot_id = robot_id
        self.retry_period = retry_period_sec
        self._clock = clock
        self._granted_token = None   # 매니저가 마지막으로 허가해 준 토큰 (내 robot_id 것만)
        self._token = None           # 이번 진입 시도의 토큰 (send_request 가 만들고 재전송에 재사용)
        self._retry = RetryTimer(retry_period_sec)   # 재전송 주기 판단
        self._slot = None            # (k, x, y, yaw) — k=-1 이면 queue_full

        qos = QoSProfile(depth=10,
                          reliability=ReliabilityPolicy.RELIABLE,
                          history=HistoryPolicy.KEEP_LAST)

        self.request_pub = node.create_publisher(
            String, f'{NS}/request_entry', qos)
        self.exit_pub = node.create_publisher(
            String, f'{NS}/notify_exit', qos)
        self.cancel_pub = node.create_publisher(
            String, f'{NS}/cancel_entry', qos)
        self.grant_sub = node.create_subscription(
            String, f'{NS}/grant_entry', self._on_grant, qos)
        self.slot_sub = node.create_subscription(
            String, f'{NS}/queue_slot', self._on_slot, qos)

    # ----- 수신 -----
    def _on_grant(self, msg: String):
        """grant_entry 콜백: 다른 로봇 앞으로 온 허가는 무시하고 내 토큰만 저장."""
        parsed = parse_grant_msg(msg.data)
        if parsed and parsed[0] == self.robot_id:
            self._granted_token = parsed[1]

    def _on_slot(self, msg: String):
        """queue_slot 콜백: 내 robot_id 의 최신 슬롯만 보관 (형식 오류는 경고 후 무시)."""
        parsed = parse_slot_msg(msg.data)
        if parsed is None:
            self.node.get_logger().warn(
                f'[{self.robot_id}] queue_slot 형식 오류 (무시): {msg.data!r}')
            return
        robot_id, k, x, y, yaw = parsed
        if robot_id != self.robot_id:
            return
        self._slot = (k, x, y, yaw)

    # ----- 슬롯 상태 -----
    @property
    def current_slot(self):
        """(k, x, y, yaw) 또는 None. k=-1(대기열 가득 참)일 때도 튜플로 돌려준다."""
        return self._slot

    @property
    def queue_full(self):
        return self._slot is not None and self._slot[0] == QUEUE_FULL_K

    # ----- 비블로킹 API -----
    def reset(self):
        """새 진입 시도 전에 이전 허가/슬롯/토큰 상태를 지운다."""
        self._granted_token = None
        self._token = None
        self._slot = None
        self._retry.reset()

    def send_request(self):
        """request_entry 를 1회 발행하고 이번 시도의 토큰을 반환한다."""
        self._token = uuid.uuid4().hex[:8]
        self._publish_request()
        return self._token

    def resend_if_due(self):
        """마지막 발행 후 retry_period 가 지났으면 같은 토큰으로 재전송. 보냈으면 True."""
        if self._token is None:
            return False
        if self._retry.due(self._clock()):
            self._publish_request()
            return True
        return False

    def granted(self, token=None):
        """token(생략 시 마지막 send_request 의 토큰)으로 허가가 왔는지."""
        token = self._token if token is None else token
        return token is not None and self._granted_token == token

    def cancel_entry(self):
        """대기를 포기한다 (stop/실패/queue_full). 매니저가 큐에서 제거한다."""
        self.cancel_pub.publish(String(data=self.robot_id))
        self._token = None
        self._slot = None
        self._retry.reset()
        self.node.get_logger().info(f'[{self.robot_id}] zone 대기 취소 통보 전송')

    def _publish_request(self):
        """request_entry 를 실제로 발행하고 재전송 타이머를 갱신 (send/resend 공통)."""
        self.request_pub.publish(String(data=f'{self.robot_id}:{self._token}'))
        self._retry.mark(self._clock())

    # ----- 블로킹 API (v1 과 동일) -----
    def wait_for_entry(self, timeout_sec=None, stop_check=None):
        """
        허가가 날 때까지 블로킹.

        stop_check: 인자 없이 호출해서 True/False를 반환하는 콜백.
                    True를 반환하면 즉시 대기를 중단합니다.
                    (예: patrol 노드의 stop 명령 처리용)

        반환값: 허가받았으면 True, timeout이나 stop_check로 중단되면 False.
        """
        token = uuid.uuid4().hex[:8]
        self._granted_token = None
        start = time.time()

        while rclpy.ok():
            if stop_check is not None and stop_check():
                return False

            msg = String()
            msg.data = f'{self.robot_id}:{token}'
            self.request_pub.publish(msg)

            end_wait = time.time() + self.retry_period
            while time.time() < end_wait:
                rclpy.spin_once(self.node, timeout_sec=0.1)
                if self._granted_token == token:
                    self.node.get_logger().info(
                        f'[{self.robot_id}] zone 진입 허가 받음')
                    return True
                if stop_check is not None and stop_check():
                    return False
                if timeout_sec is not None and (time.time() - start) > timeout_sec:
                    return False

        return False

    def notify_exit(self):
        """zone을 완전히 벗어났음을 zone_manager에 통보 (락 즉시 해제)."""
        msg = String()
        msg.data = self.robot_id
        self.exit_pub.publish(msg)
        self.node.get_logger().info(f'[{self.robot_id}] zone 이탈 통보 전송')
