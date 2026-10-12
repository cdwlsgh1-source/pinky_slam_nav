"""알람 카탈로그 수정(Step A5) 시험: 권한(401/403), 입력 검증(영문 오류), 되돌리기, 서버 재시작 후 유지, alarms.yaml 불변,
끈 알람이 발생해도 표시·기록되지 않음, 로그 화면 감사 기록, WS 방송. mock 서버를 TestClient 로 띄운다 (ROS 불필요)."""
import json
import re
import struct
import wave

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import alarmcatalog as ac
from backend.alarms import AlarmManager
from backend.auth import Auth
from backend.config import load_config
from backend.main import create_app

CFG = load_config()
ROOT_ALARMS_YAML = CFG.alarms_local_path.replace('alarms.local.yaml', 'alarms.yaml')


@pytest.fixture
def env(tmp_path):
    sounds = tmp_path / 'sounds'
    sounds.mkdir()
    with wave.open(str(sounds / 'a.wav'), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(struct.pack('<8h', *range(8)))
    return {'local': tmp_path / 'alarms.local.yaml', 'hist': tmp_path / 'hist', 'sounds': sounds, 'tmp': tmp_path}


def make_client(env, auth=None):
    app = create_app(CFG, mock=True, auth=auth if auth is not None else Auth(), alarm_dir=str(env['hist']),
                     sound_dir=str(env['sounds']), alarms_local=str(env['local']))
    return TestClient(app)


def login(c, user):
    assert c.post('/api/login', json={'id': user, 'password': user}).status_code == 200
    return c


def put(c, code, body, **kw):
    return c.put(f'/api/alarms/catalog/{code}', json=body, **kw)


def row(c, code):
    return next(r for r in c.get('/api/alarms/catalog').json()['alarms'] if r['code'] == code)


def reject(c, robot='pinky1'):
    assert c.post(f'/api/robots/{robot}/command', json={'cmd': 'bogus'}).status_code == 400


# ---- 권한 (FRA5-1, 0-7 원칙 4) ----
def test_edit_requires_login(env):
    with make_client(env) as c:
        r = put(c, 'CMD_REJECTED', {'level': 'H'})
        assert r.status_code == 401        # 전역 로그인 게이트(문구는 A6 에서 영문화)가 먼저 막는다
        assert c.post('/api/alarms/catalog/CMD_REJECTED/reset', json={}).status_code == 401


def test_operator_gets_403_and_nothing_changes(env):
    with make_client(env) as c:
        login(c, 'oper')
        r = put(c, 'CMD_REJECTED', {'level': 'H'})
        assert r.status_code == 403 and r.json()['error'] == '알람 카탈로그는 manager 만 바꿀 수 있습니다'
        assert c.post('/api/alarms/catalog/CMD_REJECTED/reset', json={}).status_code == 403
        assert row(c, 'CMD_REJECTED')['level'] == 'L'      # 읽기는 된다
        assert c.get('/api/alarms/catalog').json()['can_edit'] is False
        assert not env['local'].exists()


def test_manager_can_edit_and_can_edit_flag(env):
    with make_client(env) as c:
        login(c, 'mngr')
        assert c.get('/api/alarms/catalog').json()['can_edit'] is True
        r = put(c, 'CMD_REJECTED', {'level': 'H'})
        assert r.status_code == 200 and r.json()['alarm']['level'] == 'H' and r.json()['alarm']['modified'] is True


def test_no_auth_server_allows_edit(env):
    with make_client(env, Auth.disabled()) as c:
        assert put(c, 'CMD_REJECTED', {'level': 'H'}).status_code == 200


def test_origin_and_content_type_are_checked(env):
    with make_client(env) as c:
        login(c, 'mngr')
        assert put(c, 'CMD_REJECTED', {'level': 'H'}, headers={'Origin': 'http://evil.example'}).status_code == 403
        assert c.put('/api/alarms/catalog/CMD_REJECTED', content='{"level":"H"}', headers={'Content-Type': 'text/plain'}).status_code == 415
        assert row(c, 'CMD_REJECTED')['level'] == 'L'


# ---- 검증 (FRA5-6, FRA5-7) ----
BAD = [
    ('CMD_REJECTED', {'message': ''}, '메시지는 줄바꿈 없이 1~120글자'),
    ('CMD_REJECTED', {'message': 'x' * 121}, '메시지는 줄바꿈 없이 1~120글자'),
    ('CMD_REJECTED', {'message': 'two\nlines'}, '메시지는 줄바꿈 없이 1~120글자'),
    ('CMD_REJECTED', {'message': '   '}, '메시지는 줄바꿈 없이 1~120글자'),
    ('CMD_REJECTED', {'message': 5}, '메시지는 줄바꿈 없이 1~120글자'),
    ('CMD_REJECTED', {'message': 'bad {nope}'}, '알 수 없는 자리표시자 {nope}'),
    ('ROBOT_OFFLINE', {'message': '{robot} {cmd}'}, '알 수 없는 자리표시자 {cmd}'),    # 기본 문구에 없는 자리표시자
    ('CMD_REJECTED', {'message': '{robot.name}'}, '{이름} 형태'),
    ('CMD_REJECTED', {'message': '{robot[0]}'}, '{이름} 형태'),
    ('CMD_REJECTED', {'message': '{robot:>5}'}, '{이름} 형태'),
    ('CMD_REJECTED', {'message': '{}'}, '{이름} 형태'),
    ('CMD_REJECTED', {'message': 'open { brace'}, '짝이 맞지 않습니다'),
    ('CMD_REJECTED', {'message': 'close } brace'}, '짝이 맞지 않습니다'),
    ('CMD_REJECTED', {'level': 'X'}, '레벨은 HH, H, L, LL, info 중 하나여야 합니다'),
    ('CMD_REJECTED', {'level': 3}, '레벨은'),
    ('CMD_REJECTED', {'enabled': 'no'}, '사용 값은 true 또는 false'),
    ('CMD_REJECTED', {'sound': 1}, '소리 값은 true 또는 false'),
    ('CMD_REJECTED', {'sound_file': '../x.wav'}, '알 수 없는 사운드 파일입니다'),
    ('CMD_REJECTED', {'sound_file': 'nope.wav'}, '알 수 없는 사운드 파일입니다'),
    ('CMD_REJECTED', {'sound_file': 'a.txt'}, '알 수 없는 사운드 파일입니다'),
    ('CMD_REJECTED', {'sound_file': 5}, '알 수 없는 사운드 파일입니다'),
    ('CMD_REJECTED', {'repeat': 0}, '반복 주기는 1~60초'),
    ('CMD_REJECTED', {'repeat': 61}, '반복 주기는 1~60초'),
    ('CMD_REJECTED', {'repeat': '5'}, '반복 주기는 1~60초'),
    ('CMD_REJECTED', {'repeat': True}, '반복 주기는 1~60초'),
    ('CMD_REJECTED', {'repeat': 2.5}, '반복 주기는 1~60초'),
    ('CMD_REJECTED', {'name': 'x'}, '알 수 없는 항목: name'),                  # 이름·조건은 alarms.yaml 에서만
    ('CMD_REJECTED', {'bogus': 1}, '알 수 없는 항목: bogus'),
    ('CMD_REJECTED', {}, '항목이 하나 이상'),
    ('CMD_SENT', {'sound': True}, 'info 알람에는 소리를 켤 수 없습니다'),   # info 는 무음
    ('CMD_REJECTED', {'level': 'info', 'sound': True}, 'info 알람에는 소리를 켤 수 없습니다'),
    ('SERVER_DISCONNECTED', {'message': 'x'}, '이 알람의 메시지는 바꿀 수 없습니다'),  # 브라우저 알람 문구
]


@pytest.mark.parametrize('code,body,expect', BAD)
def test_validation_rejects_with_korean_error_and_changes_nothing(env, code, body, expect):
    with make_client(env) as c:
        login(c, 'mngr')
        before = c.get('/api/alarms/catalog').json()['alarms']
        r = put(c, code, body)
        assert r.status_code == 400, r.text
        assert expect in r.json()['error']
        assert re.search('[가-힣]', r.json()['error']), '오류 문구는 한국어여야 한다'
        assert c.get('/api/alarms/catalog').json()['alarms'] == before
        assert not env['local'].exists()


def test_body_must_be_object_and_code_must_exist(env):
    with make_client(env) as c:
        login(c, 'mngr')
        assert c.put('/api/alarms/catalog/CMD_REJECTED', json=[1]).status_code == 400
        assert c.put('/api/alarms/catalog/CMD_REJECTED', content='not json', headers={'Content-Type': 'application/json'}).status_code == 400
        r = put(c, 'NO_SUCH', {'level': 'H'})
        assert r.status_code == 404 and r.json()['error'] == '알 수 없는 알람 코드입니다'
        assert c.post('/api/alarms/catalog/NO_SUCH/reset', json={}).status_code == 404


def test_message_length_counts_characters_not_bytes(env):
    with make_client(env) as c:
        login(c, 'mngr')
        assert put(c, 'CMD_REJECTED', {'message': '가' * 120}).status_code == 200      # 한글 120자 = 360 바이트여도 통과
        assert put(c, 'CMD_REJECTED', {'message': '가' * 121}).status_code == 400
        assert put(c, 'CMD_REJECTED', {'message': '{robot} 명령 {cmd} 거절: {reason}'}).status_code == 200   # 한영 혼용 허용


def test_valid_edits_apply(env):
    with make_client(env) as c:
        login(c, 'mngr')
        r = put(c, 'CMD_REJECTED', {'message': 'Rejected {cmd} for {robot}', 'level': 'H', 'sound_file': 'a.wav', 'repeat': 3})
        a = r.json()['alarm']
        assert (a['message'], a['level'], a['sound_file'], a['repeat'], a['sound'], a['enabled']) == ('Rejected {cmd} for {robot}', 'H', 'a.wav', 3, True, True)
        assert len(r.json()['changes']) == 4
        reject(c)
        al = c.get('/api/alarms').json()['alarms']
        assert al[0]['text'] == 'Rejected bogus for pinky1' and al[0]['level'] == 'H'


# ---- 보호: 비상정지 계열 (FRA5-4) ----
@pytest.mark.parametrize('code', ac.PROTECTED)
def test_protected_alarms_reject_disable_level_and_sound_off(env, code):
    with make_client(env) as c:
        login(c, 'mngr')
        assert row(c, code)['level'] == 'HH' and row(c, code)['protected'] is True
        bodies = [({'enabled': False}, '이 알람은 끌 수 없습니다'), ({'level': 'H'}, '이 알람의 레벨은 바꿀 수 없습니다'),
                  ({'level': 'info'}, '이 알람의 레벨은 바꿀 수 없습니다')]
        soundable = row(c, code)['soundable']
        if soundable:
            bodies.append(({'sound': False}, '이 알람의 소리는 끌 수 없습니다'))
        for body, expect in bodies:
            r = put(c, code, body)
            assert r.status_code == 400 and expect in r.json()['error'], (body, r.text)
        assert not env['local'].exists()
        ok = {'level': 'HH', 'enabled': True}
        if soundable:
            ok.update({'sound_file': 'a.wav', 'repeat': 2})     # 같은 값과 소리 파일·주기는 괜찮다
        assert put(c, code, ok).status_code == 200


def test_error_messages_have_no_code_prefix(env):
    with make_client(env) as c:
        login(c, 'mngr')
        assert put(c, 'ESTOP_FAILED', {'enabled': False}).json()['error'] == '이 알람은 끌 수 없습니다'
        assert put(c, 'CMD_REJECTED', {'level': 'bogus'}).json()['error'] == '레벨은 HH, H, L, LL, info 중 하나여야 합니다'


def test_one_time_browser_alarms_have_no_sound(env):
    with make_client(env) as c:
        login(c, 'mngr')
        for code in ('DRIVE_DENIED', 'SCAN_PENDING', 'ESTOP_REQUEST_FAILED'):
            a = row(c, code)
            assert a['soundable'] is False and a['sound'] is False, code
            for body in ({'sound': True}, {'sound_file': 'a.wav'}, {'repeat': 3}):
                r = put(c, code, body)
                assert r.status_code == 400 and r.json()['error'] == '이 알람은 한 번 알리고 끝나는 안내라 소리가 없습니다', (code, body)
        assert put(c, 'DRIVE_DENIED', {'level': 'HH', 'enabled': False}).status_code == 200   # Enable/레벨은 가능
        d = row(c, 'SERVER_DISCONNECTED')                                                      # 연결 끊김은 active 상태가 있어 소리 가능
        assert d['soundable'] is True and d['sound'] is True
        assert put(c, 'SERVER_DISCONNECTED', {'sound_file': 'a.wav', 'repeat': 3}).status_code == 200


def test_estop_alarm_still_raises_after_attempts_to_disable(env):
    with make_client(env) as c:
        login(c, 'mngr')
        put(c, 'ESTOP_FAILED', {'enabled': False})
        m = AlarmManager(catalog=ac.effective({'ROBOT_OFFLINE': {'enabled': False}}))
        assert m.estop('pinky1', False, False, 'x')                      # 끌 수 없으므로 생긴다


# ---- 끈 알람은 만들어지지 않는다 (FRA5-1) ----
def test_disabled_alarm_is_not_raised_shown_or_recorded(env):
    with make_client(env) as c:
        login(c, 'mngr')
        reject(c)
        n_before = len(c.get('/api/alarms/history', params={'limit': 1000}).json()['records'])
        assert put(c, 'CMD_REJECTED', {'enabled': False}).status_code == 200
        assert row(c, 'CMD_REJECTED')['enabled'] is False
        for _ in range(3):
            reject(c)
        codes = [a['code'] for a in c.get('/api/alarms').json()['alarms']]
        assert 'CMD_REJECTED' not in [a['code'] for a in c.get('/api/alarms').json()['alarms'] if a['state'] == 'active']
        recs = c.get('/api/alarms/history', params={'limit': 1000}).json()['records']
        raised_after = [r for r in recs if r['code'] == 'CMD_REJECTED' and r['event'] in ('raised', 'repeated')]
        assert len(raised_after) == 1                                   # 끄기 전의 한 건뿐
        assert row(c, 'CMD_REJECTED')['count'] == 1                      # 끈 뒤의 시도는 센 횟수에도 안 든다
        assert codes.count('CMD_REJECTED') == 1


def test_disabling_clears_the_active_alarm_and_broadcasts(env):
    with make_client(env) as c1, make_client(env) as c2:
        for c in (c1, c2):
            login(c, 'mngr')
        reject(c1)
        assert [a['state'] for a in c1.get('/api/alarms').json()['alarms'] if a['code'] == 'CMD_REJECTED'] == ['active']
        r = put(c1, 'CMD_REJECTED', {'enabled': False})
        assert r.status_code == 200
        assert [a['state'] for a in c1.get('/api/alarms').json()['alarms'] if a['code'] == 'CMD_REJECTED'] == ['cleared']   # 소리가 멈추도록 해소
        recs = c1.get('/api/alarms/history', params={'limit': 1000}).json()['records']
        assert any(x['code'] == 'CMD_REJECTED' and x['event'] == 'cleared' for x in recs)


def test_level_change_applies_to_new_alarms_only(env):
    with make_client(env) as c:
        login(c, 'mngr')
        reject(c)
        put(c, 'CMD_REJECTED', {'level': 'HH'})
        first = [a for a in c.get('/api/alarms').json()['alarms'] if a['code'] == 'CMD_REJECTED'][0]
        assert first['level'] == 'L'                                     # 이미 뜬 행은 그대로
        c.post('/api/alarms/ack', json={'id': None})                    # ACK 후 다시 생기면 새 행: 새 레벨
        reject(c)
        levels = [a['level'] for a in c.get('/api/alarms').json()['alarms'] if a['code'] == 'CMD_REJECTED' and a['state'] == 'active']
        assert sorted(levels) == ['HH', 'L']                              # 새 행은 HH, ACK 한 옛 행은 L 그대로


# ---- 되돌리기와 저장 파일 (FRA5-3) ----
def test_local_file_stores_only_the_differences_and_yaml_is_untouched(env):
    yaml_path = ROOT_ALARMS_YAML
    with open(yaml_path, 'rb') as f:
        original = f.read()
    with make_client(env) as c:
        login(c, 'mngr')
        put(c, 'CMD_REJECTED', {'level': 'H', 'message': ac.DEFAULTS['CMD_REJECTED']['message'], 'enabled': True, 'repeat': None})
        saved = yaml.safe_load(env['local'].read_text(encoding='utf-8'))
        assert saved == {'alarms': {'CMD_REJECTED': {'level': 'H'}}}          # 기본과 같은 값은 저장하지 않는다
        put(c, 'CMD_REJECTED', {'level': 'L'})                                # 기본으로 돌아가면 항목이 사라진다
        assert yaml.safe_load(env['local'].read_text(encoding='utf-8')) == {'alarms': {}}
        assert row(c, 'CMD_REJECTED')['modified'] is False
    with open(yaml_path, 'rb') as f:
        assert f.read() == original


def test_sound_default_follows_level_and_is_not_pinned(env):
    with make_client(env) as c:
        login(c, 'mngr')
        assert put(c, 'CMD_REJECTED', {'level': 'info', 'sound': False}).status_code == 200
        assert yaml.safe_load(env['local'].read_text(encoding='utf-8'))['alarms']['CMD_REJECTED'] == {'level': 'info'}   # sound:false 는 info 의 기본이라 저장 안 함
        assert row(c, 'CMD_REJECTED')['sound'] is False
        assert put(c, 'CMD_REJECTED', {'level': 'H'}).status_code == 200
        assert row(c, 'CMD_REJECTED')['sound'] is True                    # 레벨이 H 로 돌아오면 소리도 기본(켜짐)
        assert put(c, 'CMD_REJECTED', {'sound': False}).status_code == 200
        assert row(c, 'CMD_REJECTED')['sound'] is False and row(c, 'CMD_REJECTED')['defaults']['sound'] is True


def test_reset_restores_defaults_and_removes_entry(env):
    with make_client(env) as c:
        login(c, 'mngr')
        put(c, 'CMD_REJECTED', {'level': 'H', 'message': 'x {robot}', 'enabled': False, 'sound': False, 'sound_file': 'a.wav', 'repeat': 9})
        put(c, 'ROBOT_OFFLINE', {'level': 'LL'})
        r = c.post('/api/alarms/catalog/CMD_REJECTED/reset', json={})
        assert r.status_code == 200 and r.json()['alarm']['modified'] is False and r.json()['alarm']['level'] == 'L'
        a = row(c, 'CMD_REJECTED')
        assert (a['level'], a['message'], a['enabled'], a['sound'], a['sound_file'], a['repeat']) == ('L', ac.DEFAULTS['CMD_REJECTED']['message'], True, True, '', None)
        assert list(yaml.safe_load(env['local'].read_text(encoding='utf-8'))['alarms']) == ['ROBOT_OFFLINE']   # 다른 알람의 변경은 남는다
        assert c.post('/api/alarms/catalog/CMD_REJECTED/reset', json={}).status_code == 200     # 이미 기본이어도 오류 아님


def test_changes_persist_across_server_restart(env):
    with make_client(env) as c:
        login(c, 'mngr')
        put(c, 'CMD_REJECTED', {'level': 'H', 'enabled': False, 'repeat': 7})
    with make_client(env) as c2:                                          # 앱을 새로 만든다 = 서버 재시작
        a = row(login(c2, 'oper'), 'CMD_REJECTED')
        assert (a['level'], a['enabled'], a['repeat'], a['modified']) == ('H', False, 7, True)
        login(c2, 'mngr')
        reject(c2)
        assert 'CMD_REJECTED' not in [x['code'] for x in c2.get('/api/alarms').json()['alarms']]        # 재시작 뒤에도 꺼져 있다


def test_broken_local_file_falls_back_to_defaults_and_reports(env):
    env['local'].write_text('alarms:\n  CMD_REJECTED:\n    level: bogus\n', encoding='utf-8')
    original = env['local'].read_text(encoding='utf-8')
    with make_client(env) as c:
        login(c, 'mngr')
        d = c.get('/api/alarms/catalog').json()
        assert d['local_error'] and 'alarms.local.yaml' in d['local_error']
        assert row(c, 'CMD_REJECTED')['level'] == 'L'
        assert env['local'].read_text(encoding='utf-8') == original      # 읽지 못한 파일은 건드리지 않는다
    env['local'].write_text('::: not yaml [', encoding='utf-8')
    with make_client(env) as c:
        assert c.get('/api/alarms/catalog').status_code in (200, 401)


def test_save_failure_rolls_back_memory(env, monkeypatch):
    with make_client(env) as c:
        login(c, 'mngr')
        monkeypatch.setattr(ac.os, 'replace', lambda *a, **k: (_ for _ in ()).throw(OSError(13, 'Permission denied')))
        r = put(c, 'CMD_REJECTED', {'level': 'H'})
        assert r.status_code == 500 and '알람 카탈로그를 저장하지 못했습니다' in r.json()['error']
        assert row(c, 'CMD_REJECTED')['level'] == 'L'
        assert not list(env['tmp'].glob('.alarms.local.*.tmp'))           # 임시 파일도 남기지 않는다


def test_a_removed_sound_file_does_not_block_other_edits(env):
    with make_client(env) as c:
        login(c, 'mngr')
        put(c, 'CMD_REJECTED', {'sound_file': 'a.wav'})
        (env['sounds'] / 'a.wav').unlink()
        assert put(c, 'ROBOT_OFFLINE', {'level': 'LL'}).status_code == 200      # 다른 알람이 지워진 파일 때문에 막히지 않는다
        assert put(c, 'CMD_REJECTED', {'repeat': 4}).status_code == 200         # 같은 알람의 다른 항목도 (저장된 파일 이름은 그대로)
        assert put(c, 'CMD_REJECTED', {'sound_file': 'a.wav'}).status_code == 400   # 새로 고르는 파일은 지금 있어야 한다


# ---- 로그 화면 감사 기록 (FRA5-5) ----
def test_changes_are_logged_with_who_and_what(env, caplog):
    caplog.set_level('INFO', logger='backend')   # 서버는 INFO 로 띄운다 (main.py basicConfig)
    with make_client(env) as c:
        login(c, 'mngr')
        put(c, 'CMD_REJECTED', {'level': 'H'})
        put(c, 'CMD_REJECTED', {'enabled': False})
        put(c, 'CMD_REJECTED', {'message': ''})                         # 거절도 남긴다
        c.post('/api/alarms/catalog/CMD_REJECTED/reset', json={})
        entries = [e for e in c.get('/api/logs').json()['entries'] if e['src'] == 'gui']
        texts = [(e['lvl'], e['text']) for e in entries if '카탈로그' in e['text']]
        joined = '\n'.join(t for _, t in texts)
        assert '알람 카탈로그 변경 (mngr): CMD_REJECTED' in joined and "level 'L'→'H'" in joined
        assert any(l == 'WARN' and 'enabled True→False' in t for l, t in texts)    # 끄기는 WARN 으로
        assert '알람 카탈로그 되돌리기 (mngr): CMD_REJECTED' in joined
        assert '알람 카탈로그 변경 거절 (mngr, CMD_REJECTED): 메시지는' in joined


def test_no_auth_changes_are_logged_as_no_auth(env, caplog):
    caplog.set_level('INFO', logger='backend')
    with make_client(env, Auth.disabled()) as c:
        put(c, 'CMD_REJECTED', {'level': 'H'})
        assert any('변경 (no-auth)' in e['text'] for e in c.get('/api/logs').json()['entries'])


# ---- WS: 모든 화면에 정책이 간다 ----
def test_policy_is_in_snapshot_and_broadcast(env):
    with make_client(env) as c:
        login(c, 'mngr')
        with c.websocket_connect('/ws') as ws1, c.websocket_connect('/ws') as ws2:
            snaps = [ws.receive_json() for ws in (ws1, ws2)]
            for s in snaps:
                assert s['type'] == 'snapshot' and set(s['alarm_policy']) == set(ac.DEFAULTS)
                assert s['alarm_policy']['CMD_REJECTED'] == {'enabled': True, 'level': 'L', 'sound': True, 'sound_file': '', 'repeat': None}
            put(c, 'CMD_REJECTED', {'enabled': False, 'level': 'H'})
            for ws in (ws1, ws2):
                for _ in range(50):
                    m = ws.receive_json()
                    if m['type'] == 'alarm_policy':
                        break
                assert m['policy']['CMD_REJECTED']['enabled'] is False and m['policy']['CMD_REJECTED']['level'] == 'H'


def test_catalog_rows_carry_defaults_and_placeholders(env):
    with make_client(env) as c:
        login(c, 'oper')
        a = row(c, 'CMD_REJECTED')
        assert a['placeholders'] == ['cmd', 'reason', 'robot']
        assert a['defaults'] == {'message': ac.DEFAULTS['CMD_REJECTED']['message'], 'level': 'L', 'enabled': True, 'sound': True, 'sound_file': '', 'repeat': None}
        assert a['modified'] is False
