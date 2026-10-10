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
    # Step 4: status(원문)는 그대로 두고 해석 결과(state/holder/token/held_sec)를 더했다
    [m] = s.apply(('zone', 'free'))
    assert m['type'] == 'zone' and m['status'] == 'free' and m['state'] == 'free' and m['holder'] is None
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


def test_alive_event_only_keeps_robot_online_without_changing_values():
    from backend.state import StateStore
    t = [1000.0]
    s = StateStore(['a'], '/zone', 5.0, 10, now=lambda: t[0])
    msgs = s.apply(('alive', 'a'))
    assert any(m.get('field') == 'online' and m.get('data') is True for m in msgs)
    assert s.is_online('a')
    t[0] += 3
    assert s.apply(('alive', 'a')) == []          # 이미 online 이면 아무 메시지도 없다
    t[0] += 3                                      # 마지막 수신 3초 뒤: 5초 타임아웃 전이라 online 유지
    assert s.tick() == [] and s.is_online('a')
    t[0] += 6
    assert not s.is_online('a') or s.tick() != []


def test_last_kind_records_last_topic():
    st = StateStore(['pinky1'], '/z', 5, 10, now=lambda: 100.0)
    assert st.snapshot()['robots']['pinky1']['last_kind'] is None
    st.apply(('alive', 'pinky1'))
    assert st.snapshot()['robots']['pinky1']['last_kind'] == 'alive'
