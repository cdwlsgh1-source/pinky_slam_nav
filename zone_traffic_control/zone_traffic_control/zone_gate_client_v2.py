#!/usr/bin/env python3
"""
zone_gate_client_v2.py

zone_manager_node_v2 가 발행하는 grant / hold / resume 를 받아 로봇 쪽에서 처리하는 헬퍼.
로봇은 더 이상 request_entry 를 보내지 않는다 (구역 진입 판정은 매니저가 amcl_pose 로 수행).

사용 예 (PinkyPatrolNode 의 주행 폴링 루프 안에서):
    self.gate = ZoneGateClientV2(self.navigator, self.navigator, robot_id='pinky1')

    while not self.navigator.isTaskComplete():
        if self.gate.hold_requested:
            # 현재 Nav2 goal 취소 -> 정지 좌표로 이동 -> resume 까지 대기
            self.gate.service_hold(stop_check=lambda: self._stop_requested)
            self.navigator.goToPose(current_goal)   # 원래 goal 재전송
        ...

    # 구역을 벗어난 직후 (선택, 매니저가 거리로도 자동 회수함)
    self.gate.release_token()

hold 처리를 콜백이 아니라 호출자 루프에서 하도록 한 이유: patrol 루프가 쓰는
navigator 의 goal 을 다른 스레드에서 취소하면 루프가 "goal 실패"로 오인하기 때문.
"""

import math
import time

import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from std_msgs.msg import String


class ZoneGateClientV2:
    def __init__(self, node, navigator, robot_id, hold_goal_tolerance_sec=120.0):
        """
        node: rclpy Node (BasicNavigator 도 Node 이므로 그대로 넘겨도 됨)
        navigator: nav2_simple_commander.BasicNavigator
        robot_id: zone_manager_node_v2 의 robot_ids 와 동일한 문자열
        hold_goal_tolerance_sec: 정지 좌표로 이동하는 최대 시간(초)
        """
        self.node = node
        self.navigator = navigator
        self.robot_id = robot_id
        self.hold_timeout = hold_goal_tolerance_sec

        self.token = None            # 현재 보유한 토큰 (없으면 None)
        self.hold_requested = False  # 매니저가 hold 를 지시했는가
        self.hold_pose = None        # (x, y, yaw)

        qos = QoSProfile(depth=10,
                         reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST)
        node.create_subscription(String, '/zone_manager_v2/grant', self._on_grant, qos)
        node.create_subscription(String, '/zone_manager_v2/hold', self._on_hold, qos)
        node.create_subscription(String, '/zone_manager_v2/resume', self._on_resume, qos)
        self.release_pub = node.create_publisher(String, '/zone_manager_v2/release', qos)

    # ---------------- 콜백 ----------------

    def _on_grant(self, msg: String):
        try:
            robot_id, token = msg.data.split(':', 1)
        except ValueError:
            return
        if robot_id != self.robot_id:
            return
        if self.token != token:
            self.node.get_logger().info(f'[{self.robot_id}] 토큰 수신: {token}')
        self.token = token
        self.hold_requested = False

    def _on_hold(self, msg: String):
        try:
            robot_id, rest = msg.data.split(':', 1)
            x, y, yaw = (float(v) for v in rest.split(','))
        except ValueError:
            return
        if robot_id != self.robot_id:
            return
        if not self.hold_requested:
            self.node.get_logger().info(
                f'[{self.robot_id}] hold 지시 수신 -> 정지 좌표 ({x:.2f}, {y:.2f})')
        self.hold_pose = (x, y, yaw)
        self.hold_requested = True

    def _on_resume(self, msg: String):
        if msg.data.strip() != self.robot_id:
            return
        if self.hold_requested:
            self.node.get_logger().info(f'[{self.robot_id}] resume 수신')
        self.hold_requested = False
        self.token = None

    # ---------------- 호출자 API ----------------

    @property
    def has_token(self):
        return self.token is not None

    def service_hold(self, stop_check=None):
        """
        Nav2 goal 취소 -> 정지 좌표로 이동 -> resume 수신까지 대기.
        반환: resume 으로 정상 종료하면 True, stop_check 로 중단되면 False.
        호출 후 호출자는 원래 goal 을 다시 보내야 한다.
        """
        if self.hold_pose is None:
            return True

        self.navigator.cancelTask()
        x, y, yaw = self.hold_pose
        self.navigator.goToPose(self._make_pose(x, y, yaw))

        start = time.time()
        while rclpy.ok() and self.hold_requested:
            if stop_check is not None and stop_check():
                self.navigator.cancelTask()
                return False
            if (not self.navigator.isTaskComplete()
                    and time.time() - start > self.hold_timeout):
                self.node.get_logger().warn(f'[{self.robot_id}] 정지 좌표 이동 시간 초과')
                self.navigator.cancelTask()
            rclpy.spin_once(self.node, timeout_sec=0.1)

        # resume 이 먼저 와서 아직 정지 좌표로 가는 중이면 goal 을 취소하고 복귀
        if not self.navigator.isTaskComplete():
            self.navigator.cancelTask()
        return True

    def release_token(self):
        """토큰 명시 반환 (구역을 벗어난 직후). 매니저는 거리 기반으로도 자동 회수한다."""
        if self.token is None:
            return
        self.release_pub.publish(String(data=f'{self.robot_id}:{self.token}'))
        self.node.get_logger().info(f'[{self.robot_id}] 토큰 반환: {self.token}')
        self.token = None

    # ---------------- 유틸 ----------------

    def _make_pose(self, x, y, yaw):
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.navigator.get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.orientation.z = math.sin(yaw / 2.0)
        pose.pose.orientation.w = math.cos(yaw / 2.0)
        return pose
