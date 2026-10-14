"""
Pinky2 순찰 노드

[v3 변경점] 위험구역 진입을 "대기열 슬롯" 방식으로 처리 (zone_manager_node_v3 / zone_gate_client_v3)
  - 진입 요청을 먼저 보내고, 점유 중이면 매니저가 알려 준 슬롯에서 줄 서서 대기 (상태 QUEUEING)
  - goto 경로와 start 경로가 같은 _queue_and_enter 를 사용
  - 로봇 코드에는 대기 좌표가 없고 진입 앵커(ZONE_ENTRY)와 반납 출구(ZONE_EXIT)만 유지

[지원 명령]  (토픽: patrol_cmd)
  start                : WAYPOINTS 전체 순회
  goto:<P>[,<P>,...]   : 지정한 지점들을 순서대로 방문 -> 홈(P1) 복귀
                         예) goto:P2          (1개 지점)
                             goto:P2,P3,P6    (여러 지점 경로)
                         각 지점 도착 시 GOTO_WAIT_SEC초 대기(노랑 LED)
  stop                 : 현재 작업 즉시 중단

[상태 발행]  (토픽: patrol_status, JSON)
  IDLE / STARTING / MOVING / QUEUEING / WAITING_ZONE / ARRIVED /
  LEAVING_ZONE / RETURNING / DONE / STOPPED / FAILED / RETRY
  (goto 경로 중에는 waypoint = 경로 안에서의 순번, detail = 지점 이름)

[파일 구성]
  1. IMPORT
  2. 좌표 / 경로 상수
  3. 위험 구역(ZONE) 상수
  4. goto 상수
  5. LED 상수
  6. 초기화 (__init__)
  7. LED 제어
  8. 통신 (상태 발행 / 명령 수신)
  9. 좌표 변환 / 위치 저장 / 초기 위치 보정
  10. 주행 (Nav2 이동 헬퍼)
  11. 위험 구역 (zone mutex + 대기열 헬퍼)
  12. 작업: goto 경로 순회 후 복귀
  13. 작업: 전체 순찰
  14. main
"""

# ==============================================================================
# 1. IMPORT
# ==============================================================================
import rclpy
from rclpy.signals import SignalHandlerOptions
from rclpy.node import Node
from nav2_simple_commander.robot_navigator import BasicNavigator, TaskResult
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist
from std_msgs.msg import String
from pinky_interfaces.srv import SetLed
import json, math, os, time, threading, subprocess

# zone_traffic_control 패키지가 설치되어 있어야 합니다.
from zone_traffic_control.zone_gate_client_v3 import (ZoneGateClient,
                                                     choose_entry_target)


