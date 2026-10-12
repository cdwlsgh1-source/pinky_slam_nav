"""알람 카탈로그(Step A4) 시험: 누락 검사(코드가 내는 모든 알람 코드가 카탈로그에 있는지, 반대로 안 쓰는 항목은 없는지), alarms.yaml 덮어쓰기 검증,
AlarmManager 의 사용 여부·통계, GET /api/alarms/catalog 와 권한. mock 서버를 TestClient 로 띄운다 (ROS 불필요)."""
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import alarmcatalog as ac
from backend.alarms import PATROL_CODES, AlarmManager
from backend.auth import Auth
from backend.config import load_config
from backend.main import create_app

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config()
CODE_RE = r"['\"]([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)['\"]"


def source(*names):
    return '\n'.join((ROOT / n).read_text(encoding='utf-8') for n in names)


# ---- 누락 검사 (FRA4-4): 코드가 실제로 내는 알람이 모두 카탈로그에 있어야 한다 ----
def server_codes():
    """backend/*.py 에서 raise_ 로 만드는 코드 + 순찰 상태 표(PATROL_CODES)의 코드."""
    found = set(re.findall(r"raise_\(\s*" + CODE_RE, source(*(f'backend/{p.name}' for p in (ROOT / 'backend').glob('*.py')))))
    return found | set(PATROL_CODES.values())


def browser_codes():
    """frontend/*.js 의 addAlarm(...) 호출에서 code: 'X' 로 지정한 코드."""
    out, calls = set(), 0
    for p in (ROOT / 'frontend').glob('*.js'):
        for line in p.read_text(encoding='utf-8').splitlines():
            if 'addAlarm(' in line and not line.lstrip().startswith(('function addAlarm', '//')):
                calls += 1
                out |= set(re.findall(r"code:\s*" + CODE_RE, line))
    return out, calls


def test_every_server_code_is_in_catalog():
    missing = server_codes() - set(ac.DEFAULTS)
    assert not missing, f'카탈로그에 없는 서버 알람 코드: {sorted(missing)}'


def test_every_browser_code_is_in_catalog():
    codes, _ = browser_codes()
    missing = codes - set(ac.DEFAULTS)
    assert not missing, f'카탈로그에 없는 브라우저 알람 코드: {sorted(missing)}'


def test_every_addalarm_call_names_a_code():
    """code 없이 호출하면 카탈로그에 올릴 수 없다 (예전에는 모두 'LOCAL' 이라 구분이 안 됐다)."""
    for p in (ROOT / 'frontend').glob('*.js'):
        for n, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
            if 'addAlarm(' in line and not line.lstrip().startswith(('function addAlarm', '//')):
                assert re.search(r"code:\s*" + CODE_RE, line), f'{p.name}:{n} addAlarm 호출에 code 가 없다: {line.strip()[:100]}'


def test_no_dead_catalog_entries():
    """카탈로그에 있는데 코드 어디에서도 내지 않는 항목이 없어야 한다 (표가 거짓이 되지 않게)."""
    used = server_codes() | browser_codes()[0]
    dead = {c for c in ac.DEFAULTS if c not in used}
    assert not dead, f'어디에서도 내지 않는 카탈로그 항목: {sorted(dead)}'


def test_origin_matches_where_it_is_raised():
    srv, brw = server_codes(), browser_codes()[0]
    for code, e in ac.DEFAULTS.items():
        assert (code in srv) == (e['origin'] == 'server'), code
        assert (code in brw) == (e['origin'] == 'browser'), code


def test_defaults_are_well_formed():
    for code, e in ac.DEFAULTS.items():
        assert e['level'] in ac.LEVELS, code
        assert e['origin'] in ('server', 'browser') and e['source'] and e['name'] and e['condition'] and e['message'], code
    assert set(ac.PROTECTED) <= set(ac.DEFAULTS)


