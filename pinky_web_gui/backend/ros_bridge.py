"""관제 PC(ROS_DOMAIN_ID=50)의 rclpy 노드. 구독과, 로봇별 /{id}/patrol_cmd 발행(publish_cmd) 만 한다.

rclpy 는 별도 스레드에서 spin 하고, 콜백은 값만 뽑아 asyncio 루프에 call_soon_threadsafe 로
넘긴다. 상태 변경은 전부 루프 스레드에서만 일어난다.
rclpy 는 mock 모드에서 설치되어 있지 않아도 되도록 이 모듈 안에서만 import 한다.
"""
import logging
import time
import threading

from .commands import CommandUnavailable
from .scan import RateLimiter, TfTree, compact, quat_to_rot
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
        self._Twist = None
        self._LaserScan = None
        self._sensor_qos = None
        self._vel_pubs = {}   # 로봇 id -> /{id}/cmd_vel 퍼블리셔
        self._scan_subs = {}  # 로봇 id -> /{id}/scan 구독 (브라우저가 켰을 때만 존재한다)
        self._tf = {rid: TfTree() for rid in cfg.robots}  # 로봇 id -> tf 변환 모음 (map -> odom -> base -> 센서)
        self._tf_dyn = {}     # 로봇 id -> /{id}/tf 구독 (LiDAR 를 켠 동안만)
        self._TFMessage = None
        self._scan_lock = threading.Lock()

    def _put(self, event):
        # 루프가 닫힌 뒤(종료 중)에 들어오는 콜백은 무시한다
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, event)
        except RuntimeError:
            pass

    def start(self):
        import rclpy
        from geometry_msgs.msg import PoseWithCovarianceStamped, Twist
        from nav_msgs.msg import Odometry
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
        from sensor_msgs.msg import LaserScan
        from std_msgs.msg import Float32, String
        from tf2_msgs.msg import TFMessage

        self._rclpy = rclpy
        self._String = String
        self._Twist = Twist
        self._LaserScan = LaserScan
        self._TFMessage = TFMessage
        self._sensor_qos = qos_profile_sensor_data  # BEST_EFFORT: 센서 발행자(RELIABLE/BEST_EFFORT)와 모두 연결된다
        # rclpy 기본값은 SIGINT/SIGTERM 에서 컨텍스트를 곧바로 shutdown 한다. 그러면 uvicorn 이 종료 절차를 시작하기 전에
        # 발행이 막혀서, 수동 조작/비상정지 중에 서버를 끄면 마지막 0 속도를 보낼 수 없다 (격리 도메인에서 확인).
        # 시그널은 uvicorn 이 처리하게 하고, lifespan 종료 단계에서 0 속도를 보낸 뒤 rclpy 를 정리한다.
        rclpy.init(signal_handler_options=rclpy.SignalHandlerOptions.NO)
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
        # 생존 확인용: bringup 만 켠 로봇은 amcl_pose(Nav2)와 patrol_status(순찰 노드)가 없고 배터리뿐이라 online 이 불안정했다.
        # bringup 이 계속 내는 /odom 을 받아 '살아 있음' 만 반영한다 (BEST_EFFORT 로 받으면 어느 발행자와도 연결된다). 상태 값은 만들지 않는다.
        self._alive_t = {}
        for rid in self._cfg.robots:
            node.create_subscription(Odometry, f'/{rid}/odom', lambda m, rid=rid: self._on_alive(rid), qos_profile_sensor_data)
        node.create_subscription(
            String, self._cfg.zone_status_topic,
            lambda m: self._put(('zone', m.data)), 10)

        # /tf_static 은 시작 때부터 받는다 (센서 장착 위치 같은 고정 변환, 한 번만 발행되므로 TRANSIENT_LOCAL 로 받아야 늦게 붙어도 얻는다).
        # 동적 /tf 는 LiDAR 를 켠 동안만 받는다 (scan_enable).
        static_qos = QoSProfile(depth=100, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        for rid in self._cfg.robots:
            node.create_subscription(TFMessage, f'/{rid}/tf_static', lambda m, rid=rid: self._on_tf(rid, m), static_qos)

        # 명령 퍼블리셔는 서버 시작 시 한 번만 만든다 (기존 CLI 클라이언트의 1초 대기가 필요 없다).
        # 이름은 설정의 robots 에서만 나온다. 허용 목록 검증은 백엔드(commands.validate)가 이미 끝낸 뒤다.
        for rid in self._cfg.robots:
            self._cmd_pubs[rid] = node.create_publisher(String, f'/{rid}/patrol_cmd', 10)
            # 비상정지 0 속도와 수동 조작. 허용 명령은 backend.motion 이 만든 (선속도, 각속도) 뿐이다.
            self._vel_pubs[rid] = node.create_publisher(Twist, f'/{rid}/cmd_vel', 10)

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

    def publish_twist(self, rid, linear, angular):
        """/{rid}/cmd_vel 로 (선속도 m/s, 각속도 rad/s) 를 발행한다. 비상정지는 구독자가 없어도 시도해야 하므로 막지 않는다.

        반환: 구독자(domain_bridge)가 하나라도 있으면 True. 없으면 발행은 하되 False (어디에도 전달되지 않았을 수 있다).
        """
        pub = self._vel_pubs.get(rid)
        if pub is None:
            raise CommandUnavailable(f'{rid} 의 cmd_vel 퍼블리셔가 없습니다')
        msg = self._Twist()
        msg.linear.x = float(linear)
        msg.angular.z = float(angular)
        pub.publish(msg)
        return pub.get_subscription_count() > 0

    def _on_tf(self, rid, msg):
        tree = self._tf[rid]
        for t in msg.transforms:
            q, v = t.transform.rotation, t.transform.translation
            tree.set(t.header.frame_id, t.child_frame_id, quat_to_rot(q.x, q.y, q.z, q.w), (v.x, v.y, v.z))

    def scan_enable(self, rid, on):
        """LiDAR 구독을 켜거나 끈다 (FR4-5). 켤 때만 /{rid}/scan 을 구독하고, 끄면 구독을 해제해서 브리지가 더는 중계하지 않게 한다."""
        if rid not in self._vel_pubs or self._node is None:
            raise CommandUnavailable(f'알 수 없는 로봇이거나 ROS 가 준비되지 않았습니다: {rid}')
        m = self._cfg.motion
        with self._scan_lock:
            if on and rid not in self._scan_subs:
                limiter = RateLimiter(m.scan_max_hz)
                tree = self._tf[rid]
                warned = [None]

                def cb(msg, rid=rid):
                    if not limiter.allow():
                        return
                    frame = msg.header.frame_id
                    # 센서 프레임 -> map 변환. 구하지 못하면 None 이고, 소비하는 쪽이 amcl_pose + 보정 각도로 그린다
                    tf = tree.lookup('map', frame) if frame else None
                    if tf is None and warned[0] != frame:
                        warned[0] = frame
                        log.warning('%s: map -> %r 변환을 tf 에서 구하지 못해 amcl_pose 로 그린다 (알고 있는 프레임: %s)',
                                    rid, frame, tree.frames())
                    elif tf is not None and warned[0] != ('ok', frame):
                        warned[0] = ('ok', frame)
                        log.info('%s: LiDAR 프레임 %r 의 map 변환을 tf 에서 구했다', rid, frame)
                    self._put(('scan', rid, tf, frame) + compact(msg.angle_min, msg.angle_increment, msg.ranges,
                                                                  m.scan_decimate, msg.range_min, msg.range_max))

                # map -> odom -> base 는 계속 바뀌므로 LiDAR 를 켠 동안 /tf 도 받는다
                self._tf_dyn[rid] = self._node.create_subscription(self._TFMessage, f'/{rid}/tf', lambda mm, rid=rid: self._on_tf(rid, mm), 100)

                self._scan_subs[rid] = self._node.create_subscription(self._LaserScan, f'/{rid}/scan', cb, self._sensor_qos)
                log.info('%s LiDAR 구독 시작', rid)
            elif not on and rid in self._scan_subs:
                self._node.destroy_subscription(self._scan_subs.pop(rid))
                dyn = self._tf_dyn.pop(rid, None)
                if dyn is not None:
                    self._node.destroy_subscription(dyn)
                log.info('%s LiDAR 구독 해제', rid)

    def scan_subscribed(self):
        with self._scan_lock:
            return sorted(self._scan_subs)

    def _on_alive(self, rid):
        now = time.monotonic()
        if now - self._alive_t.get(rid, 0.0) >= 0.5:   # odom 은 수십 Hz 라서 초당 2번만 넘긴다
            self._alive_t[rid] = now
            self._put(('alive', rid))

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
