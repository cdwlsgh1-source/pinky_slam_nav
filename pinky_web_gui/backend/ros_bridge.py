"""관제 PC(ROS_DOMAIN_ID=50)의 rclpy 노드. 구독과, 로봇별 /{id}/patrol_cmd 발행(publish_cmd) 만 한다.

rclpy 는 별도 스레드에서 spin 하고, 콜백은 값만 뽑아 asyncio 루프에 call_soon_threadsafe 로
넘긴다. 상태 변경은 전부 루프 스레드에서만 일어난다.
rclpy 는 mock 모드에서 설치되어 있지 않아도 되도록 이 모듈 안에서만 import 한다.
"""
import logging
import threading

from .commands import CommandUnavailable
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
        self._cmd_pubs = {}  # 로봇 id -> /{id}/patrol_cmd 퍼블리셔 (시작할 때 한 번만 만든다)
        self._String = None

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
        self._String = String
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

        # 명령 퍼블리셔는 서버 시작 시 한 번만 만든다 (기존 CLI 클라이언트의 1초 대기가 필요 없다).
        # 이름은 설정의 robots 에서만 나온다. 허용 목록 검증은 백엔드(commands.validate)가 이미 끝낸 뒤다.
        for rid in self._cfg.robots:
            self._cmd_pubs[rid] = node.create_publisher(String, f'/{rid}/patrol_cmd', 10)

        self._thread = threading.Thread(target=self._spin, name='rclpy-spin', daemon=True)
        self._thread.start()
        log.info('rclpy 시작: 로봇 %s, zone 토픽 %s', list(self._cfg.robots), self._cfg.zone_status_topic)

    def publish_cmd(self, rid, data):
        """검증이 끝난 명령 문자열을 /{rid}/patrol_cmd 로 발행한다. 구독자(domain_bridge)가 없으면 CommandUnavailable.

        브리지가 꺼져 있으면 발행해도 아무도 받지 않아 명령이 조용히 사라지므로, 먼저 막아서 알린다.
        """
        pub = self._cmd_pubs.get(rid)
        if pub is None:
            raise CommandUnavailable(f'{rid} 의 명령 퍼블리셔가 없습니다')
        if pub.get_subscription_count() == 0:
            raise CommandUnavailable(f'/{rid}/patrol_cmd 를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다)')
        pub.publish(self._String(data=data))

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
