"""명령 검증, 이력, 응답 추적, 설정 검증 단위 테스트 (ROS 불필요). 실행: .venv/bin/python -m pytest tests -q"""
import pytest
import yaml

from backend.commands import (ACKNOWLEDGED, FAILED, NO_RESPONSE, REJECTED, SENT, CommandRejected,
                              CommandTracker, validate)
from backend.config import DEFAULT_CONFIG, RobotSettings, load_config

CFG = load_config()
S1 = CFG.robot_settings['pinky1']
S2 = CFG.robot_settings['pinky2']


def ok(body, s=S1):
    return validate(s, CFG.max_goto_points, body)


def bad(body, s=S1):
    with pytest.raises(CommandRejected) as e:
        ok(body, s)
    return str(e.value)


# ---- validate ----
def test_start_stop_pass():
    assert ok({'cmd': 'start'}) == ('start', [], 'start')
    assert ok({'cmd': 'stop', 'points': []}) == ('stop', [], 'stop')


def test_start_stop_reject_points():
    bad({'cmd': 'start', 'points': ['P2']})
    bad({'cmd': 'stop', 'points': ['P2']})


def test_goto_builds_command_string():
    assert ok({'cmd': 'goto', 'points': ['P2', 'P3', 'P6']}) == ('goto', ['P2', 'P3', 'P6'], 'goto:P2,P3,P6')


def test_goto_normalizes_case_and_space():
    assert ok({'cmd': 'goto', 'points': [' p2 ', 'p3']})[2] == 'goto:P2,P3'


@pytest.mark.parametrize('points', [
    ['RED1IN'], ['red1out'], ['P2', 'RED2IN'], ['RED2OUT'],      # 구역 문은 항상 거절
    ['P9'], ['X'], [''], ['P'],                                    # 알 수 없는 지점
    ['P2,RED1IN'], ['P2 P3'], ['P2\nP3'], ['goto:P2'], [':'],      # 한 항목에 구분자를 섞은 주입
    [1], [None], [['P2']], [{'a': 1}],                             # 문자열이 아님
    [],                                                            # 비어 있음
])
def test_goto_rejects(points):
    bad({'cmd': 'goto', 'points': points})


def test_goto_points_not_list():
    bad({'cmd': 'goto', 'points': 'P2'})
    bad({'cmd': 'goto'})
    bad({'cmd': 'goto', 'points': None})


def test_goto_max_points():
    ok({'cmd': 'goto', 'points': ['P2'] * CFG.max_goto_points})
    bad({'cmd': 'goto', 'points': ['P2'] * (CFG.max_goto_points + 1)})


@pytest.mark.parametrize('body', [None, 'start', ['start'], 1, {}, {'cmd': 'STARTX'}, {'cmd': 'Start'},
                                  {'cmd': None}, {'cmd': ['start']}, {'cmd': 'goto:P2'}, {'cmd': 'cmd_vel'}])
def test_bad_body_or_cmd(body):
    bad(body)


def test_allowed_set_is_per_robot():
    narrow = RobotSettings(('P1', 'P2'), 'P1', 10, True, ())
    assert ok({'cmd': 'goto', 'points': ['P2']}, narrow)[2] == 'goto:P2'
    assert 'P3' in bad({'cmd': 'goto', 'points': ['P3']}, narrow)


def test_door_rejected_even_if_config_allows_it():
    sneaky = RobotSettings(('P1', 'RED1IN'), 'P1', 10, True, ())  # load_config 는 이런 설정을 막지만 이중 방어
    bad({'cmd': 'goto', 'points': ['RED1IN']}, sneaky)


def test_real_config_has_no_door_and_home_allowed():
    for s in CFG.robot_settings.values():
        assert not any(n.upper().startswith('RED') for n in s.goto_allowed)
        assert s.home in s.goto_allowed
    assert S1.home == 'P1' and S2.home == 'P7'


# ---- tracker ----
class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def tracker(size=50):
    c = Clock()
    return CommandTracker(['a', 'b'], size, 5.0, now=c), c


def results(t):
    return [e['result'] for e in t.history()]


def test_ack_on_patrol_change():
    t, c = tracker()
    e, msgs = t.sent('a', 'start', [], 'start')
    assert e['result'] == SENT
    assert [m['type'] for m in msgs] == ['command', 'robot_update', 'robot_update']
    assert [m['field'] for m in msgs[1:]] == ['last_command', 'last_task'] and msgs[1]['robot'] == 'a'
    msgs = t.on_patrol('a', 'STARTING')
    assert results(t) == [ACKNOWLEDGED]
    assert msgs[0]['entry']['result'] == ACKNOWLEDGED
    assert t.on_patrol('a', 'STARTING') == []  # 한 번만


