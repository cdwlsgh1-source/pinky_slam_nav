"""AlarmManager 단위 테스트: 생성, 묶기(count), 해소, 이벤트 -> 알람 규칙, 비상정지 보호. 실행: .venv/bin/python -m pytest tests -q"""
import pytest

from backend.alarms import ACTIVE, CATALOG, CLEARED, LEVELS, PROTECTED, AlarmManager, command_text, is_protected


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(max_alarms=200):
    c = Clock()
    return AlarmManager(now=c, max_alarms=max_alarms), c


def active(a, code=None, robot=None):
    return [x for x in a.snapshot() if x['state'] == ACTIVE and (code is None or x['code'] == code) and (robot is None or x['robot'] == robot)]


def test_catalog_levels_are_known():
    assert all(level in LEVELS for level, _ in CATALOG.values())
    assert set(PROTECTED) <= set(CATALOG)


def test_raise_creates_active_alarm_and_message():
    a, c = make()
    msgs = a.raise_('ROBOT_OFFLINE', 'pinky1')
    assert len(msgs) == 1 and msgs[0]['type'] == 'alarm'
    al = msgs[0]['alarm']
    assert al['code'] == 'ROBOT_OFFLINE' and al['level'] == 'H' and al['robot'] == 'pinky1'
    assert al['text'] == 'pinky1 is offline' and al['state'] == ACTIVE and al['count'] == 1
    assert al['t_raised'] == 1000.0 and al['t_cleared'] is None


def test_unknown_code_is_rejected():
    a, _ = make()
    with pytest.raises(ValueError):
        a.raise_('NOPE', 'pinky1')


def test_repeat_is_grouped_by_count():
    a, c = make()
    a.raise_('CMD_REJECTED', 'pinky1', cmd='goto P9', reason='unknown point')
    c.t += 5
    msgs = a.raise_('CMD_REJECTED', 'pinky1', cmd='goto P9', reason='unknown point')
    assert len(a.snapshot()) == 1
    al = msgs[0]['alarm']
    assert al['count'] == 2 and al['t_raised'] == 1000.0 and al['t_last'] == 1005.0


def test_same_code_other_robot_is_separate():
    a, _ = make()
    a.raise_('ROBOT_OFFLINE', 'pinky1')
    a.raise_('ROBOT_OFFLINE', 'pinky2')
    assert len(active(a, 'ROBOT_OFFLINE')) == 2


def test_clear_and_reraise_makes_new_row():
    a, c = make()
    a.raise_('ROBOT_OFFLINE', 'pinky1')
    c.t += 3
    msgs = a.clear('ROBOT_OFFLINE', 'pinky1')
    assert msgs[0]['alarm']['state'] == CLEARED and msgs[0]['alarm']['t_cleared'] == 1003.0
    assert a.clear('ROBOT_OFFLINE', 'pinky1') == []   # 이미 해소됨
    a.raise_('ROBOT_OFFLINE', 'pinky1')
    assert len(a.snapshot()) == 2 and len(active(a)) == 1
    assert a.snapshot()[0]['id'] > a.snapshot()[1]['id']   # 최신이 앞


def test_info_rows_are_records_not_grouped():
    a, _ = make()
    a.raise_('CMD_SENT', 'pinky1', cmd='start')
    a.raise_('CMD_SENT', 'pinky1', cmd='start')
    rows = a.snapshot()
    assert len(rows) == 2 and all(r['state'] == CLEARED and r['level'] == 'info' for r in rows)


def test_template_uses_allowed_keys_only_and_trims_empty_reason():
    a, _ = make()
    al = a.raise_('PATROL_FAILED', 'pinky1', reason='', evil='{x}')[0]['alarm']
    assert al['text'] == 'Patrol failed on pinky1'
    al = a.raise_('CMD_SEND_FAILED', 'pinky2', cmd='start', reason='구독자 없음')[0]['alarm']
    assert al['text'] == 'Command `start` could not be sent to pinky2: 구독자 없음'


