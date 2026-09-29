import sys

import rclpy
from rclpy.node import Node
from rclpy.duration import Duration
from rclpy.qos import QoSProfile, ReliabilityPolicy
from geometry_msgs.msg import Twist
from std_msgs.msg import Bool, Float32


# max(최소값, min(최대값, x)): x를 [최소값, 최대값] 범위로 잘라내는(clamp) 관용 표현
def clamp(x, lo, hi):
    return max(lo, min(hi, x))


# 현재값에서 목표값으로 가되, 한 스텝에 max_delta 이상 변하지 않게 제한 (가속도 제한)
def slew(current, target, max_delta):
    return current + clamp(target - current, -max_delta, max_delta)


class LaneFollowerNode(Node):
    """
    lane_detector_node가 발행하는 lane/center_offset, lane/detected(, crossline/detected)를
    구독해서 실제로 cmd_vel(Twist)을 publish하는 주행 노드.
    (한쪽 차선 처리를 위해 lane/left_detected, lane/right_detected, 코너 대응을 위해
     lane/offset_trend도 함께 구독한다.)

    제어 방식: offset(-1.0 ~ 1.0, 0이 정중앙)에 대한 P(비례) + D(미분) 제어.
      angular.z = -(kp * offset + kd * d_offset/dt)
      (offset이 양수 = 차선 중심이 화면 오른쪽에 있음 -> 오른쪽으로 돌아야 하므로
       ROS 표준(양의 각속도 = 반시계/왼쪽 회전) 기준 angular.z는 음수 방향)

    부드러운 주행을 위한 처리:
      - offset과 D항에 저역통과(EMA) 필터를 적용한다.
      - 콜백은 값만 저장하고, 고정 주기(control_rate) 타이머에서 cmd_vel을 계산/발행한다.
      - 선속도/각속도에 가속도 제한(slew)을 걸어 cmd_vel이 계단식으로 튀지 않게 한다.

    한쪽 차선만 보일 때:
      - offset이 추정값이므로 속도를 줄이고(single_side_speed_scale),
        보이는 차선에서 멀어지는 쪽(안 보이는 차선 쪽)으로 회전을 추가한다(single_side_turn_bias).

    코너(추세 기반 탐색/보정):
      - lane/offset_trend(보이는 차선이 멀어질수록 휘는 방향/정도)를 매 수신마다 반영해서
        "차선이 사라지기 직전 어느 쪽으로 휘고 있었는지"를 _last_side에 기억해 둔다
        (offset 자체가 0.05를 못 넘는 완만한 구간에서도 trend는 먼저 반응할 수 있음).
      - kd_trend(기본 0.0)를 0보다 크게 주면, 추종 중에도 trend를 살짝 feed-forward로
        더해 코너 진입을 미리 시작하게 할 수 있다.

    안전장치:
      - 검출이 detect_grace_time 이내로 잠깐 끊긴 것은 소실로 보지 않고 계속 추종한다.
      - 차선을 잃어버리면(lane_detected=False) 시간 기준으로 단계별 처리한다.
          (1) lost_hold_time 동안: 마지막 조향을 유지하며 감속
          (2) lost_stop_time 까지: 마지막으로 라인이 있던(또는 휘던) 방향으로 천천히 회전하며 탐색
          (3) 그 이후: 정지(linear.x=0)
        소실 중의 offset 값은 신뢰하지 않고 무시한다.
      - offset 토픽 자체가 일정 시간(watchdog_timeout) 이상 끊기면(카메라/추론 노드 다운)
        무조건 정지한다.
    """

    # ===============================================================
    # 초기화 (파라미터 로드, 발행자/구독자/워치독 타이머 등록)
    # ===============================================================
    def __init__(self):
        super().__init__('lane_follower_node')

        self.declare_parameter('linear_speed', 0.05)            # 기본 직진 속도 (m/s)
        self.declare_parameter('min_linear_speed', 0.03)        # 많이 꺾을 때 최저 속도
        self.declare_parameter('max_linear_accel', 0.15)        # 선속도 가속도 제한 (m/s^2)
        self.declare_parameter('kp', 0.8)                       # 비례 게인
        self.declare_parameter('kd', 0.1)                       # 미분 게인
        self.declare_parameter('max_angular_speed', 0.8)        # 각속도 제한 (rad/s)
        self.declare_parameter('max_angular_accel', 2.0)        # 각속도 가속도 제한 (rad/s^2)
        self.declare_parameter('offset_alpha', 0.5)             # offset 필터 (0~1, 작을수록 부드러움)
        self.declare_parameter('d_alpha', 0.3)                  # 미분값 필터 (0~1, 작을수록 부드러움)
        self.declare_parameter('single_side_speed_scale', 0.6)  # 한쪽 차선만 보일 때 속도 배율
        self.declare_parameter('single_side_turn_bias', 0.15)   # 한쪽 차선만 보일 때 추가 회전 각속도 (rad/s, 0이면 회전 추가 안 함)
        self.declare_parameter('kd_trend', 0.0)                 # trend feed-forward 게인 (0이면 미사용)
        self.declare_parameter('trend_side_threshold', 0.05)    # 이 값을 넘는 trend만 탐색 방향(_last_side)에 반영
        self.declare_parameter('detect_grace_time', 0.2)        # 검출이 이 시간 이내로 끊기면 소실로 보지 않고 계속 추종(s)
        self.declare_parameter('lost_hold_time', 0.5)           # 차선 소실 시 마지막 조향 유지 시간(s, grace 시간 포함)
        self.declare_parameter('lost_stop_time', 1.5)           # 차선 소실 후 완전 정지까지 시간(s)
        self.declare_parameter('search_angular_speed', 0.3)     # 소실 후 탐색 회전 속도 (0이면 탐색 안 함)
        self.declare_parameter('lost_speed_scale', 0.5)         # 소실 중 속도 배율
        self.declare_parameter('watchdog_timeout', 0.5)         # offset 미수신 시 정지까지 시간(s)
        self.declare_parameter('control_rate', 20.0)            # 제어 루프 주기 (Hz)
        self.declare_parameter('stop_on_crossline', False)      # Crossline 검출 시 정지할지 여부
        self.declare_parameter('crossline_stop_duration', 2.0)  # 정지 유지 시간(s)

        self.linear_speed = self.get_parameter('linear_speed').value
        self.min_linear_speed = self.get_parameter('min_linear_speed').value
        self.max_linear_accel = self.get_parameter('max_linear_accel').value
        self.kp = self.get_parameter('kp').value
        self.kd = self.get_parameter('kd').value
        self.max_angular_speed = self.get_parameter('max_angular_speed').value
        self.max_angular_accel = self.get_parameter('max_angular_accel').value
        self.offset_alpha = self.get_parameter('offset_alpha').value
        self.d_alpha = self.get_parameter('d_alpha').value
        self.single_side_speed_scale = self.get_parameter('single_side_speed_scale').value
        self.single_side_turn_bias = self.get_parameter('single_side_turn_bias').value
        self.kd_trend = self.get_parameter('kd_trend').value
        self.trend_side_threshold = self.get_parameter('trend_side_threshold').value
        self.detect_grace_time = self.get_parameter('detect_grace_time').value
        self.lost_hold_time = self.get_parameter('lost_hold_time').value
        self.lost_stop_time = self.get_parameter('lost_stop_time').value
        self.search_angular_speed = self.get_parameter('search_angular_speed').value
        self.lost_speed_scale = self.get_parameter('lost_speed_scale').value
        self.watchdog_timeout = self.get_parameter('watchdog_timeout').value
        self.dt_ctrl = 1.0 / self.get_parameter('control_rate').value
        self.stop_on_crossline = self.get_parameter('stop_on_crossline').value
        self.crossline_stop_duration = self.get_parameter('crossline_stop_duration').value

        now = self.get_clock().now()
        self._offset_f = 0.0               # 필터링된 offset
        self._d_f = 0.0                    # 필터링된 미분값
        self._trend = 0.0                  # 가장 최근에 수신한 lane/offset_trend
        self._prev_time = None             # D항 계산용 이전 수신 시각 (소실 시 None으로 리셋)
        self._last_msg_time = now
        self._last_valid_time = now        # 마지막으로 차선이 검출된 시각
        self._last_side = 0.0              # 탐색 회전 방향 (trend 부호 기반, 소실 시 이 방향으로 회전)
        self._crossline_stop_until = None  # rclpy Time or None
        self._lane_detected = False
        self._left_detected = False        # 좌/우 차선이 이번 프레임에 실제로 관측됐는지
        self._right_detected = False

        self._cmd_lin = 0.0                # 현재 출력값 (가속도 제한 계산용)
        self._cmd_ang = 0.0

        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)

        self.cmd_pub = self.create_publisher(Twist, 'cmd_vel', 10)

        self.create_subscription(Float32, 'lane/center_offset', self.offset_callback, qos)
        self.create_subscription(Bool, 'lane/detected', self.detected_callback, qos)
        self.create_subscription(Bool, 'lane/left_detected', self.left_callback, qos)
        self.create_subscription(Bool, 'lane/right_detected', self.right_callback, qos)
        self.create_subscription(Float32, 'lane/offset_trend', self.trend_callback, qos)
        self.create_subscription(Bool, 'crossline/detected', self.crossline_callback, qos)

        # 고정 주기 제어 루프 (내부에서 offset 토픽 끊김 워치독도 함께 확인)
        self.create_timer(self.dt_ctrl, self.control_loop)

        self.get_logger().info('lane_follower_node 시작 (cmd_vel 발행)')

    # ===============================================================
    # 콜백: 차선 검출 여부 수신 -> 마지막 검출 시각 갱신
    # ===============================================================
    def detected_callback(self, msg: Bool):
        self._lane_detected = msg.data
        if msg.data:
            self._last_valid_time = self.get_clock().now()

    # ===============================================================
    # 콜백: 좌/우 차선 개별 검출 여부 수신 (한쪽만 보일 때 감속/회전용)
    # ===============================================================
    def left_callback(self, msg: Bool):
        self._left_detected = msg.data

    def right_callback(self, msg: Bool):
        self._right_detected = msg.data

    # ===============================================================
    # 콜백: 차선 추세(offset_trend) 수신 -> 저장 + 탐색 방향(_last_side) 갱신
    # ===============================================================
    def trend_callback(self, msg: Float32):
        self._trend = float(msg.data)
        # trend 부호는 "선이 멀어질수록 어느 쪽으로 휘는지"이므로, 탐색 회전에 쓰는
        # _last_side(= -offset 부호 규약)에 맞추려면 부호를 뒤집어야 한다.
        # (예: trend>0 = 앞쪽에서 선이 점점 왼쪽으로 휨 -> offset이 곧 음수 쪽으로
        #  움직인다는 뜻이므로 _last_side도 음수가 되어야 탐색 시 왼쪽으로 돈다)
        if abs(self._trend) > self.trend_side_threshold:
            self._last_side = -1.0 if self._trend > 0 else 1.0

    # ===============================================================
    # 콜백: Crossline 검출 시 정지 종료 시각(_crossline_stop_until) 설정
    # ===============================================================
    def crossline_callback(self, msg: Bool):
        if self.stop_on_crossline and msg.data and self._crossline_stop_until is None:
            self.get_logger().info('Crossline 검출: 잠시 정지')
            # ROS 시간(Time)에 ROS 기간(Duration)을 더해 "몇 초 뒤 시각"을 구함
            self._crossline_stop_until = self.get_clock().now() + Duration(
                seconds=self.crossline_stop_duration)

    # ===============================================================
    # 콜백: offset 수신 -> 필터링 후 저장만 함 (cmd_vel 계산은 control_loop에서)
    # ===============================================================
    def offset_callback(self, msg: Float32):
        now = self.get_clock().now()
        self._last_msg_time = now

        # 차선 소실 중의 offset은 신뢰할 수 없으므로 무시하고, D항 이력을 리셋
        if not self._lane_detected:
            self._prev_time = None
            return

        offset = clamp(float(msg.data), -1.0, 1.0)
        prev_filtered = self._offset_f
        # 저역통과(EMA) 필터: 갑작스러운 offset 변화를 완화
        self._offset_f = self.offset_alpha * offset + (1.0 - self.offset_alpha) * prev_filtered

        if self._prev_time is None:
            # 소실 후 복귀한 첫 프레임: 미분 스파이크 방지를 위해 D항은 0
            self._d_f = 0.0
        else:
            # (now - self._prev_time)는 ROS Duration 객체 -> .nanoseconds로 나노초를 꺼내 초 단위로 변환
            dt = max((now - self._prev_time).nanoseconds / 1e9, 1e-3)
            d_offset = (self._offset_f - prev_filtered) / dt
            self._d_f = self.d_alpha * d_offset + (1.0 - self.d_alpha) * self._d_f
        self._prev_time = now

    # ===============================================================
    # 메인 제어 루프: 고정 주기로 PD 제어 -> 가속도 제한 -> cmd_vel 발행
    # ===============================================================
    def control_loop(self):
        now = self.get_clock().now()

        # offset 수신이 오래 끊겼으면 강제 정지
        if self.watchdog_tick():
            return

        # Crossline 정지 구간이면 그대로 정지 유지
        if self._crossline_stop_until is not None:
            if now < self._crossline_stop_until:
                self._stop_now()
                return
            self._crossline_stop_until = None

        # 마지막으로 차선이 검출된 후 지금까지 흐른 시간(초)
        lost = (now - self._last_valid_time).nanoseconds / 1e9

        # 검출이 잠깐(detect_grace_time 이내) 끊긴 것은 소실로 보지 않고 마지막 offset으로 계속 추종
        if self._lane_detected or lost < self.detect_grace_time:
            target_lin, target_ang = self.compute_tracking()
        else:
            # 차선 소실: 경과 시간에 따라 유지 -> 탐색 -> 정지
            if lost < self.lost_hold_time:
                # 마지막 조향을 유지하며 감속
                target_lin = self.min_linear_speed * self.lost_speed_scale
                target_ang = self._cmd_ang
            elif lost < self.lost_stop_time:
                # 마지막으로 라인이 있던 방향으로 제자리 회전하며 탐색
                target_lin = 0.0
                target_ang = -self._last_side * self.search_angular_speed
            else:
                # 차선을 너무 오래 잃어버렸으면 정지
                target_lin, target_ang = 0.0, 0.0

        # 가속도 제한을 거쳐 급격한 명령 변화를 방지
        self._cmd_lin = slew(self._cmd_lin, target_lin, self.max_linear_accel * self.dt_ctrl)
        self._cmd_ang = slew(self._cmd_ang, target_ang, self.max_angular_accel * self.dt_ctrl)
        self.publish_cmd(self._cmd_lin, self._cmd_ang)

    # ===============================================================
    # 유틸: 필터링된 offset으로 PD 제어 목표 속도(linear, angular) 계산
    # ===============================================================
    def compute_tracking(self):
        offset = self._offset_f

        angular_z = -(self.kp * offset + self.kd * self._d_f)
        # trend feed-forward: kd_trend>0이면 코너 진입을 조금 미리 시작 (기본 0이면 무영향).
        # trend_callback의 부호 규약과 동일: trend>0(앞이 왼쪽으로 휨) -> angular_z를 양(왼쪽)으로.
        angular_z += self.kd_trend * self._trend

        # 많이 꺾을수록(오프셋이 클수록) 속도를 줄여서 커브에서 안정적으로 돌게 함
        speed_scale = max(0.0, 1.0 - min(abs(offset), 1.0))
        linear_x = self.min_linear_speed + (self.linear_speed - self.min_linear_speed) * speed_scale

        # 한쪽 차선만 보이면 offset이 추정값이므로 감속하고,
        # 보이는 차선에서 멀어지는 쪽(안 보이는 차선 쪽)으로 회전을 추가
        if self._left_detected != self._right_detected:
            linear_x *= self.single_side_speed_scale
            # ROS 표준: 양의 각속도 = 왼쪽 회전, 음수 = 오른쪽 회전
            # 왼쪽 차선만 보임 -> 오른쪽으로(음수), 오른쪽 차선만 보임 -> 왼쪽으로(양수)
            if self._left_detected:
                angular_z -= self.single_side_turn_bias
            else:
                angular_z += self.single_side_turn_bias

        # 회전을 더한 뒤에도 최대 각속도를 넘지 않게 제한
        angular_z = clamp(angular_z, -self.max_angular_speed, self.max_angular_speed)

        return linear_x, angular_z

    # ===============================================================
    # 워치독: offset 수신이 오래 끊기면 강제 정지 (정지했으면 True 반환)
    # ===============================================================
    def watchdog_tick(self):
        # 마지막 offset 수신 후 지금까지 흐른 시간(초)
        elapsed = (self.get_clock().now() - self._last_msg_time).nanoseconds / 1e9
        if elapsed > self.watchdog_timeout:
            self._stop_now()
            return True
        return False

    # ===============================================================
    # 유틸: 출력 상태까지 함께 0으로 만들고 정지 명령 발행
    # ===============================================================
    def _stop_now(self):
        self._cmd_lin = 0.0
        self._cmd_ang = 0.0
        self.publish_cmd(0.0, 0.0)

    # ===============================================================
    # 유틸: Twist 메시지를 만들어 cmd_vel로 발행
    # ===============================================================
    def publish_cmd(self, linear_x: float, angular_z: float):
        twist = Twist()
        twist.linear.x = float(linear_x)
        twist.angular.z = float(angular_z)
        self.cmd_pub.publish(twist)


# ===============================================================
# 엔트리 포인트 (노드 생성 후 rclpy.spin으로 실행, 종료 시 정지 후 정리)
# ===============================================================
def main(args=None):
    rclpy.init(args=args)

    try:
        node = LaneFollowerNode()
    except Exception as e:
        print(f'[lane_follower_node] 노드를 시작할 수 없습니다: {e}', file=sys.stderr)
        rclpy.shutdown()
        sys.exit(1)

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:      # 종료 시 로봇 정지
        node.publish_cmd(0.0, 0.0)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()