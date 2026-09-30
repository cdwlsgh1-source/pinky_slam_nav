import sys

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rcl_interfaces.msg import SetParametersResult
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool, Float32


# ===============================================================
# 좌/우 차선 추적기 (회귀로 각 차선의 위치+추세를 구하고, 좌/우 판정 + 중심 오프셋 계산)
# ===============================================================
class LaneTracker:
    """
    핵심 아이디어: 차선은 좌/우 두 줄이고, 로봇은 그 사이(도로 중앙)로 가야 한다.
    각 인스턴스 마스크를 x = a*y + b 형태로 1차 회귀 피팅해서:
      - 화면 맨 아래(로봇에 가장 가까운 지점)로 외삽한 x = 그 차선의 근접 위치
      - 기울기 a = 그 차선이 멀어질수록(위로 갈수록) 어느 쪽으로 휘는지 (trend)
    를 구한다. 회귀로 외삽하므로 코너에서 마스크가 화면 맨 아래까지 안 닿아도 위치를 낼 수 있다.

    근접 위치가 가까운 인스턴스끼리는 같은 차선의 조각(점선 등)으로 보고 하나로 묶는다.
    묶음이 2개 이상이면 가장 왼쪽/오른쪽을 좌/우 차선으로 보고 실측 중간점을 쓰고,
    1개면 이전 프레임 위치와 비교해 좌/우를 판정한 뒤 학습된 차선 폭으로 반대편을 추정한다.

    모든 x/y좌표는 마스크 크기로 정규화(0.0~1.0)해서 다룬다 (해상도와 무관).

    roi_ratio: 화면 하단에서 몇 %를 관심영역(ROI)으로 볼지. 0.2 = 하단 20%.
    min_pixels: 인스턴스를 유효한 차선 조각으로 인정할 최소 픽셀 수 (노이즈 제거용).
    init_lane_width_ratio: 차선 폭(좌~우 차선 사이 거리)의 초기 가정값 (이미지 폭 대비 비율).
                     양쪽이 다 보일 때마다 실측값으로 갱신된다.
    near_reach_ratio: 인스턴스의 실측 최하단이 이미지 하단에서 이 비율보다 멀리 떨어져 있으면
                     'detected'(바로 추종하기엔 불확실)에서 제외. 클러스터링/trend 계산에는
                     여전히 포함된다 (코너에서 끊기기 직전까지 신호를 주기 위함).
    min_y_span_ratio: 회귀가 의미 있으려면 필요한 최소 y 분산(이미지 높이 대비 비율).
                     이보다 좁으면(거의 점 하나) 기울기가 노이즈이므로 그 인스턴스의 trend는 0.
    width_alpha: 차선 폭 이동평균 계수 (0~1, 작을수록 천천히 갱신).
    history_reset_frames: 차선이 이 프레임 수 이상 연속으로 안 보이면 좌/우 이력을 초기화.
    """

    MIN_LANE_WIDTH = 0.3    # 학습되는 차선 폭의 하한 (이미지 폭 대비)
    MAX_LANE_WIDTH = 0.95   # 학습되는 차선 폭의 상한

    def __init__(self, roi_ratio=0.2, min_pixels=15, init_lane_width_ratio=0.7,
                 near_reach_ratio=0.1, min_y_span_ratio=0.05,
                 width_alpha=0.1, history_reset_frames=15,
                 wide_mask_ratio=0.6, bottom_band_ratio=0.10, horiz_aspect=2.5):
        self.horiz_aspect = horiz_aspect
        self.corner_ahead = False                # 이번 프레임에 전방 수평선(ㄱ자 코너)이 보였는지
        self.wide_mask_ratio = wide_mask_ratio
        self.bottom_band_ratio = bottom_band_ratio
        self.roi_ratio = roi_ratio
        self.min_pixels = min_pixels
        self.near_reach_ratio = near_reach_ratio
        self.min_y_span_ratio = min_y_span_ratio
        self.width_alpha = width_alpha
        self.history_reset_frames = history_reset_frames

        self.lane_width = init_lane_width_ratio  # 학습된 차선 폭 (정규화)
        self.prev_left = None                    # 이전 프레임의 좌/우 차선 위치 (정규화)
        self.prev_right = None
        self.lost_frames = 0

    # -----------------------------------------------------------
    # 단일 인스턴스 마스크 -> (근접 x, 추세, 실측 최하단이 화면 하단 근처인지) 또는 None(픽셀 부족)
    # -----------------------------------------------------------
    def _fit_instance(self, instance_mask: np.ndarray, roi_start: int, h: int, w: int):
        roi = instance_mask[roi_start:, :]
        # np.nonzero: roi에서 0이 아닌(=마스크가 칠해진) 픽셀들의 좌표를 (y좌표 배열, x좌표 배열)로 반환
        ys, xs = np.nonzero(roi)
        if len(xs) < self.min_pixels:
            return None

        # 정규화 좌표로 변환 (y=0: 이미지 맨 위/먼 곳, y=1: 이미지 맨 아래/로봇과 가장 가까운 곳)
        ys_norm = (ys + roi_start) / float(h)
        xs_norm = xs / float(w)

        y_span = float(ys_norm.max() - ys_norm.min())
        x_span = float(xs_norm.max() - xs_norm.min())
        if x_span > self.wide_mask_ratio:
            # 세로 차선과 전방 수평선이 합쳐진 넓은 마스크: 회귀가 성립하지 않으므로
            # 마스크 최하단 띠(bottom_band_ratio)의 x 평균만 위치로 쓰고 trend는 무시
            band = ys_norm >= ys_norm.max() - self.bottom_band_ratio
            trend = 0.0
            near_x = float(xs_norm[band].mean())
        elif y_span >= self.min_y_span_ratio:
            a, b = np.polyfit(ys_norm, xs_norm, 1)
            trend = float(a)
            near_x = float(a * 1.0 + b)  # 화면 맨 아래(y_norm=1.0)로 외삽
        else:
            trend = 0.0
            near_x = float(xs_norm.mean())
        near_x = float(np.clip(near_x, 0.0, 1.0))

        # 실측(외삽 아님) 최하단이 이미지 하단에서 너무 멀면 '바로 추종하기엔 불확실'
        y_max_px = int(ys.max())
        gap_px = roi.shape[0] - 1 - y_max_px
        limit_px = self.near_reach_ratio * h
        # 대각선 차선이 화면 좌/우 가장자리로 빠져나가며 끊기는 경우도 '도달'로 인정
        edge_margin = int(0.02 * w)
        touches_side = int(xs.min()) <= edge_margin or int(xs.max()) >= w - 1 - edge_margin
        reaches_bottom = (gap_px <= limit_px) or touches_side

        # 가로로 누운 인스턴스(전방 수평선)는 좌/우 차선 후보에서 제외. 합쳐진 넓은 마스크는 제외하지 않음
        # (매우 넓어도 y폭이 얇으면 세로 차선과 합쳐진 게 아니라 수평선 단독이므로 제외 대상)
        is_horizontal = (x_span > max(y_span, 1e-3) * self.horiz_aspect
                         and (x_span <= self.wide_mask_ratio or y_span < 0.15))

        # 디버그: 어느 조건에서 detected가 False가 되는지 확인용
        print(f'[fit] gap_px={gap_px} limit_px={limit_px:.1f} '
              f'xmin={int(xs.min())} xmax={int(xs.max())} '
              f'near_x={near_x:.3f} trend={trend:.3f} reach={reaches_bottom} horiz={is_horizontal}', flush=True)

        return near_x, trend, reaches_bottom, is_horizontal

    # -----------------------------------------------------------
    # 단독으로 보이는 차선이 왼쪽인지 판정 (이전 프레임 위치와 가까운 쪽)
    # -----------------------------------------------------------
    def _is_left(self, x: float) -> bool:
        if self.prev_left is not None and self.prev_right is not None:
            return abs(x - self.prev_left) <= abs(x - self.prev_right)
        # 이력이 없으면 이미지 중앙 기준
        return x < 0.5

    # -----------------------------------------------------------
    # 차선 인스턴스 마스크 -> (offset, detected, left_detected, right_detected, trend)
    # -----------------------------------------------------------
    def update(self, instance_masks):
        """
        instance_masks: (N, H, W) 크기의, Lane 클래스로 검출된 '개별 인스턴스' 마스크들 (없으면 None).

        반환값: (offset, detected, left_detected, right_detected, trend)
          offset   : -1.0(화면 완전 왼쪽) ~ 1.0(화면 완전 오른쪽), 0.0이 정중앙(도로 한가운데)
          detected : 좌/우 중 최소 한쪽 차선을 신뢰 가능하게(near_reach_ratio 이내) 찾았는지 여부
          left_detected / right_detected : 이번 프레임에 그쪽 차선이 화면 하단 근처까지 실측됐는지
          trend    : 보이는 차선이 멀어질수록 오른쪽(+)/왼쪽(-)으로 휘는 정도 (양쪽 다 보이면 평균).
                     detected가 False여도(코너에서 막 놓친 프레임 포함) 픽셀이 있으면 계산된다.
        """
        fits = []
        self.corner_ahead = False
        if instance_masks is not None and len(instance_masks) > 0:
            h, w = instance_masks.shape[1], instance_masks.shape[2]
            roi_start = int(h * (1 - self.roi_ratio))
            for inst in instance_masks:
                r = self._fit_instance(inst, roi_start, h, w)
                if r is not None:
                    fits.append(r)  # (near_x, trend, reaches_bottom, is_horizontal)

        # 수평선 인스턴스는 좌/우 판정에서 빼고 corner_ahead 신호로만 기록
        self.corner_ahead = any(f[3] for f in fits)
        fits = [f[:3] for f in fits if not f[3]]

        if not fits:
            self.lost_frames += 1
            if self.lost_frames >= self.history_reset_frames:
                self.prev_left = None
                self.prev_right = None
            return 0.0, False, False, False, 0.0
        self.lost_frames = 0

        # 여러 개로 쪼개져 검출된 경우(점선 등) 가까운 것끼리 묶어서 대표값 하나로 합침
        # (같은 쪽 차선 조각들은 서로 차선 폭의 절반보다 가깝다고 가정)
        fits.sort(key=lambda f: f[0])
        gap_threshold = 0.5 * self.lane_width
        clusters = [[fits[0]]]
        for f in fits[1:]:
            if f[0] - clusters[-1][-1][0] < gap_threshold:
                clusters[-1].append(f)
            else:
                clusters.append([f])

        def summarize(cluster):
            near_xs = [f[0] for f in cluster]
            trends = [f[1] for f in cluster]
            reaches = any(f[2] for f in cluster)
            return float(np.mean(near_xs)), float(np.mean(trends)), reaches

        summaries = [summarize(c) for c in clusters]

        if len(summaries) >= 2:
            # 양쪽 다 보임: 가장 왼쪽/가장 오른쪽 묶음을 좌/우 차선으로 보고 진짜 중간점 사용
            left_x, left_trend, left_reach = summaries[0]
            right_x, right_trend, right_reach = summaries[-1]
            measured = right_x - left_x
            if self.MIN_LANE_WIDTH <= measured <= self.MAX_LANE_WIDTH:
                # 차선 폭 이동평균 갱신 (한쪽만 보일 때 반대편 추정에 사용)
                self.lane_width = (1.0 - self.width_alpha) * self.lane_width + self.width_alpha * measured
            lane_center_x = (left_x + right_x) / 2.0
            self.prev_left, self.prev_right = left_x, right_x
            left_detected, right_detected = left_reach, right_reach
            trend = (left_trend + right_trend) / 2.0
        else:
            x, trend, reach = summaries[0]
            if self._is_left(x):
                # 왼쪽만 보임: 오른쪽 차선이 학습된 차선 폭만큼 떨어져 있다고 가정
                lane_center_x = x + self.lane_width / 2.0
                self.prev_left, self.prev_right = x, x + self.lane_width
                left_detected, right_detected = reach, False
            else:
                lane_center_x = x - self.lane_width / 2.0
                self.prev_left, self.prev_right = x - self.lane_width, x
                left_detected, right_detected = False, reach

        offset = (lane_center_x - 0.5) / 0.5
        # np.clip(값, 최소, 최대): 값이 범위를 벗어나면 최소/최대로 잘라냄 (여기선 -1.0~1.0로 제한)
        offset = float(np.clip(offset, -1.0, 1.0))
        detected = left_detected or right_detected
        return offset, detected, left_detected, right_detected, trend


