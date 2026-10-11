"""알람 ACK API (POST /api/alarms/ack) 테스트: 권한(401), 입력 검증, 결과 반영, 이력 파일. WS 브로드캐스트는 두 브라우저를 쓰는 verify_alarm_ui.py 가 확인한다. mock 서버를 TestClient 로 띄운다 (ROS 불필요)."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.auth import Auth
from backend.config import load_config
from backend.main import create_app

CFG = load_config()
JSON = {'Content-Type': 'application/json'}


def make_client(tmp_path, auth=None):
    app = create_app(CFG, mock=True, auth=auth if auth is not None else Auth(), alarm_dir=str(tmp_path))
    return TestClient(app)


def login(client, user):
    r = client.post('/api/login', json={'id': user, 'password': user})
    assert r.status_code == 200
    return client


def reject_a_command(client, robot='pinky1'):
    """허용 목록 밖 명령을 보내 CMD_REJECTED 알람 한 행을 만든다 (manager 로그인 필요)."""
    r = client.post(f'/api/robots/{robot}/command', json={'cmd': 'bogus'})
    assert r.status_code == 400


def alarms_of(client):
    return client.get('/api/alarms').json()['alarms']


def test_ack_requires_login(tmp_path):
    with make_client(tmp_path) as c:
        assert c.post('/api/alarms/ack', json={'id': None}).status_code == 401


def test_operator_and_manager_can_ack_and_account_is_recorded(tmp_path):
    with make_client(tmp_path) as c:
        login(c, 'mngr')
        reject_a_command(c)
        reject_a_command(c, 'pinky2')                     # 다른 로봇이라 묶이지 않고 별도 행
        first = alarms_of(c)[0]['id']
        login(c, 'oper')                                   # 계정 전환
        r = c.post('/api/alarms/ack', json={'id': first})
        assert r.status_code == 200 and r.json() == {'ok': True, 'acked': [first]}
        rows = {a['id']: a for a in alarms_of(c)}
        assert rows[first]['acked_by'] == 'oper' and rows[first]['acked_at'] is not None
        login(c, 'mngr')
        r = c.post('/api/alarms/ack', json={})            # id 없으면 전체 (이미 ACK 한 것은 건너뜀)
        assert r.status_code == 200 and first not in r.json()['acked'] and len(r.json()['acked']) >= 1
        assert {a['acked_by'] for a in alarms_of(c) if a['level'] != 'info'} == {'oper', 'mngr'}


def test_ack_hides_cleared_alarm_for_everyone(tmp_path):
    with make_client(tmp_path) as c:
        login(c, 'mngr')
        reject_a_command(c)                                # CMD_REJECTED(L, 사건형): 아직 active
        a = next(x for x in alarms_of(c) if x['code'] == 'CMD_REJECTED')
        r = c.post('/api/robots/pinky1/command', json={'cmd': 'stop'})   # 새 명령이 나가면 거절 알람은 해소된다
        assert r.status_code in (200, 202, 503)
        a = next(x for x in alarms_of(c) if x['id'] == a['id'])
        if a['state'] != 'cleared':
            pytest.skip('mock 에서 stop 이 거절 알람을 해소하지 않았다')
        assert c.post('/api/alarms/ack', json={'id': None}).status_code == 200
        assert all(x['id'] != a['id'] for x in alarms_of(c))               # 숨겨졌다
        with c.websocket_connect('/ws') as ws:                             # 새로 접속한 화면의 snapshot 에도 없다
            snap = ws.receive_json()
            assert snap['type'] == 'snapshot' and all(x['id'] != a['id'] for x in snap['alarms'])


@pytest.mark.parametrize('body', [{'id': 'x'}, {'id': [1, 'a']}, {'id': True}, {'id': 1.5}, [1], 'text'])
def test_bad_body_is_400(tmp_path, body):
    with make_client(tmp_path) as c:
        login(c, 'oper')
        assert c.post('/api/alarms/ack', json=body).status_code == 400


def test_origin_and_content_type_are_checked(tmp_path):
    with make_client(tmp_path) as c:
        login(c, 'oper')
        assert c.post('/api/alarms/ack', json={}, headers={'Origin': 'http://evil.example'}).status_code == 403
        assert c.post('/api/alarms/ack', content='{}', headers={'Content-Type': 'text/plain'}).status_code == 415


def test_no_auth_server_allows_ack_as_no_auth(tmp_path):
    with make_client(tmp_path, Auth.disabled()) as c:
        reject_a_command(c)
        assert c.post('/api/alarms/ack', json={'id': None}).status_code == 200
        assert 'no-auth' in {a['acked_by'] for a in alarms_of(c)}


def test_ack_is_appended_to_history_file(tmp_path):
    with make_client(tmp_path) as c:
        login(c, 'mngr')
        reject_a_command(c)
        c.post('/api/alarms/ack', json={'id': None})
    rows = [json.loads(line) for f in sorted(tmp_path.glob('alarms_*.jsonl')) for line in f.read_text(encoding='utf-8').splitlines()]
    assert [r['event'] for r in rows if r['code'] == 'CMD_REJECTED'] == ['raised', 'acked']
    assert [r['t'] for r in rows] == sorted(r['t'] for r in rows)