def test_robot_online_transitions():
    a, _ = make()
    a.robot_online('pinky1', False)
    assert len(active(a, 'ROBOT_OFFLINE')) == 1
    msgs = a.robot_online('pinky1', True)
    assert not active(a, 'ROBOT_OFFLINE')
    assert [m['alarm']['code'] for m in msgs] == ['ROBOT_OFFLINE', 'ROBOT_ONLINE']


def test_patrol_states():
    a, _ = make()
    a.patrol('pinky1', 'FAILED', 'not allowed: [P3]')
    assert active(a, 'PATROL_FAILED')[0]['text'] == 'Patrol failed on pinky1: not allowed: [P3]'
    a.patrol('pinky1', 'RETRY', 'again')     # FAILED 는 해소되고 RETRY 가 활성
    assert not active(a, 'PATROL_FAILED') and len(active(a, 'PATROL_RETRY')) == 1
    a.patrol('pinky1', 'STOPPED')
    assert not active(a, 'PATROL_RETRY') and len(active(a, 'PATROL_STOPPED')) == 1
    assert a.patrol('pinky1', 'MOVING') and not active(a)      # 다른 상태로 바뀌면 모두 해소
    assert a.patrol('pinky1', 'MOVING') == []                   # 해소할 것이 없으면 메시지도 없다


def test_patrol_same_failed_again_counts():
    a, _ = make()
    a.patrol('pinky1', 'FAILED', 'x')
    a.patrol('pinky1', 'FAILED', 'x')
    assert active(a, 'PATROL_FAILED')[0]['count'] == 2


def test_command_failures_clear_on_new_send_or_ack():
    a, _ = make()
    a.command_rejected('pinky1', 'goto', ['P9'], 'unknown point')
    a.command_no_response('pinky1', 'start')
    a.command_send_failed('pinky1', 'start', None, 'no subscriber')
    assert len(active(a)) == 3
    a.command_acknowledged('pinky1')            # 응답했으면 무응답·거절은 해소, 전송 실패는 새 명령이 나가야 해소
    assert [x['code'] for x in active(a)] == ['CMD_SEND_FAILED']
    a.command_sent('pinky1', 'start')
    assert not active(a)


def test_command_failures_are_per_robot():
    a, _ = make()
    a.command_no_response('pinky1', 'start')
    a.command_sent('pinky2', 'start')
    assert len(active(a, 'CMD_NO_RESPONSE', 'pinky1')) == 1


def test_estop_outcomes():
    a, _ = make()
    a.estop('pinky1', False, False, 'both down')
    assert active(a)[0]['code'] == 'ESTOP_FAILED' and active(a)[0]['level'] == 'HH'
    a.estop('pinky1', True, False, 'cmd_vel')
    assert [x['code'] for x in active(a)] == ['ESTOP_PARTIAL']
    a.estop('pinky1', True, True)
    assert not active(a)
    assert a.snapshot()[0]['text'] == 'Command `estop` sent to pinky1'


def test_estop_codes_are_protected():
    assert is_protected('ESTOP_FAILED') and is_protected('ESTOP_PARTIAL') and not is_protected('ROBOT_OFFLINE')


def test_trim_drops_cleared_first():
    a, _ = make(max_alarms=3)
    a.raise_('ROBOT_OFFLINE', 'a')
    a.raise_('ROBOT_OFFLINE', 'b')
    a.clear('ROBOT_OFFLINE', 'a')
    a.raise_('ROBOT_OFFLINE', 'c')
    a.raise_('ROBOT_OFFLINE', 'd')
    robots = {x['robot'] for x in a.snapshot()}
    assert len(a.snapshot()) == 3 and robots == {'b', 'c', 'd'}   # 해소된 a 가 먼저 사라진다


def test_command_text():
    assert command_text('goto', ['P2', 'P3']) == 'goto P2 P3' and command_text('start') == 'start' and command_text(None) == '?'
