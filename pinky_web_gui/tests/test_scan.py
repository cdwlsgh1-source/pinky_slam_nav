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


# ---- tf 기반 변환 (센서 장착 방향/위치, 뒤집힘) ----
from backend.scan import IDENTITY, TfTree, compose, quat_to_rot, rot_z, to_world_tf, yaw_of  # noqa: E402


def quat_yaw(yaw):
    return (0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2))


def make_tree(laser_yaw=math.pi, laser_t=(0.1, 0.0, 0.2), map_odom_yaw=math.pi / 2):
    t = TfTree()
    t.set('map', 'odom', quat_to_rot(*quat_yaw(map_odom_yaw)), (1.0, 0.0, 0.0))        # AMCL: map -> odom
    t.set('odom', 'base_footprint', rot_z(0.0), (1.0, 0.0, 0.0))                       # 오도메트리
    t.set('base_footprint', 'base_link', IDENTITY, (0.0, 0.0, 0.01))
    t.set('base_link', 'laser', quat_to_rot(*quat_yaw(laser_yaw)), laser_t)            # 센서 장착
    return t


def test_tf_lookup_composes_chain():
    tf = make_tree()
    R, t = tf.lookup('map', 'laser')
    # map->odom: 90도 회전 + (1,0). odom->base: (1,0). base->laser: 180도 + (0.1,0)
    # 로봇(base)은 map 에서 (1,0)+Rz(90)*(1,0) = (1,1), 방향 90도. 센서는 로봇 앞으로 0.1 m 이므로 (1, 1.1), 방향 270도
    assert (round(t[0], 6), round(t[1], 6), round(t[2], 6)) == (1.0, 1.1, 0.21)
    assert math.isclose(math.cos(yaw_of(R)), math.cos(math.radians(270)), abs_tol=1e-9)
    assert math.isclose(math.sin(yaw_of(R)), -1.0, abs_tol=1e-9)


def test_sensor_mounted_backwards_points_behind_the_robot():
    """센서가 180도 돌아 달려 있으면 센서 앞(각도 0)의 물체는 로봇 '뒤'에 있다. amcl yaw 만 쓰면 반대쪽에 그려진다."""
    tree = make_tree(map_odom_yaw=0.0, laser_t=(0.0, 0.0, 0.0))   # 로봇: map (2, 0), 방향 0
    ranges = [1.0]                                                  # 센서 정면 1 m
    with_tf = to_world_tf(tree.lookup('map', 'laser'), 0.0, 0.0, ranges, 0.0, 10.0)
    assert with_tf == [[1.0, 0.0]]                                  # 로봇(2,0)의 뒤쪽 (x=1)
    amcl_only = to_world({'x': 2.0, 'y': 0.0, 'yaw': 0.0}, 0.0, 0.0, ranges, 0.0, 10.0)
    assert amcl_only == [[3.0, 0.0]]                                # 보정 없이는 앞쪽(x=3) 에 그려지는 방향 오류
    assert to_world({'x': 2.0, 'y': 0.0, 'yaw': 0.0}, 0.0, 0.0, ranges, 0.0, 10.0, yaw_offset=math.pi) == [[1.0, 0.0]]


def test_upside_down_sensor_mirrors_the_scan():
    """센서가 뒤집혀(roll 180도) 달려 있으면 스캔의 좌우가 반대로 된다. 3D 회전이라 자동으로 맞는다."""
    t = TfTree()
    t.set('map', 'laser', quat_to_rot(1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0))   # x축 둘레 180도
    pts = to_world_tf(t.lookup('map', 'laser'), math.pi / 2, 0.0, [1.0], 0.0, 10.0)   # 센서의 +y 방향 1 m
    assert pts == [[0.0, -1.0]]


def test_lookup_missing_link_or_cycle_is_none():
    t = TfTree()
    t.set('odom', 'base', IDENTITY, (0, 0, 0))
    assert t.lookup('map', 'base') is None                  # map -> odom 이 아직 없다
    assert t.lookup('map', 'nowhere') is None
    t.set('a', 'b', IDENTITY, (0, 0, 0))
    t.set('b', 'a', IDENTITY, (0, 0, 0))
    assert t.lookup('map', 'a') is None                     # 순환은 무한 루프 없이 실패


def test_tf_frame_names_ignore_leading_slash_and_latest_wins():
    t = TfTree()
    t.set('/map', '/odom', IDENTITY, (1, 0, 0))
    t.set('map', 'odom', IDENTITY, (5, 0, 0))               # 같은 자식의 새 값이 이긴다
    assert t.lookup('map', 'odom')[1][0] == 5
    assert t.lookup('/map', '/odom') is not None


def test_quat_to_rot_normalizes_and_survives_zero():
    assert quat_to_rot(0, 0, 0, 0) == IDENTITY
    R = quat_to_rot(0, 0, 2 * math.sin(0.3), 2 * math.cos(0.3))   # 정규화되지 않은 입력
    assert math.isclose(yaw_of(R), 0.6, abs_tol=1e-9)


def test_compose_order():
    a = (rot_z(math.pi / 2), (1.0, 0.0, 0.0))
    b = (IDENTITY, (1.0, 0.0, 0.0))
    R, t = compose(a, b)                                    # b 로 (1,0) 이동 후 a 로 90도 회전 + (1,0)
    assert (round(t[0], 9), round(t[1], 9)) == (1.0, 1.0)


def test_compact_never_exceeds_max_points_so_no_angles_are_lost():
    from backend.scan import MAX_POINTS, compact, to_world
    n = MAX_POINTS * 5 + 7
    am, inc, rs, lo, hi = compact(0.0, 2 * math.pi / n, [1.0] * n, 3, 0.0, 10.0)
    assert len(rs) <= MAX_POINTS
    pts = to_world({'x': 0, 'y': 0, 'yaw': 0}, am, inc, rs, lo, hi)
    angles = sorted(math.atan2(y, x) % (2 * math.pi) for x, y in pts)
    assert angles[-1] > 2 * math.pi * 0.95                  # 한쪽 각도만 잘리지 않고 한 바퀴 전체가 남는다
