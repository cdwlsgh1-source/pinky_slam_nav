"""StateStore 단위 테스트 (ROS, 네트워크 불필요). 실행: .venv/bin/python -m pytest tests -q"""
import json
import math

from backend.state import StateStore, quat_to_yaw


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make():
    c = Clock()
    return StateStore(['a', 'b'], '/zone', 5.0, 10, now=c), c


def test_yaw_from_quaternion():
    assert abs(quat_to_yaw(0, 0, 0, 1)) < 1e-9
    assert abs(quat_to_yaw(0, 0, math.sin(math.pi / 4), math.cos(math.pi / 4)) - math.pi / 2) < 1e-9


def test_invalid_json_keeps_server_and_raw_in_detail():
    s, _ = make()
    s.apply(('patrol', 'a', json.dumps({'state': 'MOVING', 'waypoint': 2, 'detail': '', 'time': 1.0})))
    for raw in ['not json', '[1,2]', '', '{"state":']:
        out = s.apply(('patrol', 'a', raw))
        assert any(m.get('field') == 'patrol' for m in out)
        p = s.snapshot()['robots']['a']['patrol']
        assert p['detail'] == raw and p['state'] == 'MOVING' and p['waypoint'] == -1


def test_online_by_any_topic_and_timeout():
    s, c = make()
    assert s.snapshot()['robots']['a']['online'] is False
    out = s.apply(('battery', 'a', 0.5, 7.9))
    assert {'type': 'robot_update', 'robot': 'a', 'field': 'online', 'data': True} in out
    c.t += 4.9
    assert s.tick() == []
    c.t += 0.2  # 5.1초
    out = s.tick()
    assert out == [{'type': 'robot_update', 'robot': 'a', 'field': 'online', 'data': False}]
    assert s.snapshot()['robots']['b']['online'] is False  # 한 번도 안 온 로봇은 그대로


def test_pose_rate_limited_but_latest_kept_and_trailing_flushed():
    s, c = make()
    sent = 0
    for i in range(100):  # 100Hz 로 1초
        out = s.apply(('pose', 'a', float(i), 0.0, 0.0))
        sent += sum(1 for m in out if m.get('field') == 'pose')
        out = s.tick()
        sent += sum(1 for m in out if m.get('field') == 'pose')
        c.t += 0.01
    assert sent <= 11
    assert s.snapshot()['robots']['a']['pose']['x'] == 99.0  # 최신값은 항상 반영
    c.t += 0.2
    out = s.tick()  # 보류된 마지막 pose 가 나가 있어야 한다 (이미 위 루프의 tick 에서 나갔다면 비어도 됨)
    assert all(m['field'] in ('pose', 'online') for m in out)


def test_zone_and_nan():
    s, _ = make()
    assert s.apply(('zone', 'free')) == [{'type': 'zone', 'status': 'free'}]
    assert s.apply(('zone', 'free')) == []
    s.apply(('battery', 'a', float('nan'), 7.9))
    json.dumps(s.snapshot(), allow_nan=False)  # NaN 이 남아 있으면 예외


def test_battery_parts_merge():
    s, _ = make()
    s.apply(('battery', 'a', 80.0, None))
    assert s.snapshot()['robots']['a']['battery'] == {'percentage': 80.0, 'voltage': None}
    out = s.apply(('battery', 'a', None, 7.9))
    assert s.snapshot()['robots']['a']['battery'] == {'percentage': 80.0, 'voltage': 7.9}
    assert any(m.get('field') == 'battery' for m in out)