def test_prd_8_2_templates_are_kept():
    """PRD 8-2절(한국어)의 코드·레벨·문구가 기본값 그대로다."""
    want = {'ESTOP_FAILED': ('HH', '{robot} 비상정지 실패: {reason}'), 'CMD_SEND_FAILED': ('H', '{robot} 에 명령 `{cmd}` 를 보내지 못함: {reason}'),
            'ROBOT_OFFLINE': ('H', '{robot} 오프라인'), 'CMD_REJECTED': ('L', '{robot} 에 대한 명령 `{cmd}` 거절: {reason}'),
            'PATROL_STOPPED': ('LL', '{robot} 순찰 정지됨'), 'CMD_SENT': ('info', '{robot} 에 명령 `{cmd}` 전송'), 'LOAD_FAILED': ('LL', '{resource} 을(를) 불러오지 못함')}
    for code, (lv, msg) in want.items():
        assert (ac.DEFAULTS[code]['level'], ac.DEFAULTS[code]['message']) == (lv, msg), code


def test_repo_alarms_yaml_loads_and_changes_nothing():
    assert yaml.safe_load((ROOT / 'config' / 'alarms.yaml').read_text(encoding='utf-8'))['alarms'] == {}
    assert {c: (e['level'], e['message'], e['enabled']) for c, e in CFG.alarm_catalog.items()} == {c: (e['level'], e['message'], True) for c, e in ac.DEFAULTS.items()}


# ---- 덮어쓰기 검증 ----
def test_override_applies():
    cat = ac.effective({'ROBOT_OFFLINE': {'level': 'L', 'message': '{robot} 끊김', 'name': 'X', 'enabled': False}})
    e = cat['ROBOT_OFFLINE']
    assert (e['level'], e['message'], e['name'], e['enabled']) == ('L', '{robot} 끊김', 'X', False)
    assert ac.DEFAULTS['ROBOT_OFFLINE']['level'] == 'H'   # 기본값 표는 바뀌지 않는다


@pytest.mark.parametrize('bad', [
    {'NOPE': {'level': 'H'}},                       # 모르는 코드
    {'ROBOT_OFFLINE': {'level': 'X'}},              # 모르는 레벨
    {'ROBOT_OFFLINE': {'sound': 'yes'}},            # bool 아님
    {'ROBOT_OFFLINE': {'enabled': 'no'}},           # bool 아님
    {'ROBOT_OFFLINE': {'name': ''}},                # 빈 문자열
    {'ROBOT_OFFLINE': 5},                           # 항목이 매핑 아님
    {'ESTOP_FAILED': {'enabled': False}},           # 비상정지는 끌 수 없다
    {'SERVER_DISCONNECTED': {'message': 'x'}},      # 브라우저 알람의 문구는 못 바꾼다
    {'ESTOP_REQUEST_FAILED': {'enabled': False}},   # 브라우저의 비상정지 알람도 보호
    [1],
])
def test_bad_overrides_rejected(bad):
    with pytest.raises(ValueError):
        ac.effective(bad)


def test_browser_alarm_level_and_enabled_can_change():
    cat = ac.effective({'SERVER_DISCONNECTED': {'level': 'L', 'enabled': False}})
    assert cat['SERVER_DISCONNECTED']['level'] == 'L' and cat['SERVER_DISCONNECTED']['enabled'] is False


def test_browser_alarm_name_and_condition_can_change():
    assert ac.effective({'SERVER_DISCONNECTED': {'name': 'Link lost', 'condition': 'x'}})['SERVER_DISCONNECTED']['name'] == 'Link lost'


def test_config_loads_override_file(tmp_path):
    (tmp_path / 'alarms.yaml').write_text('alarms:\n  ROBOT_OFFLINE:\n    level: L\n', encoding='utf-8')
    raw = yaml.safe_load((ROOT / 'config' / 'robots.yaml').read_text(encoding='utf-8'))
    base = ROOT / 'config'
    for k in ('map_yaml', 'points_yaml', 'zone_yaml', 'processes_yaml', 'alarm_history_dir', 'alarm_sound_dir'):
        if k in raw:
            raw[k] = str((base / raw[k]).resolve())
    raw['alarms_yaml'] = 'alarms.yaml'
    (tmp_path / 'robots.yaml').write_text(yaml.safe_dump(raw, allow_unicode=True), encoding='utf-8')
    assert load_config(tmp_path / 'robots.yaml').alarm_catalog['ROBOT_OFFLINE']['level'] == 'L'
    (tmp_path / 'alarms.yaml').write_text('alarms:\n  ESTOP_FAILED:\n    enabled: false\n', encoding='utf-8')
    with pytest.raises(ValueError, match='이 알람은 끌 수 없습니다'):
        load_config(tmp_path / 'robots.yaml')
    (tmp_path / 'alarms.yaml').unlink()   # 파일이 없으면 기본값
    assert load_config(tmp_path / 'robots.yaml').alarm_catalog['ROBOT_OFFLINE']['level'] == 'H'


