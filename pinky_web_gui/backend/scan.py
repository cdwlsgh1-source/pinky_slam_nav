"""LiDAR(/{id}/scan) 오버레이용 변환. rclpy 에 의존하지 않는다.

흐름: rclpy 콜백(제한 빈도, 점 감소) -> ('scan', rid, ...) 이벤트 -> 이벤트 루프에서 to_world 로 지도 좌표로 바꿔
스캔을 켠 브라우저에만 보낸다. 이 파일이 극좌표 -> 지도 좌표 변환식이 있는 유일한 곳이다.
"""
import math
import time

MAX_POINTS = 720  # 한 번에 보내는 점의 상한 (이상한 입력이 대역폭을 먹지 않게)
DEFAULT_RANGE_MAX = 30.0


class RateLimiter:
    """최대 max_hz 로 허용한다. 시계는 주입받는다."""

    def __init__(self, max_hz, now=time.monotonic):
        self._interval = 1.0 / max_hz
        self._now = now
        self._last = None

    def allow(self):
        t = self._now()
        if self._last is not None and t - self._last < self._interval:
            return False
        self._last = t
        return True


def compact(angle_min, angle_inc, ranges, step, range_min, range_max):
    """step 개당 1개만 남긴다 (FR4-5). 반환은 이벤트에 싣는 (angle_min, angle_inc, ranges, range_min, range_max)."""
    step = max(1, int(step))
    return angle_min, angle_inc * step, [float(r) for r in ranges[::step]], range_min, range_max


def _finite(v, default):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else default


def to_world(pose, angle_min, angle_inc, ranges, range_min, range_max):
    """극좌표 스캔 -> 지도 좌표 [[x, y], ...] (m, mm 단위로 반올림).

    pose 는 amcl_pose({x, y, yaw}) 이고, 센서와 로봇 중심의 오프셋은 무시한다 (FR4-6).
    inf/NaN, range_min 미만, range_max 초과 값(= 반사 없음)은 버린다.
    """
    x0, y0, yaw = pose['x'], pose['y'], pose['yaw']
    angle_min = _finite(angle_min, 0.0)
    angle_inc = _finite(angle_inc, 0.0)
    lo = max(0.0, _finite(range_min, 0.0))
    hi = _finite(range_max, DEFAULT_RANGE_MAX)
    pts = []
    for i, r in enumerate(ranges[:MAX_POINTS]):
        if not isinstance(r, (int, float)) or not math.isfinite(r) or r < lo or r > hi or r <= 0:
            continue
        a = yaw + angle_min + i * angle_inc
        pts.append([round(x0 + r * math.cos(a), 3), round(y0 + r * math.sin(a), 3)])
    return pts