class PinkyPatrolNode(Node):
    """
    [설계 핵심]
    - Nav2 액션은 전부 로컬(BasicNavigator)로 처리 -> 도메인 브릿지 불필요
    - 관제 PC와는 patrol_cmd(수신) / patrol_status(송신) String 토픽만 사용
    - 콜백은 즉시 리턴하고, 실제 작업은 별도 스레드에서 수행
    - waypoints는 로봇마다 다르므로 로봇별 파일에서 따로 관리
    """

    # ==========================================================================
    # 2. 좌표 / 경로 상수
    # ==========================================================================

    # 재시작해도 마지막 위치를 기억하기 위한 저장 파일 경로
    POSE_FILE = os.path.expanduser('~/.pinky_last_pose.json')

    # 좌표: (x, y) 또는 (x, y, yaw[rad])
    POINTS = {
        "P1": (0.0,  0.0),   # 원점 부근 (시작점 / 홈)
        "P2": (0.65,  0.15),   # 원점과 우측 구간 사이 중간 지점
        "P3": (1.55, -0.40),   # 우측 아래 끝
        "P4": (1.00, -0.40),   # 중앙-우측 아래
        "P5": (1.00,  0.10),   # 중앙-우측 위
        "P6": (1.55,  0.06),   # 우측 위 끝
        "P7": (0.00, -0.60),   # 원점 아래쪽

        # [미사용] 기존 위험 구역 문(RED LINE) 좌표. 대기열(v3)은 앵커 진입/출구 반납을 쓰므로 주석 처리
        # "ZONE_ENTRY":  (0.60, -0.50, 0),
        # "ZONE_EXIT": (0.60, -0.50, 3.14),
        # "ZONE_ENTRY":  (0.55, -0.60, 0),
        # "ZONE_EXIT": (0.55, -0.60, 3.14),

        # 위험 구역 진입/반납 좌표 (두 로봇 공통)
        #   ZONE_ENTRY: 대기열 앵커(슬롯 0). zone_params_v3.yaml 의 queue_anchor 와 같아야 함 (테스트가 검사)
        #   ZONE_EXIT : 구역을 나온 뒤 락(토큰)을 반납하는 출구. yaw 3.14 = 뒤 방향(180도)
        "ZONE_ENTRY": (1.0, -0.7, 0.0),
        "ZONE_EXIT":  (0.5, -0.5, 3.14),
        # stop 으로 구역 밖에 나와 락을 반납하고 서는 위치 (로봇마다 고정). ZONE_EXIT 에서 +x 방향(입구 줄 쪽)으로
        # 0.30 m 간격: pinky1 = ZONE_EXIT, pinky2 = ZONE_EXIT + 0.30. 서로 통신하지 않고 겹치지 않게 하는 고정 오프셋
        "ZONE_STOP_EXIT": (0.8, -0.5, 3.14),
    }

    # start 명령의 순찰 경로: P2 -> ZONE_ENTRY -> P3 -> P6 -> ZONE_EXIT -> P1
    WAYPOINTS = [
        POINTS["ZONE_ENTRY"],   # 0: 앵커 (요청 -> 슬롯 대기 -> 허가 -> 진입. _queue_and_enter)
        POINTS["P3"],       # 1: 우측 아래 끝 (위험 구역 안)
        POINTS["P6"],       # 2: 우측 위 끝   (위험 구역 안)
        POINTS["ZONE_EXIT"],  # 3: 출구 (도착 시 락 반납)
        POINTS["P7"],       # 4: 시작점으로 복귀
    ]

    # ==========================================================================
    # 3. 위험 구역(ZONE) 상수
    # ==========================================================================
    # [순찰(start)용] WAYPOINTS 리스트의 "순번(index)"이며 좌표 값이 아닙니다.
    #   ZONE_ENTRY_INDEX: 이 waypoint 도착 직후, 다음으로 출발하기 전에 진입 허가 요청
    #   ZONE_EXIT_INDEX : 이 waypoint 도착 시 구역을 벗어난 것으로 보고 락 반납
    ZONE_ENTRY_INDEX = 0   # WAYPOINTS[0] = ZONE_ENTRY  (v2 는 1 로 어긋나 있었음 -> v3 에서 정정)
    ZONE_EXIT_INDEX = 3    # WAYPOINTS[3] = ZONE_EXIT (v2 는 4 로 어긋나 있었음 -> v3 에서 정정)

    # [goto용] POINTS의 키 이름 기준
    ZONE_ENTRY_NAME = 'ZONE_ENTRY'                       # 허가 요청 지점
    STOP_EXIT_NAME = 'ZONE_STOP_EXIT'                   # stop 퇴장 후 락 반납 지점
    ZONE_EXIT_NAME = 'ZONE_EXIT'                       # 락 반납 지점
    ZONE_POINTS = {'P3', 'P4', 'P5', 'P6'}           # 위험 구역 안 포인트

    # ==========================================================================
    # 4. goto 상수
    # ==========================================================================
    HOME_NAME = 'P7'                                  # 경로 완료 후 복귀할 초기 위치
    GOTO_WAIT_SEC = 10.0                              # 지점 도착 후 대기 시간(초)
    WAIT_EVERY_POINT = True                           # True: 모든 지점에서 대기 / False: 마지막 지점에서만
    NAV_RETRIES = 3                                   # 진입 절차 중 한 목표에 대한 이동 최대 시도 횟수
    GOTO_ALLOWED = {'P1', 'P2', 'P7'} | ZONE_POINTS   # ZONE_*는 직접 goto 금지

    # ==========================================================================
    # 5. LED 상수
    # ==========================================================================
    # 대기(IDLE): 초록 깜박임 / 주행: 초록 고정 점등(깜박임 없음)
    IDLE_BLINK_PERIOD = 1.0

    # ==========================================================================
    # 6. 초기화
    # ==========================================================================
    def __init__(self):
        super().__init__('pinky_patrol_node')

        # 실행 시 --ros-args -p robot_id:=pinky2
        # zone_manager_node의 robot_ids 파라미터 값과 정확히 같아야 함
        self.declare_parameter('robot_id', 'pinky2')
        self.robot_id = self.get_parameter('robot_id').value

        # Nav2 액션 클라이언트 + 퍼블리셔 생성 헬퍼 역할 겸용
        self.navigator = BasicNavigator()

        # 위험 구역 허가 클라이언트 (navigator를 넘겨 executor 충돌 방지)
        self.gate = ZoneGateClient(self.navigator, robot_id=self.robot_id)

        # 통신: 상태 송신 / 명령 수신
        self.status_pub = self.create_publisher(String, 'patrol_status', 10)
        self.create_subscription(String, 'patrol_cmd', self._cmd_cb, 10)

        # 스레드 간 공유 상태
        self._running = False         # 작업(순찰/goto) 수행 중 여부
        self._stop_requested = False  # stop / emergency_stop 명령 수신 여부
        self._emergency_requested = False  # emergency_stop 수신 여부 (True 면 후퇴/락 반납 없이 현재 동작 유지)
        self._thread = None           # 작업 스레드 핸들
        self._pose_initialized = False  # 초기 위치 보정을 이미 했는지 (노드 실행 후 1회만)

        # LED
        self._led_proc = None         # 직접 띄운 led_server 프로세스
        self._blink_stop = None       # 깜박임 중단용 Event
        self._blink_thread = None
        self.led_cli = self.create_client(SetLed, 'set_led')
        self.set_idle_led()           # 시작 시 대기 LED

        self.publish_status('IDLE', -1)

    # ==========================================================================
    # 7. LED 제어
    # ==========================================================================
    # LED 상태표
    #   명령 대기       : 초록 깜박임        set_idle_led()
    #   주행 중         : 초록 고정 점등     set_drive_led()
    #   허가 대기       : 빨강 깜박임        start_blink(255, 0, 0)
    #   도착 후 대기    : 노랑 깜박임        start_blink(255, 255, 0)
    # 색을 바꿀 때는 start_blink()가 이전 깜박임을 먼저 멈추므로 별도 stop 불필요.

    def set_led(self, command, r=0, g=0, b=0):
        """LED 서비스 호출. led_server가 없으면 직접 실행 (실패해도 작업은 계속)."""
        if not self.led_cli.wait_for_service(timeout_sec=1.0):
            if self._led_proc is None:
                self.get_logger().info('led_server 자동 실행')
                # 깜박임마다 찍히는 INFO 로그("Filled/Cleared all LEDs")를 숨기기 위해
                # led_server 로그 레벨을 WARN으로 올려서 실행 (경고/에러만 출력)
                # start_new_session=True: 별도 세션으로 실행해 터미널 Ctrl+C(SIGINT)가
                # led_server에 직접 전달되지 않게 함 (종료는 main()의 finally에서 처리)
                self._led_proc = subprocess.Popen(
                    ['ros2', 'run', 'pinky_led', 'led_server',
                     '--ros-args', '--log-level', 'warn'],
                    start_new_session=True)
            if not self.led_cli.wait_for_service(timeout_sec=10.0):
                self.get_logger().warn('/set_led 서비스 없음 - LED 설정 생략')
                return
        req = SetLed.Request()
        req.command, req.r, req.g, req.b = command, r, g, b
        self.led_cli.call_async(req)

    def start_blink(self, r, g, b, period=1.0):
        """별도 스레드에서 (r,g,b) <-> 소등 반복. 이전 깜박임은 자동 중단."""
        self.stop_blink()
        stop = threading.Event()

        def loop():
            on = True
            while not stop.is_set():
                if on:
                    self.set_led('fill', r, g, b)   # 켜짐
                else:
                    self.set_led('clear')           # 꺼짐
                on = not on
                stop.wait(period)

        self._blink_stop = stop
        self._blink_thread = threading.Thread(target=loop, daemon=True)
        self._blink_thread.start()

    def stop_blink(self):
        """깜박임 스레드 정지."""
        if self._blink_stop is not None:
            self._blink_stop.set()
            self._blink_thread.join(timeout=3.0)
            self._blink_stop = None
            self._blink_thread = None

    def set_idle_led(self):
        """명령 대기: 초록 느린 깜박임."""
        self.start_blink(0, 255, 0, period=self.IDLE_BLINK_PERIOD)

    def set_drive_led(self):
        """주행 중: 초록 고정 점등. 깜박임 스레드를 먼저 멈춰야 덮어써지지 않음."""
        self.stop_blink()
        self.set_led('fill', 0, 255, 0)

    def clear_led(self):
        """노드 종료 시 LED 끄기 (서비스 응답까지 잠깐 spin)."""
        self.stop_blink()
        if not self.led_cli.service_is_ready():
            return
        req = SetLed.Request()
        req.command = 'clear'
        future = self.led_cli.call_async(req)
        rclpy.spin_until_future_complete(self, future, timeout_sec=2.0)

    # ==========================================================================
    # 8. 통신 (상태 발행 / 명령 수신)
    # ==========================================================================
    def publish_status(self, state, wp_index, detail=''):
        """상태를 JSON 문자열로 직렬화해 patrol_status로 발행."""
        payload = json.dumps({'state': state, 'waypoint': wp_index,
                              'detail': detail, 'time': time.time()})
        self.status_pub.publish(String(data=payload))

    def _cmd_cb(self, msg):
        """
        명령 수신 콜백. 오래 걸리는 작업은 하지 않고 스레드에 위임한 뒤 즉시 리턴.
        (콜백이 막히면 stop 명령을 받을 수 없음)
        """
        raw = msg.data.strip()   # 원본: goto 포인트 이름 추출용
        cmd = raw.lower()        # 소문자: 명령 판별용

        # --- start: 전체 순찰 ---
        if cmd == 'start':
            if self._running:
                self.get_logger().warn('이미 순찰 중입니다. 명령 무시.')
                return
            self._stop_requested = False
            self._emergency_requested = False
            self._running = True   # 스레드 시작 전에 세팅 (연타 시 중복 실행 방지)
            self._thread = threading.Thread(target=self._run_patrol, daemon=True)
            self._thread.start()

        # --- goto:<P>[,<P>,...]: 지점들을 순서대로 방문 -> 홈 복귀 ---
        elif cmd.startswith('goto:'):
            # 'goto:p2, p3 ,P6' -> ['P2', 'P3', 'P6']  (쉼표/공백 모두 구분자)
            names = raw.split(':', 1)[1].replace(',', ' ').upper().split()

            if not names:
                self.get_logger().warn('goto 포인트가 비어 있습니다.')
                self.publish_status('FAILED', -1, 'no point given')
                return
            unknown = [n for n in names if n not in self.POINTS]
            if unknown:
                self.get_logger().warn(f'알 수 없는 포인트: {unknown}')
                self.publish_status('FAILED', -1, f'unknown point: {unknown}')
                return
            denied = [n for n in names if n not in self.GOTO_ALLOWED]
            if denied:
                self.get_logger().warn(f'goto 불가 포인트: {denied}')
                self.publish_status('FAILED', -1, f'not allowed: {denied}')
                return
            if self._running:
                self.get_logger().warn('작업 중입니다. 명령 무시.')
                return

            self._stop_requested = False
            self._emergency_requested = False
            self._running = True
            self._thread = threading.Thread(
                target=self._run_route, args=(names,), daemon=True)
            self._thread.start()

        # --- stop / emergency_stop: 현재 작업 중단 ---
        #   stop           : 멈춘 뒤, 구역 안에서 락을 들고 있으면 ZONE_STOP_EXIT 로 나와 락을 반납한다
        #   emergency_stop : 그 자리 정지 + 락 유지 (락은 zone_manager 의 max_hold_sec 뒤에 자동 해제)
        elif cmd in ('stop', 'emergency_stop'):
            if not self._running:
                self.get_logger().warn('순찰 중이 아닙니다. 명령 무시.')
                return
            # 콜백(executor 스레드)에서 navigator.cancelTask() 를 부르면 작업 스레드가 같은 executor 를 spin 중이라
            # 'Executor is already spinning' 으로 노드가 죽는다. 플래그만 세우고 취소는 작업 스레드가 한다
            # (_navigate / _queue_and_enter / 순찰 루프가 매 0.1초 _stop_requested 를 확인해 goal 을 취소함).
            if cmd == 'emergency_stop':
                self._emergency_requested = True
            self._stop_requested = True

        else:
            self.get_logger().warn(f'알 수 없는 명령: {cmd}')

    # ==========================================================================
    # 9. 좌표 변환 / 위치 저장 / 초기 위치 보정
    # ==========================================================================
    def quat_to_yaw(self, q):
        """쿼터니언 -> yaw(rad). atan2로 -pi~pi 전 구간을 정확히 복원."""
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        return math.atan2(siny_cosp, cosy_cosp)

    def make_pose(self, x, y, yaw=0.0):
        """(x, y, yaw) -> PoseStamped. 2D 로봇이라 z축 회전만 사용."""
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp = self.navigator.get_clock().now().to_msg()
        pose.pose.position.x = x
        pose.pose.position.y = y
        pose.pose.orientation.z = math.sin(yaw / 2.0)
        pose.pose.orientation.w = math.cos(yaw / 2.0)
        return pose

    def get_current_pose(self):
        """Nav2 feedback에서 현재 위치 추출. 이동 중이 아니면 None."""
        feedback = self.navigator.getFeedback()
        if feedback is None:
            return None
        cp = feedback.current_pose.pose
        return (cp.position.x, cp.position.y, self.quat_to_yaw(cp.orientation))

    def save_last_pose(self, x, y, yaw=0.0):
        """다음 실행 시 이어서 시작할 수 있도록 마지막 위치를 파일에 저장."""
        with open(self.POSE_FILE, 'w') as f:
            json.dump({'x': x, 'y': y, 'yaw': yaw}, f)

    def load_last_pose(self):
        """저장된 마지막 위치 로드. 없으면 None."""
        if os.path.exists(self.POSE_FILE):
            with open(self.POSE_FILE) as f:
                data = json.load(f)
                data.setdefault('yaw', 0.0)   # 구버전 파일 호환
                return data
        return None

    def publish_initial_pose(self, x, y, yaw):
        """
        AMCL 초기 위치 발행. 구독자가 붙을 때까지 대기해야 메시지 유실이 없음
        (initialpose는 1회성이라 유실되면 위치 추정이 틀어짐).
        """
        pub = self.navigator.create_publisher(PoseWithCovarianceStamped, 'initialpose', 10)
        deadline = time.time() + 5.0
        while pub.get_subscription_count() == 0 and time.time() < deadline:
            time.sleep(0.1)

        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = 'map'
        msg.header.stamp = self.navigator.get_clock().now().to_msg()
        msg.pose.pose.position.x = x
        msg.pose.pose.position.y = y
        msg.pose.pose.orientation.z = math.sin(yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(yaw / 2.0)
        # covariance: 6x6 행렬을 1차원으로 편 배열. 대각 성분만 채움
        # 위치를 잘 못 잡으면 값을 키워볼 것 (x,y: 0.25 -> 0.5 / yaw: 0.3 -> 0.5~1.0)
        msg.pose.covariance[0] = 0.25    # x 분산
        msg.pose.covariance[7] = 0.25    # y 분산
        msg.pose.covariance[35] = 0.3    # yaw 분산 (6*5+5)
        pub.publish(msg)

    def spin_in_place(self, duration=12.6, angular_speed=0.5):
        """제자리 회전으로 AMCL 방향 보정. 위치를 잘 못 잡으면 duration을 늘려볼 것."""
        cmd_pub = self.navigator.create_publisher(Twist, 'cmd_vel', 10)
        deadline = time.time() + 5.0
        while cmd_pub.get_subscription_count() == 0 and time.time() < deadline:
            time.sleep(0.1)

        twist = Twist()
        twist.angular.z = angular_speed
        end_time = time.time() + duration
        while time.time() < end_time:
            cmd_pub.publish(twist)
            time.sleep(0.1)
        cmd_pub.publish(Twist())   # 정지 명령으로 마무리
        time.sleep(0.5)

    def _init_pose_once(self):
        """
        노드 실행 후 첫 작업(start/goto)에서만 초기 위치 보정을 수행.
        저장된 마지막 위치를 AMCL에 알려주고 제자리 회전으로 방향을 잡는다.
        이미 했거나 저장된 위치가 없으면 아무것도 하지 않음.
        """
        if self._pose_initialized:
            return
        self._pose_initialized = True   # 실패/없음이어도 다시 시도하지 않음

        last = self.load_last_pose()
        if last is None:
            self.get_logger().info('저장된 위치 없음 - 초기 위치 보정 생략')
            return
        self.publish_initial_pose(last['x'], last['y'], last['yaw'])
        self.get_logger().info('초기 방향 보정을 위해 제자리 회전을 시작합니다...')
        self.spin_in_place(duration=4.0, angular_speed=0.5)
        self.get_logger().info('초기 방향 보정 완료.')

    # ==========================================================================
    # 10. 주행 (Nav2 이동 헬퍼)
    # ==========================================================================
    def _navigate(self, wp, retries=3):
        """
        한 지점으로 이동. 성공 True / 실패·중단 False.
        이동 중에는 초록 고정 점등. 실패 시 retries회까지 재시도.
        """
        pose = self.make_pose(*wp)
        self.set_drive_led()
        for attempt in range(retries):
            self.navigator.goToPose(pose)
            # goToPose는 비동기이므로 완료될 때까지 폴링
            while not self.navigator.isTaskComplete():
                if self._stop_requested:
                    self.navigator.cancelTask()
                    break
                time.sleep(0.1)
            if self._stop_requested:
                return False

            result = self.navigator.getResult()

            # 마지막 위치 저장 (다음 노드 실행 시 초기 위치 보정에 사용)
            current = self.get_current_pose()
            if current is None and result == TaskResult.SUCCEEDED:
                current = (wp[0], wp[1], wp[2] if len(wp) > 2 else 0.0)
            if current is not None:
                self.save_last_pose(*current)

            if result == TaskResult.SUCCEEDED:
                return True
            self.get_logger().warn(f'이동 실패({result}), 재시도 {attempt+1}/{retries}')
        return False

    def _wait_interruptible(self, sec):
        """sec초 대기. 도중에 stop이 오면 즉시 False."""
        end = time.time() + sec
        while time.time() < end:
            if self._stop_requested:
                return False
            time.sleep(0.1)
        return True

    # ==========================================================================
    # 11. 위험 구역 (zone mutex 헬퍼)
    # ==========================================================================
    def _end_state(self):
        """이동이 끝난 이유: stop 요청이면 STOPPED, 아니면 FAILED."""
        return 'STOPPED' if self._stop_requested else 'FAILED'

    def _queue_and_enter(self, idx, name):
        """
        위험 구역 진입 절차 전체 (goto 경로와 start 경로가 함께 쓴다).
        요청을 "먼저" 보내고, 매니저 응답(허가 / 대기 슬롯)에 따라 목표를 계속 바꿔 가며 이동한다.

          1) request_entry 발송 후 일단 문(ZONE_ENTRY_NAME)으로 출발 (빈 구역이면 v2처럼 곧바로 진행)
          2) 점유 중이라 슬롯(queue_slot)이 오면 -> 문 대신 그 슬롯 좌표로 이동하고 정지 (QUEUEING)
          3) 슬롯이 바뀌면(앞 로봇 이탈/취소) -> 현재 goal 취소 후 새 슬롯으로 재이동
          4) 허가(grant)가 오면 -> 진행 중인 goal 만 취소하고 즉시 반환 (슬롯/앵커를 거치지 않고 호출자가 목적지로 직행)
          5) 대기 중 재전송(1초)으로 매니저에게 "아직 기다린다"고 알린다 (끊기면 5초 뒤 큐에서 제거됨)
        로봇 코드에는 대기 좌표가 없다. 앵커 좌표(ZONE_ENTRY)만 POINTS 에 있고 슬롯은 항상 매니저가 준다.

        True  : 허가를 받음 (락 보유). 앵커에 도착하지 않았어도 반환
        False : stop / 이동 최종 실패 / 대기열 가득 (락 없음. 허가를 이미 받았다면 반납함)
        """
        door = self.POINTS[self.ZONE_ENTRY_NAME]
        gate = self.gate
        gate.reset()
        gate.send_request()                     # 슬롯 이동 "전에" 먼저 요청 (순번 확보)

        target = None       # 현재 목표 (kind, pose). kind: 'door' | 'slot'
        moving = False      # Nav2 goal 이 진행 중인가
        arrived = False     # 현재 목표에 도착했는가
        fails = 0           # 현재 목표의 연속 이동 실패 횟수
        phase = None        # 마지막으로 발행한 상태 (같은 상태를 반복 발행하지 않기 위함)

        while rclpy.ok():
            # --- stop: 진행 중인 goal 취소 + 매니저에 대기 포기/락 반납 ---
            if self._stop_requested:
                if moving:
                    self._cancel_goal()
                self._abort_entry(idx, name, 'STOPPED')
                return False

            # 허가 전에는 1초마다 같은 토큰으로 재전송 (stale 제거 방지)
            if not gate.granted():
                gate.resend_if_due()

            # --- 허가(토큰)를 받았으면 대기열/앵커로 가지 않는다: 진행 중인 goal 만 취소하고 바로 반환 ---
            #     (호출한 쪽이 곧바로 다음 목표(P3 등)로 출발하므로 슬롯·앵커를 거치지 않고 직행)
            if gate.granted():
                if moving:
                    self._cancel_goal()
                self.stop_blink()
                self.set_drive_led()
                return True

            kind, pose = choose_entry_target(gate.granted(), gate.current_slot, door)
            if kind == 'full':
                self.get_logger().warn(f'[{self.robot_id}] 대기열이 가득 차서 진입을 포기합니다')
                if moving:
                    self._cancel_goal()
                self._abort_entry(idx, name, 'FAILED', 'queue_full')
                return False

            # --- 목표가 바뀌었으면 현재 goal 을 취소하고 새 목표로 출발 ---
            if (kind, pose) != target:
                if moving:
                    self._cancel_goal()
                target, arrived, fails = (kind, pose), False, 0
                self._start_goal(pose)
                moving = True
            # --- 이동 중: goal 완료 여부 확인 (isTaskComplete 가 navigator 를 spin 해서 콜백도 처리됨) ---
            elif moving:
                if self.navigator.isTaskComplete():
                    moving = False
                    if self._stop_requested:        # stop 으로 취소된 것이면 실패로 세지 않음
                        continue
                    result = self.navigator.getResult()
                    self._record_pose(pose, result)
                    if result == TaskResult.SUCCEEDED:
                        arrived = True
                    else:
                        fails += 1
                        self.get_logger().warn(
                            f'이동 실패({result}), 재시도 {fails}/{self.NAV_RETRIES}')
                        if fails >= self.NAV_RETRIES:
                            self._abort_entry(idx, name, 'FAILED', target[0])
                            return False
                        self._start_goal(pose)      # 같은 목표로 재시도
                        moving = True
            # --- 도착해서 대기 중: navigator 를 직접 spin 해야 grant/queue_slot 콜백이 처리됨 ---
            else:
                rclpy.spin_once(self.navigator, timeout_sec=0.1)

            # --- 상태 / LED (바뀔 때만) ---
            if kind == 'slot':
                new_phase = 'QUEUEING'
            elif arrived:
                new_phase = 'WAITING_ZONE'          # 문 앞인데 아직 허가가 없음
            else:
                new_phase = 'MOVING'
            if new_phase != phase:
                phase = new_phase
                if phase == 'MOVING':
                    self.set_drive_led()
                    self.publish_status('MOVING', idx, self.ZONE_ENTRY_NAME)
                else:
                    self.start_blink(255, 0, 0)     # 대기: 빨강 깜박임
                    detail = f'slot{gate.current_slot[0]}' if kind == 'slot' else name
                    self.publish_status(phase, idx, detail)

        return False    # rclpy 종료

    def _start_goal(self, pose):
        """Nav2 에 pose(x, y, yaw) goal 을 보낸다 (비동기, 완료는 isTaskComplete 로 확인)."""
        self.navigator.goToPose(self.make_pose(*pose))

    def _cancel_goal(self):
        """진행 중인 goal 을 취소하고 취소가 반영될 때까지 잠깐 기다린다 (새 goal 과 겹침 방지)."""
        self.navigator.cancelTask()
        end = time.time() + 2.0
        while not self.navigator.isTaskComplete() and time.time() < end:
            pass    # isTaskComplete 가 0.1초씩 spin 하므로 busy loop 아님

    def _record_pose(self, wp, result):
        """이동 결과와 무관하게 마지막 위치를 저장 (_navigate 와 같은 규칙)."""
        current = self.get_current_pose()
        if current is None and result == TaskResult.SUCCEEDED:
            current = (wp[0], wp[1], wp[2] if len(wp) > 2 else 0.0)
        if current is not None:
            self.save_last_pose(*current)

    def _abort_entry(self, idx, name, state, detail=None):
        """
        진입 절차 중단 정리. 허가를 이미 받았다면 구역 밖이므로 락을 반납하고(notify_exit),
        아직 대기 중이면 대기열에서 빠진다(cancel_entry). 상태를 발행하고 깜박임을 멈춘다.
        """
        if self.gate.granted():
            self.gate.notify_exit()
        else:
            self.gate.cancel_entry()
        self.stop_blink()
        self.publish_status(state, idx, detail or name)

    def _retreat_after_stop(self):
        """
        stop 으로 멈췄을 때 구역 안에서 락을 들고 있다면: ZONE_STOP_EXIT 로 나가 락을 반납한다.
        _navigate 는 _stop_requested 면 곧바로 취소하므로 여기서는 별도 이동 루프를 쓴다.
        emergency_stop 이면(이동 중에 들어와도) 즉시 멈추고 락을 유지한다 (max_hold_sec 뒤 자동 해제).
        True : 도착해서 락을 반납함 / False : emergency 이거나 도달 실패 (락 유지)
        """
        if self._emergency_requested:
            self.get_logger().warn('emergency_stop: 후퇴/락 반납 없이 현재 위치 유지 (max_hold_sec 뒤 락 자동 해제)')
            return False
        wp = self.POINTS[self.STOP_EXIT_NAME]
        self.publish_status('RETREATING', -1, self.STOP_EXIT_NAME)
        self.set_drive_led()
        for attempt in range(self.NAV_RETRIES):
            self.navigator.goToPose(self.make_pose(*wp))
            while not self.navigator.isTaskComplete():
                if self._emergency_requested:           # 후퇴 중 emergency_stop: 즉시 정지, 락 유지
                    self.navigator.cancelTask()
                    self.get_logger().warn('emergency_stop: 후퇴 중단, 락 유지')
                    return False
                time.sleep(0.1)
            if self._emergency_requested:
                return False
            if self.navigator.getResult() == TaskResult.SUCCEEDED:
                self.gate.notify_exit()
                self.get_logger().info(f'stop: {self.STOP_EXIT_NAME} 도착, 락 반납 완료')
                return True
            self.get_logger().warn(f'후퇴 이동 실패, 재시도 {attempt+1}/{self.NAV_RETRIES}')
        self.get_logger().warn('후퇴 실패 - 락 유지 (max_hold_sec 타임아웃 대기)')
        return False

    def _leave_zone(self):
        """
        이탈 문(ZONE_EXIT_NAME)으로 이동 -> 락 반납.
        True  : 도착해서 락을 반납함
        False : 도달 실패 (락 유지, max_hold_sec 타임아웃 대기)
        """
        door = self.POINTS[self.ZONE_EXIT_NAME]

        arrived = self._navigate(door)                              # 실제 이동 명령
        if not arrived:
            self.get_logger().warn(
                f'{self.ZONE_EXIT_NAME} 도달 실패 - 락 유지 (max_hold_sec 타임아웃 대기)')
            return False

        self.gate.notify_exit()                                     # 도착했을 때만 락 반납
        self.get_logger().info('위험 구역 이탈, 락 반납 완료')
        return True

    # ==========================================================================
    # 12. 작업: goto 경로 순회 후 복귀
    # ==========================================================================
    def _run_route(self, names):
        """
        [별도 스레드] names 리스트의 지점을 순서대로 방문한 뒤 홈으로 복귀.

        구역 처리 규칙 (인접한 두 지점의 구역 여부로 판단):
          구역 밖 -> 구역 안 : ZONE_ENTRY 경유 + 진입 허가 대기 (락 획득)
          구역 안 -> 구역 안 : 락 유지한 채 바로 이동 (ZONE_ENTRY/EXIT 경유 없음)
          구역 안 -> 구역 밖 : ZONE_EXIT 경유 + 락 반납
          구역 밖 -> 구역 밖 : 바로 이동
        마지막 지점이 구역 안이면 홈 복귀 전에 ZONE_EXIT에서 락을 반납.

        예) P2,P3,P6,P7
          P2 -> (ZONE_ENTRY, 허가) -> P3 -> P6 -> (ZONE_EXIT, 반납) -> P7 -> 홈(P1)
        """
        in_zone = False   # True = 허가(락) 보유 중
        last = len(names) - 1
        try:
            self.publish_status('STARTING', -1, ','.join(names))
            self._init_pose_once()   # 최초 1회만 초기 위치 보정
            self.navigator.waitUntilNav2Active()

            for idx, name in enumerate(names):
                if self._stop_requested:
                    self.publish_status('STOPPED', idx, name)
                    return

                to_zone = name in self.ZONE_POINTS

                # --- A) 구역 밖 -> 안: 요청 -> (필요 시 슬롯 대기) -> 허가 -> ZONE_ENTRY 진입 ---
                if to_zone and not in_zone:                 # 다음 지점이 구역 안이고, 아직 락이 없으면
                    if not self._queue_and_enter(idx, name):  # 허가를 못 받았으면 (이동 실패 / stop / 대기열 가득)
                        return                              #   -> 작업 종료
                    in_zone = True                          # 허가받음 -> "락 보유 중" 표시

                # --- B) 구역 안 -> 밖: ZONE_EXIT 도착 후 락 반납 ---
                elif not to_zone and in_zone:               # 다음 지점이 구역 밖이고, 락을 가지고 있으면
                    self.publish_status('LEAVING_ZONE', idx, self.ZONE_EXIT_NAME)
                    if not self._leave_zone():              # ZONE_EXIT 도착/락 반납에 실패했으면
                        self.publish_status('STOPPED' if self._stop_requested else 'FAILED',
                                            idx, self.ZONE_EXIT_NAME)
                        return                              #   -> 작업 종료 (락은 유지됨)
                    in_zone = False                         # 반납 완료 -> "락 없음" 표시

                # --- C) 목표 지점으로 이동 ---
                self.publish_status('MOVING', idx, name)
                if not self._navigate(self.POINTS[name]):   # 목표 지점 이동에 실패했으면 (stop 포함)
                    if in_zone and not self._stop_requested:  # 구역 안에 있고, stop이 아닌 "실패"라면
                        # 이동 실패(stop 아님): ZONE_EXIT 후퇴 시도, 성공 시 락 반납
                        in_zone = not self._leave_zone()    # 반납 성공(True)이면 in_zone=False, 실패면 True 유지
                    self.publish_status('STOPPED' if self._stop_requested else 'FAILED',
                                        idx, name)
                    return                                  #   -> 작업 종료

                # --- D) 도착: 노랑 깜박임 + 대기 ---
                self.publish_status('ARRIVED', idx, name)
                if self.WAIT_EVERY_POINT or idx == last:    # 모든 지점에서 대기하는 설정이거나, 마지막 지점이면
                    self.start_blink(255, 255, 0)
                    completed = self._wait_interruptible(self.GOTO_WAIT_SEC)
                    self.stop_blink()
                    if not completed:                       # 대기를 끝까지 못 했으면 (중간에 stop)
                        self.publish_status('STOPPED', idx, name)  # 구역 안이면 락 유지
                        return

            # --- E) 마지막 지점이 구역 안이면: ZONE_EXIT 도착 후 락 반납 ---
            if in_zone:                                     # 락을 아직 가지고 있으면 (마지막 지점이 구역 안)
                self.publish_status('LEAVING_ZONE', last, self.ZONE_EXIT_NAME)
                if not self._leave_zone():                  # ZONE_EXIT 도착/락 반납에 실패했으면
                    self.publish_status('STOPPED' if self._stop_requested else 'FAILED',
                                        last, self.ZONE_EXIT_NAME)
                    return
                in_zone = False                             # 반납 완료

            # --- F) 홈으로 복귀 ---
            self.publish_status('RETURNING', -1, self.HOME_NAME)
            if self._navigate(self.POINTS[self.HOME_NAME]): # 홈에 도착했으면 (여기만 not 없음)
                self.publish_status('DONE', -1, 'returned home')
            else:                                           # 홈 이동 실패 또는 stop
                self.publish_status('STOPPED' if self._stop_requested else 'FAILED', -1, 'return')
        finally:
            # stop 으로 끝났고 구역 안에서 락을 들고 있으면 구역 밖(ZONE_STOP_EXIT)으로 나와 락을 반납
            # (emergency_stop 이면 현재 위치/락 유지)
            if in_zone and self._stop_requested:
                if self._retreat_after_stop():
                    self.publish_status('STOPPED', -1, 'retreated')
            self.set_idle_led()     # 어떤 경우든 종료 시 대기 LED로 복귀
            self._running = False

    # ==========================================================================
    # 13. 작업: 전체 순찰
    # ==========================================================================
    def _run_patrol(self):
        """[별도 스레드] WAYPOINTS 전체 순회. rclpy.spin()을 막지 않아 stop을 받을 수 있음."""
        self._running = True
        holding = False   # True = 허가(락) 보유 중 (stop 시 후퇴/반납 판단용)
        self.publish_status('STARTING', -1)
        try:
            # 1) 최초 1회만 초기 위치 보정 (이후 start/goto에서는 생략)
            self._init_pose_once()

            # 2) Nav2 활성화 대기
            self.navigator.waitUntilNav2Active()

            # 3) 웨이포인트 순회
            for i, wp in enumerate(self.WAYPOINTS):
                if self._stop_requested:
                    self.publish_status('STOPPED', i)
                    break

                # 구역 진입 waypoint: 일반 이동 대신 goto 경로와 같은 _queue_and_enter 사용
                # (요청 -> 슬롯 대기 -> 허가 -> 문 이동 까지 한 곳에서 처리)
                if i == self.ZONE_ENTRY_INDEX:
                    if not self._queue_and_enter(i, self.ZONE_ENTRY_NAME):
                        break   # 중단/실패 상태는 _queue_and_enter 가 이미 발행함 (DONE 건너뜀)
                    holding = True
                    self.publish_status('MOVING', i + 1)
                    continue

                pose = self.make_pose(*wp)
                self.set_drive_led()   # 주행 중: 초록 고정 점등

                # 최대 3회 재시도 (일시적 장애물 / 로컬라이제이션 튐 대응)
                for attempt in range(3):
                    self.navigator.goToPose(pose)

                    while not self.navigator.isTaskComplete():
                        if self._stop_requested:
                            self.navigator.cancelTask()
                            break
                        time.sleep(0.1)

                    if self._stop_requested:
                        break

                    result = self.navigator.getResult()

                    # 도착 여부와 무관하게 현재 위치 저장 (다음 재시작 대비)
                    current = self.get_current_pose()
                    if current is None:
                        current = (pose.pose.position.x, pose.pose.position.y, 0.0)
                    self.save_last_pose(*current)

                    if result == TaskResult.SUCCEEDED:
                        self.get_logger().info(f'{i+1}번째 목표 도착 성공 ({attempt+1}번째 시도)')
                        self.publish_status('MOVING', i + 1)

                        # 구역 이탈 waypoint 도착 -> 락 반납
                        if i == self.ZONE_EXIT_INDEX:
                            self.gate.notify_exit()
                            holding = False

                        break  # 성공 -> 재시도 루프 탈출 (else 스킵)
                    else:
                        self.get_logger().warn(
                            f'{i+1}번째 목표 {attempt+1}번째 시도 실패: {result}, 재시도합니다')
                        self.publish_status('RETRY', i + 1, str(result))
                else:
                    # for-else: 3회 모두 실패
                    self.get_logger().error(f'{i+1}번째 목표 최종 실패 (3회 모두 실패)')
                    self.publish_status('FAILED', i + 1)

                if self._stop_requested:
                    # 구역 안에서 멈춘 경우: 여기서는 notify_exit 를 보내지 않고 finally 에서
                    # stop 이면 ZONE_STOP_EXIT 로 후퇴 후 반납, emergency_stop 이면 락 유지(max_hold_sec 뒤 해제)
                    self.publish_status('STOPPED', i)
                    break
            else:
                # for-else: 바깥 루프가 끝까지 돌았을 때 = 전체 완주
                self.publish_status('DONE', len(self.WAYPOINTS))
        finally:
            # stop 으로 끝났고 구역 안에서 락을 들고 있으면 ZONE_STOP_EXIT 로 나와 락을 반납 (emergency_stop 이면 유지)
            if holding and self._stop_requested:
                if self._retreat_after_stop():
                    self.publish_status('STOPPED', -1, 'retreated')
            self.set_idle_led()     # 순찰 종료(DONE/STOPPED/FAILED/예외) -> 대기 LED
            self._running = False


# ==============================================================================
# 14. main
# ==============================================================================
def main(args=None):
    rclpy.init(args=args, signal_handler_options=SignalHandlerOptions.NO)
    node = PinkyPatrolNode()

    # rclpy.spin(node) 대신 전용 executor를 명시적으로 사용
    executor = rclpy.executors.SingleThreadedExecutor()
    executor.add_node(node)

    try:
        executor.spin()   # patrol_cmd 명령을 계속 기다림
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        try:
            node.clear_led()
        except Exception:
            pass
        if node._led_proc is not None:
            # terminate(SIGTERM)는 led_server 내부 rclpy 핸들러를 거쳐
            # 이중 shutdown 에러를 내므로, 이미 LED를 끈 뒤에 kill()로 바로 종료
            node._led_proc.kill()
            try:
                node._led_proc.wait(timeout=2.0)
            except Exception:
                pass
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()