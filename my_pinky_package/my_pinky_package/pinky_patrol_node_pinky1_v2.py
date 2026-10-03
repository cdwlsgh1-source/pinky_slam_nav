"""
Pinky 순찰 노드

[지원 명령]  (토픽: patrol_cmd)
  start      : WAYPOINTS 전체 순회
  goto:<P>   : <P> 지점 이동 -> 10초 대기(노랑 LED) -> 홈(P1) 복귀
  stop       : 현재 작업 즉시 중단

[상태 발행]  (토픽: patrol_status, JSON)
  IDLE / STARTING / MOVING / WAITING_ZONE / ARRIVED /
  LEAVING_ZONE / RETURNING / DONE / STOPPED / FAILED / RETRY

[파일 구성]
  1. IMPORT
  2. 좌표 / 경로 상수
  3. 위험 구역(ZONE) 상수
  4. goto 상수
  5. LED 상수
  6. 초기화 (__init__)
  7. LED 제어
  8. 통신 (상태 발행 / 명령 수신)
  9. 좌표 변환 / 위치 저장
  10. 주행 (Nav2 이동 헬퍼)
  11. 위험 구역 (zone mutex 헬퍼)
  12. 작업: goto 후 복귀
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
from zone_traffic_control.zone_gate_client import ZoneGateClient


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
        "P1": (0.000,  0.000),   # 원점 부근 (시작점 / 홈)
        "P2": (0.650,  0.150),   # 원점과 우측 구간 사이 중간 지점
        "P3": (1.550, -0.400),   # 우측 아래 끝
        "P4": (1.000, -0.400),   # 중앙-우측 아래
        "P5": (1.000,  0.100),   # 중앙-우측 위
        "P6": (1.550,  0.060),   # 우측 위 끝
        "P7": (0.000, -0.600),   # 원점 아래쪽

        # 위험 구역 경계(RED LINE): IN = 진입 방향, OUT = 이탈 방향(180도 회전)
        "RED1IN":  (0.600, -0.500, 0),
        "RED1OUT": (0.600, -0.500, 3.14),
        "RED2IN":  (0.550, -0.800, 0),
        "RED2OUT": (0.550, -0.800, 3.14),
    }

    # start 명령의 순찰 경로: P2 -> RED1IN -> P3 -> P6 -> RED1OUT -> P1
    WAYPOINTS = [
        POINTS["P2"],       # 0: 중간 경유점
        POINTS["RED1IN"],   # 1: RED LINE (도착 후 진입 허가 요청)
        POINTS["P3"],       # 2: 우측 아래 끝 (위험 구역 안)
        POINTS["P6"],       # 3: 우측 위 끝   (위험 구역 안)
        POINTS["RED1OUT"],  # 4: RED LINE (도착 시 이탈 통보)
        POINTS["P1"],       # 5: 시작점 복귀
    ]

    # ==========================================================================
    # 3. 위험 구역(ZONE) 상수
    # ==========================================================================
    # [순찰(start)용] WAYPOINTS 리스트의 "순번(index)"이며 좌표 값이 아닙니다.
    #   ZONE_ENTRY_INDEX: 이 waypoint 도착 직후, 다음으로 출발하기 전에 진입 허가 요청
    #   ZONE_EXIT_INDEX : 이 waypoint 도착 시 구역을 벗어난 것으로 보고 락 반납
    ZONE_ENTRY_INDEX = 1   # WAYPOINTS[1] = RED1IN
    ZONE_EXIT_INDEX = 4    # WAYPOINTS[4] = RED1OUT

    # [goto용] POINTS의 키 이름 기준
    ZONE_ENTRY_NAME = 'RED1IN'                       # 허가 요청 지점
    ZONE_EXIT_NAME = 'RED1OUT'                       # 락 반납 지점
    ZONE_POINTS = {'P3', 'P4', 'P5', 'P6'}           # 위험 구역 안 포인트

    # ==========================================================================
    # 4. goto 상수
    # ==========================================================================
    HOME_NAME = 'P1'                                  # 작업 후 복귀할 초기 위치
    GOTO_WAIT_SEC = 10.0                              # 목적지 도착 후 대기 시간(초)
    GOTO_ALLOWED = {'P1', 'P2', 'P7'} | ZONE_POINTS   # RED*는 직접 goto 금지

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

        # 실행 시 --ros-args -p robot_id:=pinky1
        # zone_manager_node의 robot_ids 파라미터 값과 정확히 같아야 함
        self.declare_parameter('robot_id', 'pinky1')
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
        self._stop_requested = False  # stop 명령 수신 여부
        self._thread = None           # 작업 스레드 핸들

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
            self._running = True   # 스레드 시작 전에 세팅 (연타 시 중복 실행 방지)
            self._thread = threading.Thread(target=self._run_patrol, daemon=True)
            self._thread.start()

        # --- goto:<POINT>: 이동 -> 대기 -> 복귀 ---
        elif cmd.startswith('goto:'):
            name = raw.split(':', 1)[1].strip().upper()
            if name not in self.POINTS:
                self.get_logger().warn(f'알 수 없는 포인트: {name}')
                self.publish_status('FAILED', -1, f'unknown point: {name}')
                return
            if name not in self.GOTO_ALLOWED:
                self.get_logger().warn(f'goto 불가 포인트: {name}')
                self.publish_status('FAILED', -1, f'not allowed: {name}')
                return
            if self._running:
                self.get_logger().warn('작업 중입니다. 명령 무시.')
                return
            self._stop_requested = False
            self._running = True
            self._thread = threading.Thread(
                target=self._run_goto_and_return, args=(name,), daemon=True)
            self._thread.start()

        # --- stop: 현재 작업 중단 ---
        elif cmd == 'stop':
            if not self._running:
                self.get_logger().warn('순찰 중이 아닙니다. 명령 무시.')
                return
            self._stop_requested = True
            self.navigator.cancelTask()   # 로컬 액션이라 바로 취소 가능

        else:
            self.get_logger().warn(f'알 수 없는 명령: {cmd}')

    # ==========================================================================
    # 9. 좌표 변환 / 위치 저장
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
    def _leave_zone(self):
        """RED1OUT 이동 후 락 반납. 도착하면 True, 실패하면 False(락 유지)."""
        if self._navigate(self.POINTS[self.ZONE_EXIT_NAME]):
            self.gate.notify_exit()
            self.get_logger().info('위험 구역 이탈, 락 반납 완료')
            return True
        self.get_logger().warn('RED1OUT 도달 실패 - 락 유지 (max_hold_sec 타임아웃 대기)')
        return False

    # ==========================================================================
    # 12. 작업: goto 후 복귀
    # ==========================================================================
    def _run_goto_and_return(self, name):
        """
        [별도 스레드] 흐름
          구역 밖 포인트: 이동 -> 노랑 대기 -> 홈 복귀
          구역 안 포인트: RED1IN -> 허가 대기 -> 이동 -> 노랑 대기 -> RED1OUT(락 반납) -> 홈 복귀
        """
        needs_zone = name in self.ZONE_POINTS
        in_zone = False   # True = 허가(락) 보유 중
        try:
            self.publish_status('STARTING', -1, name)
            self.navigator.waitUntilNav2Active()

            # --- A) 구역 안 포인트: RED1IN 도착 후 허가 대기 ---
            if needs_zone:
                self.publish_status('MOVING', -1, self.ZONE_ENTRY_NAME)
                if not self._navigate(self.POINTS[self.ZONE_ENTRY_NAME]):
                    # 락 받기 전이므로 반납할 것 없음
                    self.publish_status('STOPPED' if self._stop_requested else 'FAILED',
                                        -1, self.ZONE_ENTRY_NAME)
                    return

                self.publish_status('WAITING_ZONE', -1, name)
                self.start_blink(255, 0, 0)               # 허가 대기: 빨강 깜박임
                granted = self.gate.wait_for_entry(
                    stop_check=lambda: self._stop_requested)
                self.stop_blink()
                if not granted:                           # 대기 중 stop
                    self.publish_status('STOPPED', -1, name)
                    return
                in_zone = True                            # 여기부터 락 보유

            # --- B) 목표 지점으로 이동 ---
            self.publish_status('MOVING', -1, name)
            if not self._navigate(self.POINTS[name]):
                if in_zone and not self._stop_requested:
                    # 이동 실패(stop 아님): RED1OUT 후퇴 시도, 성공 시 락 반납
                    in_zone = not self._leave_zone()
                self.publish_status('STOPPED' if self._stop_requested else 'FAILED', -1, name)
                return

            # --- C) 도착: 노랑 깜박임 + 대기 ---
            self.publish_status('ARRIVED', -1, name)
            self.start_blink(255, 255, 0)
            if not self._wait_interruptible(self.GOTO_WAIT_SEC):
                self.stop_blink()
                self.publish_status('STOPPED', -1, name)  # 구역 안이면 락 유지
                return
            self.stop_blink()

            # --- D) 구역 이탈: RED1OUT 도착 후 락 반납 ---
            if in_zone:
                self.publish_status('LEAVING_ZONE', -1, self.ZONE_EXIT_NAME)
                if not self._leave_zone():
                    self.publish_status('STOPPED' if self._stop_requested else 'FAILED',
                                        -1, self.ZONE_EXIT_NAME)
                    return
                in_zone = False

            # --- E) 홈으로 복귀 ---
            self.publish_status('RETURNING', -1, self.HOME_NAME)
            if self._navigate(self.POINTS[self.HOME_NAME]):
                self.publish_status('DONE', -1, 'returned home')
            else:
                self.publish_status('STOPPED' if self._stop_requested else 'FAILED', -1, 'return')
        finally:
            self.set_idle_led()     # 어떤 경우든 종료 시 대기 LED로 복귀
            self._running = False

    # ==========================================================================
    # 13. 작업: 전체 순찰
    # ==========================================================================
    def _run_patrol(self):
        """[별도 스레드] WAYPOINTS 전체 순회. rclpy.spin()을 막지 않아 stop을 받을 수 있음."""
        self._running = True
        self.publish_status('STARTING', -1)
        try:
            # 1) 이전 위치가 있으면 초기 위치 보정
            last = self.load_last_pose()
            if last is not None:
                self.publish_initial_pose(last['x'], last['y'], last['yaw'])
                self.get_logger().info('초기 방향 보정을 위해 제자리 회전을 시작합니다...')
                self.spin_in_place(duration=4.0, angular_speed=0.5)
                self.get_logger().info('초기 방향 보정 완료.')

            # 2) Nav2 활성화 대기
            self.navigator.waitUntilNav2Active()

            # 3) 웨이포인트 순회
            for i, wp in enumerate(self.WAYPOINTS):
                if self._stop_requested:
                    self.publish_status('STOPPED', i)
                    break

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

                        # 구역 진입 waypoint 도착 -> 허가 대기 후 출발
                        if i == self.ZONE_ENTRY_INDEX:
                            self.get_logger().info(f'[{i+1}] 위험 구역 진입 허가 요청 중...')
                            self.publish_status('WAITING_ZONE', i)
                            self.start_blink(255, 0, 0)       # 허가 대기: 빨강 깜박임
                            granted = self.gate.wait_for_entry(
                                stop_check=lambda: self._stop_requested)
                            self.stop_blink()
                            if granted:
                                self.set_drive_led()          # 허가됨 -> 주행 LED

                        break  # 성공 -> 재시도 루프 탈출 (else 스킵)
                    else:
                        self.get_logger().warn(
                            f'{i+1}번째 목표 {attempt+1}번째 시도 실패: {result}, 재시도합니다')
                        self.publish_status('RETRY', i + 1, str(result))
                else:
                    # for-else: 3회 모두 실패
                    self.get_logger().error(f'{i+1}번째 목표 최종 실패 (3회 모두 실패)')
                    self.publish_status('FAILED', i + 1)

                    # 진입 지점 도달 실패 시 허가 없이 진입할 수 없으므로 중단
                    if i == self.ZONE_ENTRY_INDEX:
                        self.get_logger().error(
                            '위험 구역 진입 지점 도달 실패 - 허가 없이 진입할 수 없어 순찰을 중단합니다')
                        self._stop_requested = True

                if self._stop_requested:
                    # 구역 안에서 멈춘 경우 notify_exit를 보내지 않음
                    # (물리적으로 아직 구역 안. max_hold_sec 타임아웃으로 해제됨)
                    self.publish_status('STOPPED', i)
                    break
            else:
                # for-else: 바깥 루프가 끝까지 돌았을 때 = 전체 완주
                self.publish_status('DONE', len(self.WAYPOINTS))
        finally:
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