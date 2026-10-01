#!/usr/bin/env python3
"""
zone_manager_node_v2.py  (위치 기반 토큰 발행 방식)

로봇이 구역(zone) 반경 안으로 "지나가는 순간" 매니저가 직접 토큰을 발행하고,
나머지 로봇에게는 hold 명령을 내려 각자의 정지 위치에서 대기시킨다.

[기존(self-report) 방식과의 차이]
- 기존: 로봇이 request_entry 를 보내면 허가(grant)해 주는 방식, 매니저는 좌표를 모름
- v2  : 매니저가 /<robot_id>/amcl_pose 를 구독해 구역 반경 진입을 직접 판정하고
        먼저 들어온 로봇에게 토큰을 발행. 점유 중에는 나머지 로봇에 hold(정지 좌표) 발행.

[토픽] (기존 /zone_manager/* 와 충돌하지 않도록 /zone_manager_v2/* 사용, 모두 std_msgs/String)
  발행  /zone_manager_v2/grant    "<robot_id>:<token>"
  발행  /zone_manager_v2/hold     "<robot_id>:<x>,<y>,<yaw>"
  발행  /zone_manager_v2/resume   "<robot_id>"
  발행  /zone_manager_v2/status   "free" | "occupied_by:<robot_id>:<token>"
  구독  /zone_manager_v2/release  "<robot_id>:<token>"   (선택: 클라이언트 명시 반환)

[해제 조건] 점유자가 (radius + exit_margin) 밖으로 나감 / release 수신(토큰 일치) /
          max_hold_sec 초과 (안전장치)
"""

import math
import threading
import time
import uuid

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.node import Node
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy)
from std_msgs.msg import String


