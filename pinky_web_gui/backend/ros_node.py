"""관제 PC(ROS_DOMAIN_ID=50)에서 로봇 상태 토픽을 구독하는 rclpy 노드.

rclpy 는 이 파일 안에서만 import 한다 (함수 안에서 지연 import). --mock 으로 실행할 때
ROS 를 source 하지 않아도 백엔드가 뜨게 하기 위해서다.

스레드 규칙: rclpy 콜백은 별도 스레드에서 돈다. 상태나 WebSocket 을 직접 건드리지 않고
loop.call_soon_threadsafe 로 이벤트 루프 스레드에 넘긴다.
"""
import logging
import threading

from .state import quaternion_to_yaw

log = logging.getLogger('pinky_web_gui.ros')


class RosBridge:
    def __init__(self, config, hub, loop):
        self._config = config
        self._hub = hub
        self._loop = loop
        self._node = None
        self._executor = None
        self._thread = None

    def start(self):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.signals import SignalHandlerOptions
        from rclpy.qos import QoSProfile, ReliabilityPolicy
        from geometry_msgs.msg import PoseWithCovarianceStamped
        from sensor_msgs.msg import BatteryState
        from std_msgs.msg import String

        self._rclpy = rclpy
        # 시그널(Ctrl+C, SIGTERM)은 uvicorn 이 처리한다. rclpy 기본 핸들러가 먼저 컨텍스트를 닫으면
        # spin 스레드가 ExternalShutdownException 으로 죽으면서 종료 때마다 Traceback 이 찍힌다.
        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        self._node = rclpy.create_node('pinky_web_gui_backend')

        # patrol_status / zone status 는 발행 쪽이 RELIABLE 이라 같은 RELIABLE 로 받는다.
        reliable = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE)
        # amcl_pose / battery_state 는 발행 쪽 QoS 를 확인하지 못했다 (확인 필요).
        # BEST_EFFORT 구독은 RELIABLE/BEST_EFFORT 어느 발행자와도 연결되므로 안전하게 이쪽을 쓴다.
        best_effort = QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT)

        post = self._loop.call_soon_threadsafe
        hub = self._hub

        for rid in self._config.robots:
            self._node.create_subscription(
                String, f'/{rid}/patrol_status',
                lambda msg, rid=rid: post(hub.ingest_patrol, rid, msg.data), reliable)
            self._node.create_subscription(
                PoseWithCovarianceStamped, f'/{rid}/amcl_pose',
                lambda msg, rid=rid: post(hub.ingest_pose, rid, *self._pose_xyyaw(msg)), best_effort)
            self._node.create_subscription(
                BatteryState, f'/{rid}/battery_state',
                lambda msg, rid=rid: post(hub.ingest_battery, rid, msg.percentage, msg.voltage), best_effort)

        self._node.create_subscription(
            String, self._config.zone_status_topic,
            lambda msg: post(hub.ingest_zone, msg.data), reliable)

        self._executor = SingleThreadedExecutor()
        self._executor.add_node(self._node)
        self._thread = threading.Thread(target=self._executor.spin, name='rclpy-spin', daemon=True)
        self._thread.start()
        log.info('ROS 구독 시작: 로봇 %s, zone=%s', list(self._config.robots), self._config.zone_status_topic)

    @staticmethod
    def _pose_xyyaw(msg):
        p = msg.pose.pose
        q = p.orientation
        return p.position.x, p.position.y, quaternion_to_yaw(q.x, q.y, q.z, q.w)

    def stop(self):
        if self._executor is not None:
            self._executor.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._node is not None:
            self._node.destroy_node()
        self._rclpy.try_shutdown()
