"""프로세스(시스템 패널) 실패 알람 PROC_FAILED 시험: 시작 실패가 서버 알람이 되고, 다시 시작하면 해소되며,
화면이 같은 실패를 또 알람으로 만들지 않도록 응답에 alarmed 가 실린다. mock 서버(TestClient), ROS 불필요."""
import pytest
from fastapi.testclient import TestClient

from backend.alarms import AlarmManager
from backend.auth import Auth
from backend.config import load_config
from backend.main import create_app

CFG = load_config()
SSH = next(s.id for s in CFG.processes if s.kind == 'ssh')


def client(tmp_path, **proc_opts):
    app = create_app(CFG, mock=True, mock_opts={'proc_opts': proc_opts}, auth=Auth(), alarm_dir=str(tmp_path / 'h'),
                     alarms_local=str(tmp_path / 'local.yaml'))
    c = TestClient(app)
    assert c.post('/api/login', json={'id': 'mngr', 'password': 'mngr'}).status_code == 200
    return c


def proc_alarms(c):
    return [a for a in c.get('/api/alarms').json()['alarms'] if a['code'] == 'PROC_FAILED']


def test_unit_failed_raises_and_other_states_clear():
    m = AlarmManager()
    msgs = m.proc_state('p1', '브릿지', 'failed', 'ssh 연결 실패')
    a = msgs[0]['alarm']
    assert (a['code'], a['level'], a['robot'], a['state']) == ('PROC_FAILED', 'H', 'p1', 'active')
    assert a['text'] == '브릿지 실패: ssh 연결 실패'
    assert m.proc_state('p1', '브릿지', 'failed', 'ssh 연결 실패')[0]['alarm']['count'] == 2   # ACK 전에는 묶인다
    assert m.proc_state('p2', '지도', 'running') == []                                          # 다른 프로세스·정상 상태는 알람 없음
    cleared = m.proc_state('p1', '브릿지', 'starting')[0]['alarm']
    assert cleared['state'] == 'cleared'


def test_start_failure_becomes_server_alarm_and_clears_on_retry(tmp_path):
    with client(tmp_path, detect_fail=[SSH]) as c:
        r = c.post(f'/api/procs/{SSH}/start', json={})
        assert r.status_code == 502 and r.json()['alarmed'] is True   # 화면은 PROC_REQUEST_FAILED 를 또 만들지 않는다
        al = proc_alarms(c)
        assert len(al) == 1 and al[0]['level'] == 'H' and al[0]['robot'] == SSH and al[0]['state'] == 'active'
        assert '중복 실행을 확인하지 못했습니다' in al[0]['text']
        c.post(f'/api/procs/{SSH}/start', json={})                    # 다시 누르면 STARTING 으로 해소되고, 또 실패하면 새 행이 생긴다
        states = sorted(a['state'] for a in proc_alarms(c))
        assert states == ['active', 'cleared']


def test_disabled_alarm_is_not_raised_and_screen_alarm_is_kept(tmp_path):
    with client(tmp_path, detect_fail=[SSH]) as c:
        assert c.put('/api/alarms/catalog/PROC_FAILED', json={'enabled': False}).status_code == 200
        r = c.post(f'/api/procs/{SSH}/start', json={})
        assert proc_alarms(c) == []
        assert r.json()['alarmed'] is False    # 서버가 안 올렸으니 화면이 PROC_REQUEST_FAILED 로 알린다


def test_unknown_process_is_not_alarmed(tmp_path):
    with client(tmp_path) as c:
        r = c.post('/api/procs/nope/start', json={})
        assert r.status_code == 404 and r.json()['alarmed'] is False
        assert proc_alarms(c) == []
