"""프로세스 제어 + 인증 API 를 --mock 서버에 직접 붙어 확인한다 (urllib + websockets). 서버는 이 스크립트가 띄우고 정리한다.

실행: .venv/bin/python tests/verify_proc_mock.py
확인: 로그인 없이 /api 거절, viewer/operator 권한(명령·프로세스 제어·수동 조작), 프로세스 시작/정지/전체 시작·정지(WS 상태 포함),
      로봇 프로세스 정지 확인 요구, 이미 실행 중인 프로세스 거절, 시작 실패 표시, 인증 미설정 시 프로세스 제어 꺼짐, 로그인 시도 제한.
ros2, ssh 는 실행하지 않는다 (--mock 은 가짜 실행기를 쓴다).
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
PORT = 8016
BASE = f'http://127.0.0.1:{PORT}'
WS = f'ws://127.0.0.1:{PORT}/ws'
FAILS = []

OP, VW = 'op-secret-1', 'vw-secret-1'


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {extra}' if extra and not cond else ''))
    if not cond:
        FAILS.append(name)


class Client:
    """쿠키를 들고 다니는 간단한 HTTP 클라이언트"""

    def __init__(self):
        self.cookie = None

    def req(self, method, path, body=None, headers=None, raw=None):
        h = {'Content-Type': 'application/json'}
        if self.cookie:
            h['Cookie'] = self.cookie
        h.update(headers or {})
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(r) as resp:
                sc = resp.headers.get('Set-Cookie')
                if sc and 'pinky_session=' in sc:
                    self.cookie = sc.split(';')[0]
                    self.set_cookie_header = sc
                return resp.status, json.loads(resp.read() or b'null')
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.load(e)
            except Exception:
                return e.code, {}

    def login(self, pw):
        return self.req('POST', '/api/login', {'password': pw})


def write_config(tmp):
    """기본 설정을 복사하고, 시험용으로 로봇 프로세스에도 명령을 채우고 settle 시간을 줄인다 (가짜 실행기가 무시한다)."""
    src = ROOT / 'config'
    for f in ('robots.yaml', 'points.yaml', 'zone.yaml'):
        txt = (src / f).read_text(encoding='utf-8')
        if f == 'robots.yaml':
            txt = txt.replace('../../map_view_pc', str(ROOT.parent / 'map_view_pc'))
        (tmp / f).write_text(txt, encoding='utf-8')
    p = (src / 'processes.yaml').read_text(encoding='utf-8')
    p = p.replace('ssh: {host: "", user: "", port: 22}', 'ssh: {host: "10.0.0.9", user: "pinky", port: 22}')
    p = re.sub(r'(source: \[\]\n\s+)command: ""\n(\s+)detect: ""', r'\1command: "ros2 launch mock mock.launch.xml"\n\2detect: "ros2 launch mock"', p)
    p = p.replace('    kind: local\n', '    kind: local\n    settle_sec: 0.3\n').replace('    kind: ssh\n', '    kind: ssh\n    settle_sec: 0.3\n')
    (tmp / 'processes.yaml').write_text(p, encoding='utf-8')
    return tmp / 'robots.yaml'


def start_server(cfg_path, env_extra, args=()):
    env = {k: v for k, v in os.environ.items() if not k.startswith('PINKY_')}
    env.update(env_extra)
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--mock', '--mock-wait-scale', '0.1', '--port', str(PORT),
                            '--config', str(cfg_path), *args], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            urllib.request.urlopen(BASE + '/api/health')
            return srv
        except Exception:
            time.sleep(0.1)
    srv.kill()
    raise RuntimeError('서버가 뜨지 않았다')


def stop_server(srv):
    srv.terminate()
    try:
        srv.wait(timeout=8)
    except subprocess.TimeoutExpired:
        srv.kill()


async def until_state(c, pid, states, sec=8):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        p = next(x for x in c.req('GET', '/api/procs')[1]['procs'] if x['id'] == pid)
        if p['state'] in states:
            return p
        await asyncio.sleep(0.1)
    return None


async def until_seq(c, sec=15):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        s = c.req('GET', '/api/procs')[1]['sequence']
        if s and s['state'] != 'running':
            return s
        await asyncio.sleep(0.1)
    return None


async def ws_recv_until(ws, pred, sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), max(0.01, end - time.monotonic())))
        except asyncio.TimeoutError:
            return None
        if pred(m):
            return m
    return None


async def with_auth(tmp):
    cfg = write_config(tmp)
    srv = start_server(cfg, {'PINKY_OPERATOR_PASSWORD': OP, 'PINKY_VIEWER_PASSWORD': VW}, ['--mock-proc-external', 'zone_manager'])
    try:
        anon, viewer, op = Client(), Client(), Client()
        # ---- 로그인 전 ----
        check('로그인 없이 /api/state 는 401', anon.req('GET', '/api/state')[0] == 401)
        check('로그인 없이 /api/procs 는 401', anon.req('GET', '/api/procs')[0] == 401)
        check('로그인 없이 estop 도 401', anon.req('POST', '/api/estop', {})[0] == 401)
        check('/api/health 는 공개', anon.req('GET', '/api/health')[0] == 200)
        sc, me = anon.req('GET', '/api/me')
        check('/api/me: 인증 켜짐, 역할 없음', sc == 200 and me['auth'] is True and me['role'] is None)
        with urllib.request.urlopen(BASE + '/') as r:
            check('화면 파일(index.html)은 로그인 전에도 받는다', r.status == 200)
        try:
            async with websockets.connect(WS) as ws:
                await asyncio.wait_for(ws.recv(), 2)
            ws_ok = True
        except Exception:
            ws_ok = False
        check('로그인 없이 WS 는 거절', not ws_ok)
        # ---- 로그인 ----
        sc, _ = Client().login('wrong')
        check('틀린 비밀번호는 401', sc == 401)
        check('로그인 POST 도 출처 검사', Client().req('POST', '/api/login', {'password': OP}, headers={'Origin': 'http://evil.example'})[0] == 403)
        check('로그인 POST 는 JSON 만', Client().req('POST', '/api/login', raw=b'password=x', headers={'Content-Type': 'text/plain'})[0] == 415)
        sc, body = viewer.login(VW)
        check('viewer 로그인', sc == 200 and body['role'] == 'viewer')
        sc, body = op.login(OP)
        check('operator 로그인', sc == 200 and body['role'] == 'operator')
        sh = op.set_cookie_header.lower()
        check('세션 쿠키: HttpOnly, SameSite=Strict', 'httponly' in sh and 'samesite=strict' in sh and OP not in op.set_cookie_header)
        check('로그인 후 /api/state 200', viewer.req('GET', '/api/state')[0] == 200)

        # ---- viewer 권한 ----
        sc, p = viewer.req('GET', '/api/procs')
        check('viewer 는 프로세스 목록을 본다', sc == 200 and p['enabled'] is True and len(p['procs']) >= 3)
        check('viewer 는 프로세스를 시작할 수 없다(403)', viewer.req('POST', '/api/procs/bridge_pinky1/start', {})[0] == 403)
        check('viewer 는 전체 시작도 불가', viewer.req('POST', '/api/procs/start_all', {})[0] == 403)
        check('viewer 는 로그를 볼 수 없다', viewer.req('GET', '/api/procs/bridge_pinky1/log')[0] == 403)
        check('viewer 는 start 명령을 보낼 수 없다(403)', viewer.req('POST', '/api/robots/pinky1/command', {'cmd': 'start'})[0] == 403)
        sc, _ = viewer.req('POST', '/api/robots/pinky1/estop', {})
        check('viewer 도 비상정지는 누를 수 있다', sc == 200)
        sc, _ = viewer.req('POST', '/api/estop', {})
        check('viewer 도 모두 정지 가능', sc == 200)
        async with websockets.connect(WS, additional_headers={'Cookie': viewer.cookie}) as ws:
            await ws.recv()
            await ws.send(json.dumps({'type': 'drive', 'robot': 'pinky1', 'linear': 0.05, 'angular': 0}))
            m = await ws_recv_until(ws, lambda x: x['type'] == 'drive_denied', 3)
            check('viewer 의 수동 조작은 거절 알림', m is not None and '보기 전용' in m['reason'])
            await ws.send(json.dumps({'type': 'scan', 'robot': 'pinky1', 'on': True}))
            m = await ws_recv_until(ws, lambda x: x['type'] == 'scan', 4)
            check('viewer 도 LiDAR 는 볼 수 있다', m is not None)
        cv = json.loads(op.req('GET', '/api/mock/cmd_vel')[1] and json.dumps(op.req('GET', '/api/mock/cmd_vel')[1]))
        check('viewer 의 drive 로 cmd_vel 이 나가지 않았다(비상정지 0 속도만)', all(c['linear'] == 0 and c['angular'] == 0 for c in cv['cmd_vel']))

        # ---- operator: 프로세스 ----
        sc, p = op.req('GET', '/api/procs')
        by = {x['id']: x for x in p['procs']}
        check('로봇 프로세스(채워 넣은 것)와 설정됨 표시', by['bringup_pinky1']['configured'] and by['bringup_pinky1']['kind'] == 'ssh' and by['bringup_pinky1']['confirm_stop'])
        check('operator 의 알 수 없는 id 는 404', op.req('POST', '/api/procs/rm_rf/start', {})[0] == 404)
        check('procs POST 출처 검사', op.req('POST', '/api/procs/bridge_pinky1/start', {}, headers={'Origin': 'http://evil.example'})[0] == 403)
        check('procs POST 는 JSON 만', op.req('POST', '/api/procs/bridge_pinky1/start', raw=b'{}', headers={'Content-Type': 'text/plain'})[0] == 415)

        async with websockets.connect(WS, additional_headers={'Cookie': op.cookie}) as ws:
            snap = json.loads(await ws.recv())
            check('snapshot 에 procs 포함', snap['procs']['enabled'] is True and len(snap['procs']['procs']) >= 3)
            sc, _ = op.req('POST', '/api/procs/bridge_pinky1/start', {})
            check('bridge_pinky1 시작 accepted', sc == 200)
            m = await ws_recv_until(ws, lambda x: x['type'] == 'procs' and any(q['id'] == 'bridge_pinky1' and q['state'] == 'running' for q in x['procs']), 6)
            check('WS 로 running 상태가 온다', m is not None)
            sc, _ = op.req('POST', '/api/procs/bridge_pinky1/start', {})
            check('이미 실행 중이면 409', sc == 409)
            time.sleep(1.5)
            sc, lg = op.req('GET', '/api/procs/bridge_pinky1/log')
            check('로그에 출력이 쌓인다', sc == 200 and any('동작 중' in x for x in lg['lines']) and lg['lines'][0].startswith('$ local:'))
            sc, _ = op.req('POST', '/api/procs/bridge_pinky1/stop', {})
            check('정지 accepted', sc == 200)
            check('정지 후 stopped', (await until_state(op, 'bridge_pinky1', ('stopped',), 5)) is not None)

            sc, r = op.req('POST', '/api/procs/zone_manager/start', {})
            check('이미 다른 곳에서 실행 중인 프로세스는 거절(409)', sc == 409 and '이미 실행 중' in r['error'])

            sc, r = op.req('POST', '/api/procs/bringup_pinky1/start', {})
            check('로봇 프로세스 시작(가짜 SSH)', sc == 200)
            await until_state(op, 'bringup_pinky1', ('running',), 5)
            sc, r = op.req('POST', '/api/procs/bringup_pinky1/stop', {})
            check('로봇 프로세스 정지는 확인 없이는 409 + needs_confirm', sc == 409 and r['needs_confirm'] is True)
            check('거절된 정지는 상태를 바꾸지 않는다', next(x for x in op.req('GET', '/api/procs')[1]['procs'] if x['id'] == 'bringup_pinky1')['state'] == 'running')
            sc, r = op.req('POST', '/api/procs/bringup_pinky1/stop', {'confirm': True})
            check('confirm 후 정지', sc == 200 and (await until_state(op, 'bringup_pinky1', ('stopped',), 5)) is not None)

            # ---- 전체 시작 / 정지 ----
            sc, _ = op.req('POST', '/api/procs/start_all', {})
            check('전체 시작 accepted', sc == 200)
            sc2, _ = op.req('POST', '/api/procs/start_all', {})
            check('전체 시작 중 다시 누르면 409', sc2 == 409)
            seq = await until_seq(op)
            res = [(x['id'], x['result']) for x in seq['results']] if seq else []
            check('전체 시작 완료 + 순서(브릿지, zone_manager(외부), bringup, map)', seq is not None and seq['state'] == 'done'
                  and [r[0] for r in res][:4] == ['bridge_pinky1', 'bridge_pinky2', 'zone_manager', 'bringup_pinky1'] and ('zone_manager', 'external') in res, str(seq))
            procs_now = {x['id']: x['state'] for x in op.req('GET', '/api/procs')[1]['procs']}
            check('전체 시작 뒤 실행 중', procs_now['bridge_pinky1'] == procs_now['bringup_pinky1'] == procs_now['map_pinky2'] == 'running', str(procs_now))
            sc, r = op.req('POST', '/api/procs/stop_all', {})
            check('전체 정지는 확인 없이 409', sc == 409 and r['needs_confirm'])
            sc, _ = op.req('POST', '/api/procs/stop_all', {'confirm': True})
            seq = await until_seq(op, 30)
            check('전체 정지 완료(역순)', seq is not None and seq['state'] == 'done' and [x['id'] for x in seq['results']][0] == 'map_pinky2', str(seq))
            procs_now = {x['id']: x['state'] for x in op.req('GET', '/api/procs')[1]['procs']}
            check('전체 정지 뒤 모두 stopped', all(v == 'stopped' for v in procs_now.values()), str(procs_now))

        # ---- 로그아웃 ----
        sc, _ = op.req('POST', '/api/logout', {})
        check('로그아웃 후 401', sc == 200 and op.req('GET', '/api/state')[0] == 401)

        # ---- 로그인 시도 제한 (마지막: 같은 주소가 잠긴다) ----
        codes = [Client().login('bad')[0] for _ in range(6)]
        check('틀린 비밀번호가 반복되면 429', codes[:5] == [401] * 5 and codes[5] == 429, str(codes))
        check('잠긴 동안은 맞는 비밀번호도 429', Client().login(OP)[0] == 429)
    finally:
        stop_server(srv)


async def with_failures(tmp):
    cfg = write_config(tmp)
    srv = start_server(cfg, {'PINKY_OPERATOR_PASSWORD': OP}, ['--mock-proc-fail', 'bridge_pinky1'])
    try:
        op = Client()
        op.login(OP)
        sc, _ = op.req('POST', '/api/procs/bridge_pinky1/start', {})
        p = await until_state(op, 'bridge_pinky1', ('failed',), 5)
        check('시작 직후 종료되면 failed + 사유', sc == 200 and p is not None and '시작 직후 종료' in p['message'] and p['exit_code'] == 1, str(p))
        sc, _ = op.req('POST', '/api/procs/start_all', {})
        seq = await until_seq(op)
        check('전체 시작은 첫 실패에서 멈추고 사유를 보인다', seq is not None and seq['state'] == 'failed' and 'pinky1' in seq['message'], str(seq))
        st = {x['id']: x['state'] for x in op.req('GET', '/api/procs')[1]['procs']}
        check('실패 뒤 단계는 시작하지 않았다', st['bridge_pinky2'] == 'stopped' and st['zone_manager'] == 'stopped', str(st))
    finally:
        stop_server(srv)


async def without_auth(tmp):
    cfg = write_config(tmp)
    srv = start_server(cfg, {})
    try:
        c = Client()
        check('인증 미설정: /api 는 그대로 쓸 수 있다', c.req('GET', '/api/state')[0] == 200)
        sc, me = c.req('GET', '/api/me')
        check('인증 미설정: /api/me', me['auth'] is False and me['role'] == 'operator' and me['procs_allowed'] is False)
        sc, p = c.req('GET', '/api/procs')
        check('인증 미설정: 목록은 enabled=false', sc == 200 and p['enabled'] is False)
        sc, r = c.req('POST', '/api/procs/bridge_pinky1/start', {})
        check('인증 미설정이면 프로세스 제어는 403 (원격 실행을 열지 않는다)', sc == 403 and 'PINKY_OPERATOR_PASSWORD' in r['error'])
        check('인증 미설정: 전체 시작도 403', c.req('POST', '/api/procs/start_all', {})[0] == 403)
        check('인증 미설정: 기존 명령 API 는 그대로', c.req('POST', '/api/robots/pinky1/command', {'cmd': 'stop'})[0] == 200)
        check('인증 미설정: 로그인 시도는 400', c.login('x')[0] == 400)
    finally:
        stop_server(srv)


async def viewer_only(tmp):
    cfg = write_config(tmp)
    srv = start_server(cfg, {'PINKY_VIEWER_PASSWORD': VW})
    try:
        v = Client()
        check('viewer 비밀번호만 설정: viewer 로그인', v.login(VW)[0] == 200)
        sc, p = v.req('GET', '/api/procs')
        check('viewer 비밀번호만 설정하면 프로세스 제어는 꺼짐', sc == 200 and p['enabled'] is False)
        check('operator 가 없으니 명령도 못 보낸다', v.req('POST', '/api/robots/pinky1/command', {'cmd': 'stop'})[0] == 403)
    finally:
        stop_server(srv)


async def main():
    for fn in (with_auth, with_failures, without_auth, viewer_only):
        print(f'--- {fn.__name__} ---')
        with tempfile.TemporaryDirectory() as t:
            await fn(Path(t))
    print('\n' + ('모두 통과' if not FAILS else f'실패 {len(FAILS)}건: {FAILS}'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
