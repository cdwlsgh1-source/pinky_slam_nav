"""LiDAR 변환: 점 감소, 빈도 제한, 극좌표 -> 지도 좌표."""
import math

import pytest

from backend.scan import MAX_POINTS, RateLimiter, compact, to_world

INF = float('inf')


def test_compact_keeps_every_nth_and_scales_increment():
    am, inc, rs, lo, hi = compact(-1.0, 0.01, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0], 3, 0.1, 8.0)
    assert (am, rs, lo, hi) == (-1.0, [1.0, 4.0, 7.0], 0.1, 8.0)
    assert inc == pytest.approx(0.03)                    # 점을 건너뛴 만큼 각도 간격이 커진다


def test_compact_step_is_at_least_one():
    assert compact(0, 0.1, [1.0, 2.0], 0, 0, 1)[2] == [1.0, 2.0]


def test_rate_limiter_caps_frequency():
    t = [0.0]
    lim = RateLimiter(5, now=lambda: t[0])               # 5Hz -> 0.2초 간격
    allowed = []
    for _ in range(100):                                  # 50Hz 로 2초간 들어옴
        allowed.append(lim.allow())
        t[0] += 0.02
    assert 9 <= sum(allowed) <= 11


def test_to_world_known_geometry():
    # 로봇이 (1, 2) 에서 +y 방향(yaw=90도)을 본다. 정면(각도 0) 1 m 앞은 (1, 3), 왼쪽(+90도) 2 m 은 (-1, 2)
    pose = {'x': 1.0, 'y': 2.0, 'yaw': math.pi / 2}
    pts = to_world(pose, 0.0, math.pi / 2, [1.0, 2.0], 0.05, 10.0)
    assert pts == [[1.0, 3.0], [-1.0, 2.0]]


def test_to_world_drops_invalid_ranges():
    pose = {'x': 0.0, 'y': 0.0, 'yaw': 0.0}
    rs = [INF, float('nan'), 0.01, 0.0, -1.0, 99.0, 1.0, None, 'x']
    pts = to_world(pose, 0.0, 0.1, rs, 0.05, 12.0)       # 정상은 1.0 하나뿐
    assert len(pts) == 1 and pts[0][0] == pytest.approx(math.cos(0.6), abs=1e-3)


def test_to_world_rounds_to_mm_and_serializes():
    import json
    pts = to_world({'x': 0.12345, 'y': 0.0, 'yaw': 0.0}, 0.0, 0.0, [1.0], 0.0, 5.0)
    assert pts == [[1.123, 0.0]]
    json.dumps(pts, allow_nan=False)


def test_to_world_bad_header_values_do_not_crash():
    pts = to_world({'x': 0, 'y': 0, 'yaw': 0}, float('nan'), None, [1.0], float('nan'), float('inf'))
    assert pts == [[1.0, 0.0]]                            # 헤더 값이 이상하면 0/기본값으로 처리


def test_to_world_caps_point_count():
    pts = to_world({'x': 0, 'y': 0, 'yaw': 0}, 0.0, 0.001, [1.0] * (MAX_POINTS * 3), 0.0, 5.0)
    assert len(pts) == MAX_POINTS