class LaneDetectorNode(Node):
    """
    PC(또는 로봇)에서 실행. <namespace>/camera/image_raw/compressed 를 구독해 YOLO(best.pt, segment)로
    추론하고 결과를 발행한다.
      - <namespace>/lane/debug/compressed  : 마스크/박스를 그린 디버그 영상 (구독자가 있을 때만)
      - <namespace>/crossline/detected     : Crossline 검출 여부 (std_msgs/Bool)
      - <namespace>/lane/center_offset     : 차선 중심 오프셋 -1.0~1.0 (std_msgs/Float32)
      - <namespace>/lane/detected          : offset을 바로 신뢰해도 되는지 여부 (std_msgs/Bool)
      - <namespace>/lane/left_detected     : 왼쪽 차선이 화면 하단 근처까지 실측됐는지 (std_msgs/Bool)
      - <namespace>/lane/right_detected    : 오른쪽 차선이 화면 하단 근처까지 실측됐는지 (std_msgs/Bool)
      - <namespace>/lane/offset_trend      : 보이는 차선이 멀어질수록 휘는 방향/정도 (std_msgs/Float32)
      - <namespace>/lane/corner_ahead      : 전방 수평선(ㄱ자 코너)이 보이는지 (std_msgs/Bool)
    """

    # ===============================================================
    # 초기화 (파라미터 로드, YOLO 모델 로드, 구독자/발행자 등록)
    # ===============================================================
    def __init__(self):
        super().__init__('lane_detector_node')

        # 파라미터 선언 (이름, 기본값)
        
        # (모델변경) self.declare_parameter('model_path', '/home/jinho/dev_ws/pinky_slam_nav/pinky_camera/best.pt')
        self.declare_parameter('model_path', '/home/jinho/dev_ws/pinky_slam_nav/pinky_camera/lane_seg_best.pt')
        self.declare_parameter('imgsz', 640)                           # 추론 해상도. 클수록 정확하지만 느림
        self.declare_parameter('conf', 0.35)                           # YOLO 검출 신뢰도 하한. 낮을수록 많이 잡고 오검출도 늘어남
        self.declare_parameter('device', 'cpu')
        self.declare_parameter('crossline_class_id', 0)
        self.declare_parameter('lane_class_id', 1)
        self.declare_parameter('lane_roi_ratio', 0.4)                  # 화면 하단 몇 %를 볼지
        self.declare_parameter('min_lane_pixels', 15)                  # 이 픽셀 수보다 적은 마스크는 노이즈로 버림
        self.declare_parameter('assumed_half_lane_width_ratio', 0.35)  # 차선 폭 초기 가정값(절반). 이후 자동 학습
        self.declare_parameter('near_reach_ratio', 0.3)                # 이 비율보다 멀리서 끝나는 차선은 detected=False
        self.declare_parameter('min_y_span_ratio', 0.05)               # 회귀 기울기(trend)를 신뢰할 최소 y분산
        self.declare_parameter('lane_width_alpha', 0.1)                # 차선 폭 학습 속도
        self.declare_parameter('history_reset_frames', 15)             # 차선 소실 시 좌/우 이력 초기화까지 프레임 수
        self.declare_parameter('wide_mask_ratio', 0.6)                 # 마스크 x 폭이 화면 폭 대비 이 비율을 넘으면 '수평선 합쳐진 마스크'로 봄
        self.declare_parameter('bottom_band_ratio', 0.10)              # 넓은 마스크는 최하단 이 비율(화면 높이 기준) 띠의 x 평균으로 위치 계산
        self.declare_parameter('horiz_aspect', 2.5)                    # 마스크 x폭이 y폭의 이 배수를 넘으면 수평선으로 보고 좌/우 차선에서 제외
        self.declare_parameter('debug_image', True)
        self.declare_parameter('jpeg_quality', 80)

        # 파라미터 값 읽기
        self.model_path = self.get_parameter('model_path').value
        self.imgsz = self.get_parameter('imgsz').value
        self.conf = self.get_parameter('conf').value
        self.device = self.get_parameter('device').value
        self.crossline_class_id = self.get_parameter('crossline_class_id').value
        self.lane_class_id = self.get_parameter('lane_class_id').value
        self.lane_roi_ratio = self.get_parameter('lane_roi_ratio').value
        self.min_lane_pixels = self.get_parameter('min_lane_pixels').value
        self.assumed_half_lane_width_ratio = self.get_parameter('assumed_half_lane_width_ratio').value
        self.near_reach_ratio = self.get_parameter('near_reach_ratio').value
        self.min_y_span_ratio = self.get_parameter('min_y_span_ratio').value
        self.lane_width_alpha = self.get_parameter('lane_width_alpha').value
        self.history_reset_frames = self.get_parameter('history_reset_frames').value
        self.wide_mask_ratio = self.get_parameter('wide_mask_ratio').value
        self.bottom_band_ratio = self.get_parameter('bottom_band_ratio').value
        self.horiz_aspect = self.get_parameter('horiz_aspect').value
        self.debug_image = self.get_parameter('debug_image').value
        self.jpeg_quality = self.get_parameter('jpeg_quality').value

        # 좌/우 차선 추적기 (프레임 간 좌/우 위치와 차선 폭을 기억, 회귀로 offset/trend 계산)
        self.tracker = LaneTracker(
            roi_ratio=self.lane_roi_ratio,
            min_pixels=self.min_lane_pixels,
            init_lane_width_ratio=2.0 * self.assumed_half_lane_width_ratio,
            near_reach_ratio=self.near_reach_ratio,
            min_y_span_ratio=self.min_y_span_ratio,
            width_alpha=self.lane_width_alpha,
            history_reset_frames=self.history_reset_frames,
            wide_mask_ratio=self.wide_mask_ratio,
            bottom_band_ratio=self.bottom_band_ratio,
            horiz_aspect=self.horiz_aspect,
        )

        # ultralytics(torch)는 import가 무거우므로 노드 생성 시점에 import
        from ultralytics import YOLO
        self.model = YOLO(self.model_path)

        # 추론이 느려도 지연이 쌓이지 않도록 최신 프레임 1개만 유지
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)

        # 구독자 / 발행자
        self.sub = self.create_subscription(
            CompressedImage, 'camera/image_raw/compressed', self.image_callback, qos)
        self.debug_pub = self.create_publisher(
            CompressedImage, 'lane/debug/compressed', 10)
        self.crossline_pub = self.create_publisher(Bool, 'crossline/detected', 10)
        self.offset_pub = self.create_publisher(Float32, 'lane/center_offset', 10)
        self.lane_detected_pub = self.create_publisher(Bool, 'lane/detected', 10)
        self.left_detected_pub = self.create_publisher(Bool, 'lane/left_detected', 10)
        self.right_detected_pub = self.create_publisher(Bool, 'lane/right_detected', 10)
        self.trend_pub = self.create_publisher(Float32, 'lane/offset_trend', 10)
        self.corner_pub = self.create_publisher(Bool, 'lane/corner_ahead', 10)

        # 실행 중 파라미터 변경(rqt, ros2 param set)을 즉시 반영 (model_path, device는 제외)
        self.add_on_set_parameters_callback(self._on_params)

        self.get_logger().info(
            f'모델 로드 완료: {self.model_path} (task={self.model.task}, '
            f'classes={self.model.names}) {self.sub.topic_name} 구독 중')

    # ===============================================================
    # 파라미터 변경 콜백: 노드 속성 / LaneTracker 속성에 새 값을 바로 반영
    # ===============================================================
    # 파라미터 이름 -> 노드(self) 속성 이름
    _NODE_PARAMS = {
        'imgsz': 'imgsz',
        'conf': 'conf',
        'crossline_class_id': 'crossline_class_id',
        'lane_class_id': 'lane_class_id',
        'debug_image': 'debug_image',
        'jpeg_quality': 'jpeg_quality',
    }
    # 파라미터 이름 -> LaneTracker 속성 이름
    _TRACKER_PARAMS = {
        'lane_roi_ratio': 'roi_ratio',
        'min_lane_pixels': 'min_pixels',
        'near_reach_ratio': 'near_reach_ratio',
        'min_y_span_ratio': 'min_y_span_ratio',
        'lane_width_alpha': 'width_alpha',
        'history_reset_frames': 'history_reset_frames',
        'wide_mask_ratio': 'wide_mask_ratio',
        'bottom_band_ratio': 'bottom_band_ratio',
        'horiz_aspect': 'horiz_aspect',
    }
    # 0 초과 1 이하여야 하는 비율 파라미터 (범위를 벗어난 값은 거부)
    _RATIO_PARAMS = {'lane_roi_ratio', 'near_reach_ratio', 'wide_mask_ratio',
                     'bottom_band_ratio', 'lane_width_alpha',
                     'assumed_half_lane_width_ratio', 'conf'}
    _POSITIVE_PARAMS = {'horiz_aspect'}

    def _on_params(self, params):
        # 1) 먼저 전부 검증 (하나라도 잘못되면 아무것도 반영하지 않음)
        for p in params:
            if p.name in self._RATIO_PARAMS and not (0.0 < float(p.value) <= 1.0):
                return SetParametersResult(
                    successful=False, reason=f'{p.name}는 0 초과 1 이하여야 합니다: {p.value}')
            if p.name in self._POSITIVE_PARAMS and not float(p.value) > 0.0:
                return SetParametersResult(
                    successful=False, reason=f'{p.name}는 0보다 커야 합니다: {p.value}')
        # 2) 검증 통과 후 반영
        for p in params:
            if p.name in self._NODE_PARAMS:
                setattr(self, self._NODE_PARAMS[p.name], p.value)
            elif p.name in self._TRACKER_PARAMS:
                setattr(self.tracker, self._TRACKER_PARAMS[p.name], p.value)
                setattr(self, p.name, p.value)
            elif p.name == 'assumed_half_lane_width_ratio':
                # 학습된 차선 폭을 새 가정값으로 다시 시작
                self.tracker.lane_width = 2.0 * float(p.value)
                self.assumed_half_lane_width_ratio = p.value
            elif p.name in ('model_path', 'device'):
                self.get_logger().warn(f'{p.name}는 실행 중 변경이 반영되지 않습니다(재시작 필요)')
                continue
            else:
                continue
            self.get_logger().info(f'파라미터 반영: {p.name} = {p.value}')
        return SetParametersResult(successful=True)

    # ===============================================================
    # 메인 콜백 (이미지 수신 -> YOLO 추론 -> crossline/offset/디버그영상 발행)
    # ===============================================================
    def image_callback(self, msg):
        # 1. JPEG -> OpenCV 이미지
        jpeg_bytes = np.frombuffer(msg.data, np.uint8)
        frame = cv2.imdecode(jpeg_bytes, cv2.IMREAD_COLOR)
        if frame is None:
            self.get_logger().warn('JPEG 디코딩 실패, 프레임을 건너뜁니다')
            return

        # 2. YOLO 추론
        results = self.model.predict(
            frame, imgsz=self.imgsz, conf=self.conf, device=self.device, verbose=False)
        result = results[0]

        # ---- 임시 디버그: 1초마다 실시간 검출 상태 출력 ----
        self.get_logger().info(
            f'검출 클래스: {result.boxes.cls.tolist()}, '
            f'masks 있음: {result.masks is not None}, '
            f'conf: {result.boxes.conf.tolist() if result.boxes is not None else []}',
            throttle_duration_sec=1.0)
        # --------------------------------------------

        # 3. Crossline 검출 여부 발행
        detected_classes = result.boxes.cls.tolist()
        crossline_detected = self.crossline_class_id in detected_classes
        self.crossline_pub.publish(Bool(data=crossline_detected))

        # 4. Lane 중심 오프셋 계산 및 발행 (좌/우 개별 검출 여부 + 추세도 함께 발행)
        offset, lane_detected, left_detected, right_detected, trend = self.compute_lane_offset(result)
        self.offset_pub.publish(Float32(data=offset))
        self.lane_detected_pub.publish(Bool(data=lane_detected))
        self.left_detected_pub.publish(Bool(data=left_detected))
        self.right_detected_pub.publish(Bool(data=right_detected))
        self.trend_pub.publish(Float32(data=trend))
        self.corner_pub.publish(Bool(data=self.tracker.corner_ahead))

        # 5. 디버그 영상 발행
        self.publish_debug_image(msg, result)

    # ===============================================================
    # Lane 인스턴스 좌/우 분리(추적) -> 중간점 오프셋 계산
    # ===============================================================
    def compute_lane_offset(self, result):
        # 차선이 없는 경우도 tracker.update(None)로 넘겨 소실 프레임 수를 세게 함
        if result.masks is None or result.boxes is None:
            return self.tracker.update(None)

        cls_ids = result.boxes.cls.cpu().numpy().astype(int)
        masks_np = result.masks.data.cpu().numpy()  # (N, H, W), 모델에 따라 원본과 크기가 다를 수 있음

        # np.where(조건)[0]: cls_ids 중 조건(lane 클래스)을 만족하는 원소들의 인덱스만 배열로 반환
        lane_indices = np.where(cls_ids == self.lane_class_id)[0]
        if len(lane_indices) == 0:
            return self.tracker.update(None)

        # 인스턴스를 합치지 않고 그대로 넘긴다 (좌/우 차선을 분리해서 보기 위함)
        # masks_np[lane_indices]: lane 인스턴스들만 골라냄 -> 0.5 초과 여부로 0/1 이진 마스크로 변환
        lane_instance_masks = (masks_np[lane_indices] > 0.5).astype(np.uint8)

        # 마스크 해상도가 원본 프레임과 다르면 정규화된 offset 계산에는 영향 없음
        # (tracker는 mask 자체의 W, H를 기준으로 정규화하기 때문)
        return self.tracker.update(lane_instance_masks)

    # ===============================================================
    # 디버그 영상 발행 (검출 결과를 그린 이미지를 JPEG로 인코딩해 publish)
    # ===============================================================
    def publish_debug_image(self, msg, result):
        # 디버그 옵션이 꺼져 있거나 구독자가 없으면 plot/JPEG 인코딩을 생략해 CPU 절약
        if not self.debug_image:
            return
        if self.debug_pub.get_subscription_count() == 0:
            return

        plotted = result.plot()
        ok, buf = cv2.imencode(
            '.jpg', plotted, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            self.get_logger().warn('디버그 JPEG 인코딩 실패')
            return

        out = CompressedImage()
        out.header = msg.header
        out.format = 'jpeg'
        out.data = buf.tobytes()
        self.debug_pub.publish(out)


# ===============================================================
# 엔트리 포인트 (노드 생성 후 rclpy.spin으로 실행, 종료 시 정리)
# ===============================================================
def main(args=None):
    rclpy.init(args=args)

    # 노드 생성 (모델 파일 없음/손상, ultralytics 미설치 등이면 실패)
    try:
        node = LaneDetectorNode()
    except Exception as e:
        print(f'[lane_detector_node] 노드를 시작할 수 없습니다: {e}', file=sys.stderr)
        rclpy.shutdown()
        sys.exit(1)

    # 실행
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()