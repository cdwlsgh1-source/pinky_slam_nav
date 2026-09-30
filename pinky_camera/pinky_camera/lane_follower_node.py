import math
import sys
from collections import deque

import rclpy
from rclpy.node import Node
from rclpy.duration import Duration
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rcl_interfaces.msg import SetParametersResult
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
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
        self.declare_parameter('side_history_time', 3.0)        # 한쪽 차선 단독 검출 기록을 다수결에 쓰는 최근 시간(s)
        self.declare_parameter('record_max_angular', 0.2)       # 기록은 |현재 각속도 명령|이 이 값 미만(직진 중)일 때만 남김 (rad/s)
        self.declare_parameter('record_max_offset', 0.5)        # 기록은 |offset|이 이 값 미만일 때만 남김
        self.declare_parameter('side_confirm_time', 0.5)        # 같은 쪽 단독 검출이 이 시간(s) 이상 이어져야 기록으로 인정
        self.declare_parameter('side_min_ratio', 0.6)           # 다수결에서 한쪽이 이 비율 이상이어야 방향으로 인정 (0이면 단순 다수결)
        self.declare_parameter('corner_lock_release_time', 2.0) # 수평선이 이 시간(s) 이상 안 보이면(코너 모드 아님) 확정한 회전 방향을 해제
        self.declare_parameter('corner_default_side', 'L')      # 기록으로 방향을 못 정했을 때의 기본 방향: 'L'(왼쪽 차선 기준=우회전) | 'R'(좌회전) | 'none'(정지)
        self.declare_parameter('side_hold_time', 10.0)          # 다수결 표본이 없을 때 마지막 확정 기록을 유지하는 최대 시간(s)
        self.declare_parameter('corner_latch_time', 0.5)        # 수평선이 사라진 뒤에도 이 시간(s) 동안은 '수평선 있음'으로 간주
        self.declare_parameter('corner_turn_speed', 0.5)        # 코너 모드 회전 각속도 크기 (rad/s)
        self.declare_parameter('corner_turn_time', 2.0)         # 기록 반대쪽 회전 제한 시간(s)
        self.declare_parameter('corner_turn_angle_deg', 90.0)   # 코너 모드 목표 회전각(deg, odom yaw 기준). 0이면 시간 기반
        self.declare_parameter('corner_angle_tol_deg', 3.0)     # 목표 각도 도달 허용 오차(deg)
        self.declare_parameter('corner_min_turn_speed', 0.15)   # 목표 각도 근처에서 감속할 때의 최저 회전 속도 (rad/s)
        self.declare_parameter('corner_yaw_gain', 1.5)          # 남은 각도(rad) -> 회전 속도 비례 게인
        self.declare_parameter('corner_angle_timeout', 8.0)     # 각도 회전이 이 시간(s) 안에 안 끝나면 중단
        self.declare_parameter('corner_forward_distance', 0.10) # 코너 회전 전에 모서리까지 직진할 고정 거리 (m, 0이면 바로 회전)
        self.declare_parameter('corner_forward_speed', 0.05)    # 위 직진 속도 (m/s)
        self.declare_parameter('recover_wait_time', 0.5)        # 회전 후 차선이 안 보이면 정지한 채 기다리는 시간(s)
        self.declare_parameter('recover_forward_distance', 0.15) # 대기 후에도 없으면 저속 직진할 거리 (m, 0이면 직진 안 함)
        self.declare_parameter('recover_forward_speed', 0.05)   # 위 직진 속도 (m/s)
        self.declare_parameter('corner_scan_time', 3.0)         # 실패 시 반대쪽으로 스캔하는 시간(s)
        self.declare_parameter('corner_creep_speed', 0.0)       # 코너 모드 중 전진 속도 (m/s, 0이면 제자리 회전)
        self.declare_parameter('corner_slow_scale', 0.7)        # 수평선이 보이지만 차선이 아직 검출될 때 속도 배율
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
        self.side_history_time = self.get_parameter('side_history_time').value
        self.record_max_angular = self.get_parameter('record_max_angular').value
        self.record_max_offset = self.get_parameter('record_max_offset').value
        self.side_confirm_time = self.get_parameter('side_confirm_time').value
        self.side_min_ratio = self.get_parameter('side_min_ratio').value
        self.corner_lock_release_time = self.get_parameter('corner_lock_release_time').value
        self.corner_default_side = self.get_parameter('corner_default_side').value
        self.side_hold_time = self.get_parameter('side_hold_time').value
        self.corner_latch_time = self.get_parameter('corner_latch_time').value
        self.corner_turn_speed = self.get_parameter('corner_turn_speed').value
        self.corner_turn_time = self.get_parameter('corner_turn_time').value
        self.corner_scan_time = self.get_parameter('corner_scan_time').value
        self.corner_forward_distance = self.get_parameter('corner_forward_distance').value
        self.corner_forward_speed = self.get_parameter('corner_forward_speed').value
        self.recover_wait_time = self.get_parameter('recover_wait_time').value
        self.recover_forward_distance = self.get_parameter('recover_forward_distance').value
        self.recover_forward_speed = self.get_parameter('recover_forward_speed').value
        self.corner_turn_angle_deg = self.get_parameter('corner_turn_angle_deg').value
        self.corner_angle_tol_deg = self.get_parameter('corner_angle_tol_deg').value
        self.corner_min_turn_speed = self.get_parameter('corner_min_turn_speed').value
        self.corner_yaw_gain = self.get_parameter('corner_yaw_gain').value
        self.corner_angle_timeout = self.get_parameter('corner_angle_timeout').value
        self.corner_creep_speed = self.get_parameter('corner_creep_speed').value
        self.corner_slow_scale = self.get_parameter('corner_slow_scale').value
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

        self._corner_ahead = False         # 전방 수평선(ㄱ자 코너) 검출 여부
        self._corner_seen_time = None      # 수평선이 마지막으로 보인 시각
        self._side_hist = deque()          # (시각, 'L'|'R'): 한쪽 차선만 보였던 기록
        self._cand_side = None             # 현재 이어지고 있는 단독 검출 쪽 ('L'|'R')과 시작 시각
        self._cand_start = None
        self._confirmed_side = None        # 마지막으로 확정된 기록과 그 시각
        self._confirmed_time = None
        self._lock_last_seen = None
        self._locked_side = None           # 수평선을 처음 본 순간 과거 기록으로 확정한 방향 ('L'|'R')
        self._corner_mode = False          # 코너 모드(기록 반대쪽 회전 -> 스캔) 진행 중인지
        self._corner_start = None
        self._yaw = None                   # odom yaw(rad)와 수신 시각 (각도 기반 회전용)
        self._yaw_time = None
        self._pos = None                   # odom 위치 (x, y)
        self._phase = 'forward'            # 코너 모드 단계: 'forward'(모서리까지 직진) -> 'turn'(90도 회전)
        self._phase_start = None           # 현재 단계 시작 시각과 시작 위치
        self._phase_pos = None
        self._recover_active = False       # 회전 후 재탐색(대기 -> 저속 직진) 중인지
        self._recover_start = None
        self._recover_pos = None
        self._recover_fwd_started = False
        self._recover_fwd_start = None
        self._turned = 0.0                 # 코너 모드 시작 후 누적 회전각(rad, 왼쪽이 +)
        self._corner_armed = True          # 각도 회전 완료 후 차선이 다시 검출되기 전에는 코너 모드 재진입 금지
        self._corner_dir = 0.0             # 1단계 회전 방향 (+1 = 왼쪽, -1 = 오른쪽)

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
        self.create_subscription(Bool, 'lane/corner_ahead', self.corner_callback, qos)
        self.create_subscription(Odometry, 'odom', self.odom_callback, qos)

        # 고정 주기 제어 루프 (내부에서 offset 토픽 끊김 워치독도 함께 확인)
        self.create_timer(self.dt_ctrl, self.control_loop)

        # 실행 중 파라미터 변경(rqt, ros2 param set)을 즉시 반영 (control_rate는 제외)
        self.add_on_set_parameters_callback(self._on_params)

        self.get_logger().info('lane_follower_node 시작 (cmd_vel 발행)')

    # ===============================================================
    # 파라미터 변경 콜백: 같은 이름의 노드 속성에 새 값을 바로 반영
    # ===============================================================
    # 실시간 반영 대상 (파라미터 이름 == self 속성 이름)
    _RUNTIME_PARAMS = (
        'linear_speed', 'min_linear_speed', 'max_linear_accel', 'kp', 'kd',
        'max_angular_speed', 'max_angular_accel', 'offset_alpha', 'd_alpha',
        'single_side_speed_scale', 'single_side_turn_bias', 'kd_trend',
        'trend_side_threshold', 'detect_grace_time', 'lost_hold_time',
        'lost_stop_time', 'search_angular_speed', 'lost_speed_scale',
        'watchdog_timeout', 'stop_on_crossline', 'crossline_stop_duration',
        'side_history_time', 'record_max_angular', 'record_max_offset', 'side_confirm_time',
        'side_hold_time', 'side_min_ratio', 'corner_default_side', 'corner_lock_release_time', 'corner_forward_distance', 'corner_forward_speed',
        'recover_wait_time', 'recover_forward_distance', 'recover_forward_speed',
        'corner_turn_angle_deg', 'corner_angle_tol_deg',
        'corner_min_turn_speed', 'corner_yaw_gain', 'corner_angle_timeout',
        'corner_latch_time', 'corner_turn_speed', 'corner_turn_time',
        'corner_scan_time', 'corner_creep_speed', 'corner_slow_scale',
    )
    # 0~1 범위여야 하는 필터 계수 (범위를 벗어난 값은 거부)
    _UNIT_PARAMS = ('offset_alpha', 'd_alpha')

    def _on_params(self, params):
        # 1) 먼저 전부 검증 (하나라도 잘못되면 아무것도 반영하지 않음)
        for p in params:
            if p.name == 'corner_default_side' and p.value not in ('L', 'R', 'none'):
                return SetParametersResult(
                    successful=False, reason=f"corner_default_side는 'L', 'R', 'none' 중 하나여야 합니다: {p.value}")
            if p.name in self._UNIT_PARAMS and not (0.0 <= float(p.value) <= 1.0):
                return SetParametersResult(
                    successful=False, reason=f'{p.name}는 0~1 사이여야 합니다: {p.value}')
        # 2) 검증 통과 후 반영
        for p in params:
            if p.name in self._RUNTIME_PARAMS:
                setattr(self, p.name, p.value)
                self.get_logger().info(f'파라미터 반영: {p.name} = {p.value}')
            elif p.name == 'control_rate':
                self.get_logger().warn('control_rate는 실행 중 변경이 반영되지 않습니다(재시작 필요)')
        return SetParametersResult(successful=True)

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
    # 콜백: 전방 수평선(ㄱ자 코너) 검출 여부 수신
    # ===============================================================
    def corner_callback(self, msg: Bool):
        self._corner_ahead = msg.data
        if msg.data:
            self._corner_seen_time = self.get_clock().now()

    # ===============================================================
    # 콜백: odom 수신 -> yaw 갱신, 코너 모드 중이면 누적 회전각에 더함
    # ===============================================================
    def odom_callback(self, msg: Odometry):
        q = msg.pose.pose.orientation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        if self._yaw is not None and self._corner_mode and self._phase == 'turn':
            d = yaw - self._yaw
            self._turned += math.atan2(math.sin(d), math.cos(d))   # -pi~pi로 정규화
        self._yaw = yaw
        self._yaw_time = self.get_clock().now()
        self._pos = (msg.pose.pose.position.x, msg.pose.pose.position.y)

    # 각도 기반 회전을 쓸 수 있는지 (목표각 > 0 이고 odom이 최근 0.5초 이내로 들어옴)
    def _angle_mode(self, now):
        return (self.corner_turn_angle_deg > 0.0 and self._yaw_time is not None
                and (now - self._yaw_time).nanoseconds / 1e9 < 0.5)

    # ===============================================================
    # 한쪽 차선 단독 검출 기록: 최근 side_history_time 동안의 다수결 ('L'/'R'/None)
    # ===============================================================
    def _record_side(self, now):
        single = self._left_detected != self._right_detected
        side = ('L' if self._left_detected else 'R') if single else None
        if side is not None:
            # 같은 쪽 단독 검출이 side_confirm_time 이상 이어져야 기록으로 인정 (순간 오검출/깜빡임 제외)
            if side != self._cand_side:
                self._cand_side = side
                self._cand_start = now
            confirmed = (now - self._cand_start).nanoseconds / 1e9 >= self.side_confirm_time
            # 좌우로 헤딩을 바꾸는 중(각속도/offset이 큼)의 프레임은 도로 방향이 아닌 자세를 반영하므로 제외
            steady = (abs(self._cmd_ang) < self.record_max_angular
                      and abs(self._offset_f) < self.record_max_offset)
            if confirmed and steady:
                self._side_hist.append((now, side))
                self._confirmed_side = side
                self._confirmed_time = now
        elif self._left_detected and self._right_detected:
            self._cand_side = None       # 양쪽이 다 보이면 연속 기록 초기화(차선 미검출은 유지)
        limit = self.side_history_time
        while self._side_hist and (now - self._side_hist[0][0]).nanoseconds / 1e9 > limit:
            self._side_hist.popleft()

    # 수평선을 처음 보는 순간(아직 차선이 보이며 접근 중) 과거 기록으로 방향을 확정해 잠금.
    # 이후 헤딩 변화나 차선 소실로 기록이 흐려져도 이 방향을 그대로 씀
    def _update_lock(self, now):
        if self._corner_mode:
            return
        if self._corner_ahead:
            self._lock_last_seen = now
            if self._locked_side is None:
                side = self._history_side()
                if side is not None:
                    self._locked_side = side
                    self.get_logger().info(f'수평선 감지: 방향 확정 기록={side}')
        elif self._locked_side is not None and self._lock_last_seen is not None and \
                (now - self._lock_last_seen).nanoseconds / 1e9 > self.corner_lock_release_time:
            self._locked_side = None   # 오검출(수평선이 곧 사라짐)이면 확정 해제

    def _history_side(self):
        n_left = sum(1 for _, s in self._side_hist if s == 'L')
        n_right = len(self._side_hist) - n_left
        if not self._side_hist:
            # 최근 표본이 없으면 마지막 확정 기록을 side_hold_time 동안 유지
            if (self._confirmed_time is not None and
                    (self.get_clock().now() - self._confirmed_time).nanoseconds / 1e9
                    <= self.side_hold_time):
                return self._confirmed_side
            return None
        if n_left == n_right:
            return None
        major = max(n_left, n_right)
        # 누가 봐도 한쪽이 많을 때만 방향으로 인정 (애매하면 None -> 기존 소실 처리)
        if major / float(len(self._side_hist)) < self.side_min_ratio:
            return None
        return 'L' if n_left > n_right else 'R'

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

        # 한쪽 차선만 보이는 동안의 좌/우 기록 갱신 (코너 모드 방향 결정용)
        self._record_side(now)
        self._update_lock(now)

        # 차선이 다시 검출되면 코너 모드 종료 -> 기존 PD 추종으로 복귀
        # (각도 기반 회전 중에는 목표 각도에 도달할 때까지 차선이 보여도 회전을 유지해 직각을 만듦)
        if self._lane_detected:
            self._recover_active = False
        if self._lane_detected and not self._corner_mode:
            self._corner_armed = True
        if self._lane_detected and not (self._corner_mode and self._angle_mode(now)):
            self._corner_mode = False
        # 코너 모드 진입: 차선 소실 + 수평선(최근 포함) + 방향 기록 있음 (각도 회전 완료 후 차선이 다시 검출되기 전에는 재진입 금지)
        elif (not self._corner_mode and not self._lane_detected and self._corner_recent(now)
              and self._corner_armed):
            # 수평선을 처음 봤을 때 확정한 방향 우선, 없으면 지금 기록으로 결정
            side = self._locked_side or self._history_side()
            if side is None and self.corner_default_side in ('L', 'R'):
                # 기록으로 못 정했으면 기본 방향(기본값 'L' = 왼쪽 차선 기준 우회전)
                side = self.corner_default_side
                self.get_logger().info(f'기록 부족: 기본 방향 사용 기록={side}')
            if side is not None:
                self._corner_mode = True
                self._corner_start = now
                self._phase = 'forward'
                self._phase_start = now
                self._phase_pos = self._pos
                self._turned = 0.0
                # 왼쪽 차선만 보였으면 우회전(-1), 오른쪽만 보였으면 좌회전(+1)
                self._corner_dir = -1.0 if side == 'L' else 1.0
                self.get_logger().info(
                    f'코너 모드 시작: 기록={side}, 회전 방향={"우" if self._corner_dir < 0 else "좌"}')

        if self._corner_mode:
            target_lin, target_ang = self.compute_corner(now)
        elif self._recover_active:
            target_lin, target_ang = self.compute_recover(now)
        # 검출이 잠깐(detect_grace_time 이내) 끊긴 것은 소실로 보지 않고 마지막 offset으로 계속 추종
        elif self._lane_detected or lost < self.detect_grace_time:
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
    # 수평선이 지금 보이거나 corner_latch_time 이내에 보였는지
    # ===============================================================
    def _corner_recent(self, now):
        if self._corner_ahead:
            return True
        if self._corner_seen_time is None:
            return False
        return (now - self._corner_seen_time).nanoseconds / 1e9 <= self.corner_latch_time

    # ===============================================================
    # 코너 모드: 기록 반대쪽 회전(corner_turn_time) -> 반대 방향 스캔(corner_scan_time) -> 정지
    # ===============================================================
    def compute_corner(self, now):
        # 1단계: 모서리까지 고정 거리 직진 (odom 거리, 없으면 시간으로 대체)
        if self._phase == 'forward':
            if self.corner_forward_distance > 0.0 and not self._moved_enough(
                    now, self._phase_start, self._phase_pos,
                    self.corner_forward_distance, self.corner_forward_speed):
                return self.corner_forward_speed, 0.0
            # 직진 끝: 회전 단계로 전환 (회전 시간/각도는 지금부터 측정)
            self._phase = 'turn'
            self._corner_start = now
            self._turned = 0.0
        t = (now - self._corner_start).nanoseconds / 1e9
        if self._angle_mode(now):
            # odom yaw로 목표 각도(기본 90도)까지 회전. 남은 각도에 비례해 감속하며 정확히 멈춤
            progress = self._corner_dir * self._turned
            remaining = math.radians(self.corner_turn_angle_deg) - progress
            if remaining <= math.radians(self.corner_angle_tol_deg):
                self._finish_corner(now, f'회전 완료: {math.degrees(progress):.0f}도')
                return 0.0, 0.0
            if t > self.corner_angle_timeout:
                self._finish_corner(now, f'회전 시간 초과: {math.degrees(progress):.0f}도까지 회전')
                return 0.0, 0.0
            speed = clamp(self.corner_yaw_gain * remaining,
                          self.corner_min_turn_speed, self.corner_turn_speed)
            return self.corner_creep_speed, self._corner_dir * speed
        if t < self.corner_turn_time:
            return self.corner_creep_speed, self._corner_dir * self.corner_turn_speed
        if t < self.corner_turn_time + self.corner_scan_time:
            return self.corner_creep_speed, -self._corner_dir * self.corner_turn_speed
        return 0.0, 0.0

    # 시작 위치에서 distance(m) 이상 움직였는지 (odom이 없으면 distance/speed 시간이 지났는지)
    def _moved_enough(self, now, start_time, start_pos, distance, speed):
        if self._pos is not None and start_pos is not None and self._yaw_time is not None \
                and (now - self._yaw_time).nanoseconds / 1e9 < 0.5:
            return math.hypot(self._pos[0] - start_pos[0], self._pos[1] - start_pos[1]) >= distance
        return (now - start_time).nanoseconds / 1e9 >= distance / max(speed, 1e-3)

    # 회전 후 재탐색: recover_wait_time 동안 정지 -> recover_forward_distance만큼 저속 직진 -> 끝(기존 소실 처리로)
    def compute_recover(self, now):
        t = (now - self._recover_start).nanoseconds / 1e9
        if t < self.recover_wait_time:
            return 0.0, 0.0
        if self.recover_forward_distance > 0.0:
            if not self._recover_fwd_started:
                self._recover_fwd_started = True
                self._recover_pos = self._pos
                self._recover_fwd_start = now
            if not self._moved_enough(now, self._recover_fwd_start, self._recover_pos,
                                      self.recover_forward_distance, self.recover_forward_speed):
                return self.recover_forward_speed, 0.0
        self._recover_active = False
        self.get_logger().info('재탐색 종료: 차선 미검출')
        return 0.0, 0.0

    # 코너 회전 종료: 코너 모드 해제, 재진입 금지, 오래된 방향 기록 초기화
    def _finish_corner(self, now, reason):
        self._corner_mode = False
        self._corner_armed = False
        self._locked_side = None
        self._recover_active = True
        self._recover_start = now
        self._recover_pos = None
        self._recover_fwd_started = False
        self._recover_fwd_start = now
        self._side_hist.clear()
        self._confirmed_side = None
        self._confirmed_time = None
        self._cand_side = None
        self.get_logger().info(f'코너 모드 종료: {reason}')

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

        # 수평선이 보이면(아직 차선이 검출되는 중) 감속만 함
        if self._corner_ahead:
            linear_x *= self.corner_slow_scale

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