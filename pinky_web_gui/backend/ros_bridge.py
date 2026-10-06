"""관제 PC(ROS_DOMAIN_ID=50)의 rclpy 노드. 구독 전용이며 발행은 하지 않는다.

rclpy 는 별도 스레드에서 spin 하고, 콜백은 값만 뽑아 asyncio 루프에 call_soon_threadsafe 로
넘긴다. 상태 변경은 전부 루프 스레드에서만 일어난다.
rclpy 는 mock 모드에서 설치되어 있지 않아도 되도록 이 모듈 안에서만 import 한다.
"""
import logging
import threading

from .state import quat_to_yaw

log = logging.getLogger('backend.ros')


class RosBridge:
    def __init__(self, cfg, loop, queue):
        self._cfg = cfg
        self._loop = loop
        self._queue = queue
        self._thread = None
        self._node = None
        self._rclpy = None

    def _put(self, event):
        # 루프가 닫힌 뒤(종료 중)에 들어오는 콜백은 무시한다
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
        except RuntimeError:
            pass

    def start(self):
        import rclpy
        from geometry_msgs.msg import PoseWithCovarianceStamped
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from std_msgs.msg import Float32, String

        self._rclpy = rclpy
        rclpy.init()
        node = Node('pinky_web_gui_backend')
        self._node = node

        # AMCL 은 amcl_pose 를 TRANSIENT_LOCAL 로 발행하고 브리지도 같은 QoS 로 넘긴다 (ros2 topic info -v 로 확인).
        # 기본(VOLATILE) 구독이면 접속 전에 발행된 마지막 위치를 못 받아서 로봇이 움직이기 전까지 pose 가 null 이다.
        pose_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.TRANSIENT_LOCAL)

        for rid in self._cfg.robots:
            node.create_subscription(
                String, f'/{rid}/patrol_status',
                lambda m, rid=rid: self._put(('patrol', rid, m.data)), 10)
            node.create_subscription(
                PoseWithCovarianceStamped, f'/{rid}/amcl_pose',
                lambda m, rid=rid: self._on_pose(rid, m), pose_qos)
            # 로봇은 /battery_state 대신 Float32 두 개를 발행한다 (브리지 YAML 참고)
            node.create_subscription(
                Float32, f'/{rid}/battery/percent',
                lambda m, rid=rid: self._put(('battery', rid, m.data, None)), 10)
            node.create_subscription(
                Float32, f'/{rid}/battery/voltage',
                lambda m, rid=rid: self._put(('battery', rid, None, m.data)), 10)
        node.create_subscription(
            String, self._cfg.zone_status_topic,
            lambda m: self._put(('zone', m.data)), 10)

        self._thread = threading.Thread(target=self._spin, name='rclpy-spin', daemon=True)
        self._thread.start()
        log.info('rclpy 시작: 로봇 %s, zone 토픽 %s', list(self._cfg.robots), self._cfg.zone_status_topic)

    def _on_pose(self, rid, msg):
        p = msg.pose.pose
        q = p.orientation
        self._put(('pose', rid, p.position.x, p.position.y, quat_to_yaw(q.x, q.y, q.z, q.w)))

    def _spin(self):
        try:
            self._rclpy.spin(self._node)
        except Exception:  # 종료 시 shutdown 으로 인한 예외는 정상
            if self._rclpy.ok():
                log.exception('rclpy spin 오류')

    def stop(self):
        if self._rclpy is None:
            return
        try:
            self._rclpy.shutdown()
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._node:
            try:
                self._node.destroy_node()
            except Exception:
                pass
