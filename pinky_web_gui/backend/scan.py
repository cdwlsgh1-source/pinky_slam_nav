"""LiDAR(/{id}/scan) 오버레이용 변환. rclpy 에 의존하지 않는다.

흐름: rclpy 콜백(제한 빈도, 점 감소) -> ('scan', rid, ...) 이벤트 -> 이벤트 루프에서 지도 좌표로 바꿔
스캔을 켠 브라우저에만 보낸다. 이 파일이 극좌표 -> 지도 좌표 변환식이 있는 유일한 곳이다.

센서의 위치와 방향: 점은 센서 프레임(scan 헤더의 frame_id)의 극좌표라서, 지도에 그리려면 map -> 센서 변환이 필요하다.
센서가 로봇 정면이 아니라 돌려서(예: 180도) 달려 있거나 뒤집혀 있으면 로봇 pose(yaw) 만으로는 방향이 어긋난다.
그래서 로봇이 발행하는 /tf, /tf_static 에서 map -> odom -> base -> 센서 변환을 직접 구한다 (TfTree, 3D 회전으로 합성한다).
이 변환을 못 구하면 amcl_pose + 설정의 보정 각도(yaw_offset_deg)로 그린다 (화면에 어느 쪽인지 표시한다).
"""
import math
import threading
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
    """step 개당 1개만 남긴다 (FR4-5). 반환은 이벤트에 싣는 (angle_min, angle_inc, ranges, range_min, range_max).

    점이 많은 센서에서도 MAX_POINTS 를 넘지 않도록 step 을 키운다. (이전에는 넘는 부분을 잘라 버려서 스캔의 일부 각도만 그려졌다.)
    """
    n = len(ranges)
    step = max(1, int(step), -(-n // MAX_POINTS) if n else 1)
    return angle_min, angle_inc * step, [float(r) for r in ranges[::step]], range_min, range_max


def _finite(v, default):
    return v if isinstance(v, (int, float)) and math.isfinite(v) else default


def to_world(pose, angle_min, angle_inc, ranges, range_min, range_max, yaw_offset=0.0):
    """극좌표 스캔 -> 지도 좌표 [[x, y], ...] (m, mm 단위로 반올림). **tf 를 못 구했을 때의 대체 경로**다.

    pose 는 amcl_pose({x, y, yaw}) 이고 센서가 로봇 중심에서 yaw_offset(rad) 만큼 돌아 있다고 본다 (위치 오프셋은 무시).
    inf/NaN, range_min 미만, range_max 초과 값(= 반사 없음)은 버린다.
    """
    R, t = rot_z(pose['yaw'] + yaw_offset), (pose['x'], pose['y'], 0.0)
    return to_world_tf((R, t), angle_min, angle_inc, ranges, range_min, range_max)


def to_world_tf(tf, angle_min, angle_inc, ranges, range_min, range_max):
    """극좌표 스캔 -> 지도 좌표. tf = (R, t): 센서 프레임 -> map 변환 (3x3 회전, 3-벡터 이동).

    각 빔의 점 (r cos a, r sin a, 0) 을 센서 프레임에서 지도 프레임으로 옮긴 뒤 x, y 만 쓴다.
    센서가 돌려 달려 있거나 뒤집혀 있어도(roll 180도) 3D 회전이 그대로 반영된다.
    """
    R, t = tf
    angle_min = _finite(angle_min, 0.0)
    angle_inc = _finite(angle_inc, 0.0)
    lo = max(0.0, _finite(range_min, 0.0))
    hi = _finite(range_max, DEFAULT_RANGE_MAX)
    pts = []
    for i, r in enumerate(ranges[:MAX_POINTS]):
        if not isinstance(r, (int, float)) or not math.isfinite(r) or r < lo or r > hi or r <= 0:
            continue
        a = angle_min + i * angle_inc
        px, py = r * math.cos(a), r * math.sin(a)
        pts.append([round(R[0][0] * px + R[0][1] * py + t[0], 3), round(R[1][0] * px + R[1][1] * py + t[1], 3)])
    return pts


# ---- 3D 강체 변환 (tf) ----
IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def rot_z(yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))


def quat_to_rot(x, y, z, w):
    """쿼터니언 -> 3x3 회전 행렬. 정규화하고, 영벡터(잘못된 값)는 단위 회전으로 본다."""
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if not n > 1e-9:
        return IDENTITY
    x, y, z, w = x / n, y / n, z / n, w / n
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def compose(a, b):
    """a ∘ b: b 로 옮긴 뒤 a 로 옮긴다. (R, t) 쌍이다."""
    (Ra, ta), (Rb, tb) = a, b
    R = tuple(tuple(sum(Ra[i][k] * Rb[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    t = tuple(sum(Ra[i][k] * tb[k] for k in range(3)) + ta[i] for i in range(3))
    return R, t


def yaw_of(R):
    """회전 행렬의 z축 회전각(rad). 센서 앞 방향(+x)이 지도에서 향하는 각도다."""
    return math.atan2(R[1][0], R[0][0])


class TfTree:
    """로봇이 발행하는 tf(/tf, /tf_static)의 최신 변환을 모아 map -> 임의 프레임 변환을 구한다. 시간 보간은 하지 않는다 (최신값).

    rclpy 스레드(콜백)가 쓰고, 다른 스레드가 읽을 수 있어서 락을 쓴다. 프레임 이름의 앞 '/' 는 무시한다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._parent = {}  # 자식 프레임 -> (부모 프레임, (R, t))

    @staticmethod
    def _name(frame):
        return str(frame).lstrip('/')

    def set(self, parent, child, R, t):
        with self._lock:
            self._parent[self._name(child)] = (self._name(parent), (R, tuple(t)))

    def lookup(self, target, source, max_depth=32):
        """source 프레임의 점을 target 프레임으로 옮기는 (R, t). 이어지는 변환이 없으면 None."""
        target, frame = self._name(target), self._name(source)
        acc = (IDENTITY, (0.0, 0.0, 0.0))
        with self._lock:
            for _ in range(max_depth):
                if frame == target:
                    return acc
                link = self._parent.get(frame)
                if link is None:
                    return None
                acc = compose(link[1], acc)
                frame = link[0]
        return None

    def frames(self):
        with self._lock:
            return sorted(self._parent)