class ZoneManagerNodeV2(Node):
    def __init__(self):
        super().__init__('zone_manager_node_v2')

        # ---------------- 파라미터 ----------------
        self.declare_parameter('robot_ids', ['pinky1', 'pinky2'])
        self.declare_parameter('pose_topic_pattern', '/{robot_id}/amcl_pose')
        self.declare_parameter('zone_center_x', 0.0)
        self.declare_parameter('zone_center_y', 0.0)
        self.declare_parameter('zone_radius', 0.5)
        # 이탈 판정 반경 = radius + exit_margin (경계에서 떨림 방지)
        self.declare_parameter('exit_margin', 0.2)
        # 점유 후 강제 해제 시간(초)
        self.declare_parameter('max_hold_sec', 120.0)
        # 이 시간 동안 pose 가 안 오면 해당 로봇 위치를 무효로 취급
        self.declare_parameter('pose_timeout_sec', 2.0)
        self.declare_parameter('republish_period_sec', 1.0)

        self.robot_ids = list(self.get_parameter('robot_ids').value)
        pattern = str(self.get_parameter('pose_topic_pattern').value)
        self.cx = float(self.get_parameter('zone_center_x').value)
        self.cy = float(self.get_parameter('zone_center_y').value)
        self.radius = float(self.get_parameter('zone_radius').value)
        self.exit_radius = self.radius + float(self.get_parameter('exit_margin').value)
        self.max_hold_sec = float(self.get_parameter('max_hold_sec').value)
        self.pose_timeout = float(self.get_parameter('pose_timeout_sec').value)
        republish = float(self.get_parameter('republish_period_sec').value)

        # 로봇별 정지 좌표: hold_<robot_id> = [x, y, yaw]
        self.hold_points = {}
        for rid in self.robot_ids:
            self.declare_parameter(f'hold_{rid}', [0.0, 0.0, 0.0])
            hp = list(self.get_parameter(f'hold_{rid}').value)
            if len(hp) != 3:
                self.get_logger().error(f'hold_{rid} 는 [x, y, yaw] 여야 합니다: {hp}')
                hp = [0.0, 0.0, 0.0]
            self.hold_points[rid] = [float(v) for v in hp]

        # ---------------- 상태 ----------------
        self._lock = threading.Lock()
        self.poses = {}                # robot_id -> (x, y, 수신시각)
        self.armed = {r: True for r in self.robot_ids}   # 구역 밖에 한 번 나갔는가
        self.occupant = None
        self.token = None
        self.occupant_since = None

        qos = QoSProfile(depth=10,
                         reliability=ReliabilityPolicy.RELIABLE,
                         history=HistoryPolicy.KEEP_LAST)
        # amcl_pose 는 보통 TRANSIENT_LOCAL 로 발행됨 -> 구독 쪽도 맞춰준다
        pose_qos = QoSProfile(depth=1,
                              reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.TRANSIENT_LOCAL,
                              history=HistoryPolicy.KEEP_LAST)

        for rid in self.robot_ids:
            topic = pattern.format(robot_id=rid)
            self.create_subscription(
                PoseWithCovarianceStamped, topic,
                lambda msg, r=rid: self._on_pose(r, msg), pose_qos)
            self.get_logger().info(f'[{rid}] pose 구독: {topic}')

        self.create_subscription(String, '/zone_manager_v2/release', self._on_release, qos)
        self.grant_pub = self.create_publisher(String, '/zone_manager_v2/grant', qos)
        self.hold_pub = self.create_publisher(String, '/zone_manager_v2/hold', qos)
        self.resume_pub = self.create_publisher(String, '/zone_manager_v2/resume', qos)
        self.status_pub = self.create_publisher(String, '/zone_manager_v2/status', qos)

        self.create_timer(0.1, self._tick)
        self.create_timer(republish_period_sec_safe(republish), self._republish)

        self.get_logger().info(
            f'Zone manager v2 시작 (robots={self.robot_ids}, center=({self.cx}, {self.cy}), '
            f'radius={self.radius}, exit_radius={self.exit_radius}, '
            f'max_hold_sec={self.max_hold_sec})')

    # ---------------- 콜백 ----------------

    def _on_pose(self, robot_id, msg: PoseWithCovarianceStamped):
        p = msg.pose.pose.position
        with self._lock:
            self.poses[robot_id] = (p.x, p.y, time.time())

    def _on_release(self, msg: String):
        try:
            robot_id, token = msg.data.split(':', 1)
        except ValueError:
            self.get_logger().warn(f'잘못된 release 형식: {msg.data}')
            return
        with self._lock:
            if self.occupant == robot_id and self.token == token:
                self.get_logger().info(f'[{robot_id}] release 수신 -> 토큰 회수')
                self._release()
            else:
                self.get_logger().warn(
                    f'[{robot_id}] release 무시 (점유자={self.occupant}, 토큰 불일치 또는 비점유)')

    # ---------------- 주기 로직 ----------------

    def _dist(self, robot_id):
        """구역 중심까지 거리. pose 가 없거나 오래됐으면 None."""
        pose = self.poses.get(robot_id)
        if pose is None or time.time() - pose[2] > self.pose_timeout:
            return None
        return math.hypot(pose[0] - self.cx, pose[1] - self.cy)

    def _tick(self):
        with self._lock:
            # 구역 밖으로 한 번 나가야 다시 토큰을 받을 수 있다 (release 직후 재발급 방지)
            for rid in self.robot_ids:
                d = self._dist(rid)
                if d is not None and d > self.exit_radius:
                    self.armed[rid] = True

            if self.occupant is not None:
                d = self._dist(self.occupant)
                if d is not None and d > self.exit_radius:
                    self.get_logger().info(f'[{self.occupant}] 구역 이탈 -> 토큰 회수')
                    self._release()
                    return
                held = time.time() - self.occupant_since
                if held > self.max_hold_sec:
                    self.get_logger().warn(
                        f'[{self.occupant}] {held:.0f}초 점유 -> 강제 해제 '
                        f'(max_hold_sec={self.max_hold_sec})')
                    self._release()
                return

            # 비어 있음: 반경 안에 들어온 로봇 중 중심에 가장 가까운 로봇이 토큰 획득
            candidates = []
            for rid in self.robot_ids:
                d = self._dist(rid)
                if d is not None and d <= self.radius and self.armed[rid]:
                    candidates.append((d, rid))
            if candidates:
                _, winner = min(candidates)
                self._occupy(winner)

    def _republish(self):
        """메시지 유실 대비: 상태를 주기적으로 재발행."""
        with self._lock:
            self._publish_status()
            if self.occupant is not None:
                self._publish_grant(self.occupant, self.token)
                for rid in self.robot_ids:
                    if rid != self.occupant:
                        self._publish_hold(rid)
            else:
                for rid in self.robot_ids:
                    self._publish_resume(rid)

    # ---------------- 상태 전이 (lock 보유 상태에서 호출) ----------------

    def _occupy(self, robot_id):
        self.occupant = robot_id
        self.token = uuid.uuid4().hex[:8]
        self.occupant_since = time.time()
        self.get_logger().info(f'[{robot_id}] 구역 진입 -> 토큰 발행 (token={self.token})')
        self._publish_status()
        self._publish_grant(robot_id, self.token)
        for rid in self.robot_ids:
            if rid != robot_id:
                self._publish_hold(rid)

    def _release(self):
        prev = self.occupant
        self.armed[prev] = False
        self.occupant = None
        self.token = None
        self.occupant_since = None
        self._publish_status()
        for rid in self.robot_ids:
            if rid != prev:
                self._publish_resume(rid)

    # ---------------- 발행 ----------------

    def _publish_grant(self, robot_id, token):
        self.grant_pub.publish(String(data=f'{robot_id}:{token}'))

    def _publish_hold(self, robot_id):
        x, y, yaw = self.hold_points[robot_id]
        self.hold_pub.publish(String(data=f'{robot_id}:{x},{y},{yaw}'))

    def _publish_resume(self, robot_id):
        self.resume_pub.publish(String(data=robot_id))

    def _publish_status(self):
        data = (f'occupied_by:{self.occupant}:{self.token}'
                if self.occupant else 'free')
        self.status_pub.publish(String(data=data))


def republish_period_sec_safe(value):
    return value if value > 0.0 else 1.0


def main():
    rclpy.init()
    node = ZoneManagerNodeV2()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