# ---- AlarmManager 가 카탈로그를 따른다 ----
def test_manager_uses_catalog_level_message_and_enabled():
    cat = ac.effective({'ROBOT_OFFLINE': {'level': 'LL', 'message': '{robot} 끊김'}, 'CMD_SENT': {'enabled': False}})
    m = AlarmManager(now=lambda: 100.0, catalog=cat)
    a = m.robot_online('pinky1', False)[0]['alarm']
    assert (a['level'], a['text']) == ('LL', 'pinky1 끊김')
    assert m.command_sent('pinky1', 'start') == []   # 꺼진 알람은 만들지 않는다
    assert [x['code'] for x in m.snapshot()] == ['ROBOT_OFFLINE']


def test_manager_stats_count_every_occurrence():
    t = [10.0]
    m = AlarmManager(now=lambda: t[0])
    m.command_rejected('pinky1', 'bogus', None, 'x')
    t[0] = 20.0
    m.command_rejected('pinky1', 'bogus', None, 'x')   # ACK 전의 같은 알람이라 묶이지만 발생은 센다
    assert m.stats()['CMD_REJECTED'] == {'count': 2, 'last': 20.0}
    assert 'ROBOT_OFFLINE' not in m.stats()


# ---- API ----
def make_client(tmp_path, auth=None):
    return TestClient(create_app(CFG, mock=True, auth=auth if auth is not None else Auth(), alarm_dir=str(tmp_path)))


def login(c, user):
    assert c.post('/api/login', json={'id': user, 'password': user}).status_code == 200


def test_catalog_api_shape_and_counts(tmp_path):
    with make_client(tmp_path, Auth({})) as c:
        c.post('/api/robots/pinky1/command', json={'cmd': 'bogus'})
        d = c.get('/api/alarms/catalog').json()
        assert d['levels'] == ['HH', 'H', 'L', 'LL', 'info'] and d['counted_since'] > 0
        rows = {r['code']: r for r in d['alarms']}
        assert set(rows) == set(ac.DEFAULTS)
        r = rows['CMD_REJECTED']
        assert r['count'] == 1 and r['last_raised'] and r['level'] == 'L' and r['sound'] is True and r['enabled'] is True and r['origin'] == 'server'
        assert rows['CMD_SENT']['sound'] is False
        assert rows['ESTOP_FAILED']['protected'] is True and rows['ROBOT_OFFLINE']['protected'] is False
        assert rows['ROBOT_OFFLINE']['count'] == 0 and rows['ROBOT_OFFLINE']['last_raised'] is None
        b = rows['SERVER_DISCONNECTED']
        assert b['origin'] == 'browser' and b['count'] is None and b['last_raised'] is None
        assert all(r['source'] for r in rows.values())


def test_catalog_requires_login_and_is_readable_by_operator(tmp_path):
    with make_client(tmp_path) as c:
        assert c.get('/api/alarms/catalog').status_code == 401
        for user in ('oper', 'mngr'):
            login(c, user)
            assert c.get('/api/alarms/catalog').status_code == 200


def test_history_endpoints_need_login(tmp_path):
    with make_client(tmp_path) as c:
        assert c.get('/api/alarms/history').status_code == 401
        assert c.get('/api/alarms/history/files').status_code == 401
        login(c, 'oper')
        assert c.get('/api/alarms/history').status_code == 200
        assert c.get('/api/alarms/history/files').status_code == 200


def test_catalog_is_read_only(tmp_path):
    with make_client(tmp_path, Auth({})) as c:
        for m in (c.put, c.post, c.patch, c.delete):
            assert m('/api/alarms/catalog').status_code in (404, 405)