def test_ack_needs_matching_state():
    t, c = tracker()
    t.sent('a', 'start', [], 'start')
    assert t.on_patrol('a', 'MOVING') == []  # 작업 중이라 무시된 start 가 응답한 것으로 보이면 안 된다
    assert t.on_patrol('a', 'ARRIVED') == []
    assert results(t) == [SENT]
    c.t += 6
    t.tick()
    assert results(t) == [NO_RESPONSE]


def test_stop_acked_by_stopped_only_and_goto_by_starting():
    t, c = tracker()
    t.sent('a', 'stop', [], 'stop')
    assert t.on_patrol('a', 'STARTING') == [] and t.on_patrol('a', 'DONE') == []
    assert t.on_patrol('a', 'STOPPED')
    t.sent('b', 'goto', ['P2'], 'goto:P2')
    assert t.on_patrol('b', 'FAILED') == []
    assert t.on_patrol('b', 'STARTING')
    assert results(t) == [ACKNOWLEDGED, ACKNOWLEDGED]


def test_ack_only_for_same_robot():
    t, c = tracker()
    t.sent('a', 'start', [], 'start')
    assert t.on_patrol('b', 'STARTING') == []
    assert results(t) == [SENT]


def test_no_response_after_timeout():
    t, c = tracker()
    t.sent('a', 'start', [], 'start')
    c.t += 4.9
    assert t.tick() == []
    c.t += 0.2
    msgs = t.tick()
    assert results(t) == [NO_RESPONSE]
    assert msgs[0]['entry']['result'] == NO_RESPONSE and msgs[1]['field'] == 'last_command'
    assert t.tick() == []
    assert t.on_patrol('a', 'STARTING') == []  # 늦은 응답은 이력을 바꾸지 않는다


def test_new_command_replaces_pending():
    t, c = tracker()
    t.sent('a', 'start', [], 'start')
    t.sent('a', 'stop', [], 'stop')
    c.t += 6
    t.tick()
    assert results(t) == [SENT, NO_RESPONSE]  # 앞의 명령은 더 추적하지 않는다
    assert t.last_command('a')['cmd'] == 'stop'


def test_rejected_does_not_touch_last_command_or_pending():
    t, c = tracker()
    t.sent('a', 'start', [], 'start')
    e, msgs = t.rejected('a', 'goto', ['RED1IN'], '구역 문')
    assert e['result'] == REJECTED and msgs[0]['type'] == 'command' and len(msgs) == 1
    assert t.last_command('a')['cmd'] == 'start'
    t.on_patrol('a', 'STARTING')
    assert results(t) == [ACKNOWLEDGED, REJECTED]


def test_failed_result_and_sanitized_input():
    t, c = tracker()
    e, _ = t.rejected('a', 'x' * 100, ['y' * 100, 5], '사유', result=FAILED)
    assert e['result'] == FAILED and e['cmd'] == '?' and e['points'] == ['?', '?']
    e, _ = t.rejected('a', None, 'not a list', '사유')
    assert e['points'] == []


def test_history_limited():
    t, c = tracker(size=3)
    for _ in range(5):
        t.sent('a', 'start', [], 'start')
    h = t.history()
    assert len(h) == 3 and [e['id'] for e in h] == [3, 4, 5]


# ---- config 검증 ----
def write_cfg(tmp_path, mutate):
    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding='utf-8'))
    raw['points_yaml'] = str((DEFAULT_CONFIG.parent / raw['points_yaml']).resolve())
    raw['map_yaml'] = str((DEFAULT_CONFIG.parent / raw['map_yaml']).resolve())
    mutate(raw)
    p = tmp_path / 'robots.yaml'
    p.write_text(yaml.safe_dump(raw), encoding='utf-8')
    return p


def test_config_rejects_door_in_allowed(tmp_path):
    p = write_cfg(tmp_path, lambda r: r['robot_settings']['pinky1']['goto_allowed'].append('RED1IN'))
    with pytest.raises(ValueError, match='구역 문'):
        load_config(p)


def test_config_rejects_unknown_point_and_bad_home(tmp_path):
    p = write_cfg(tmp_path, lambda r: r['robot_settings']['pinky1']['goto_allowed'].append('P99'))
    with pytest.raises(ValueError):
        load_config(p)
    p = write_cfg(tmp_path, lambda r: r['robot_settings']['pinky1'].update(home='P8'))
    with pytest.raises(ValueError, match='home'):
        load_config(p)


def test_config_robot_settings_keys_must_match(tmp_path):
    p = write_cfg(tmp_path, lambda r: r['robot_settings'].pop('pinky2'))
    with pytest.raises(ValueError, match='robot_settings'):
        load_config(p)


def test_stop_does_not_replace_last_task():
    t, c = tracker()
    t.sent('a', 'goto', ['P2'], 'goto:P2')
    _, msgs = t.sent('a', 'stop', [], 'stop')
    assert t.last_command('a')['cmd'] == 'stop' and t.last_task('a')['cmd'] == 'goto'
    assert [m['field'] for m in msgs if m['type'] == 'robot_update'] == ['last_command']
