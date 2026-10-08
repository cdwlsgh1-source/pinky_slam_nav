"""Step 3 API 를 --mock 서버에 직접 붙어 확인한다 (urllib + websockets, httpx 불필요). 서버는 이 스크립트가 띄우고 정리한다.

실행: .venv/bin/python tests/verify_step3_mock.py
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
PORT = 8014
BASE = f'http://127.0.0.1:{PORT}'
FAILS = []


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {extra}' if extra and not cond else ''))
    if not cond:
        FAILS.append(name)


def post(path, body, raw=False):
    data = body if raw else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


async def main():
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--mock', '--mock-wait-scale', '0.1', '--port', str(PORT)],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(BASE + '/api/health')
                break
            except Exception:
                time.sleep(0.1)
        cfg = json.load(urllib.request.urlopen(BASE + '/api/commands/config'))
        check('설정에 로봇별 home', cfg['robots']['pinky1']['home'] == 'P1' and cfg['robots']['pinky2']['home'] == 'P7')

        s, b = post('/api/robots/pinky1/command', {'cmd': 'goto', 'points': ['RED1IN']})
        check('RED1IN -> 400', s == 400 and b['accepted'] is False, (s, b))
        s, b = post('/api/robots/pinky1/command', {'cmd': 'goto', 'points': ['P2', 'P99']})
        check('알 수 없는 지점 -> 400', s == 400)
        s, b = post('/api/robots/pinky1/command', {'cmd': 'goto', 'points': ['P2'] * 11})
        check('11개 -> 400', s == 400)
        s, b = post('/api/robots/pinky1/command', b'garbage', raw=True)
        check('JSON 아님 -> 400', s == 400)
        s, b = post('/api/robots/nobody/command', {'cmd': 'start'})
        check('알 수 없는 로봇 -> 404', s == 404)
        st = json.load(urllib.request.urlopen(BASE + '/api/state'))
        check('거절이 이력에 남음, 로봇 상태는 그대로', [e['result'] for e in st['commands']] == ['rejected'] * 4
              and st['robots']['pinky1']['patrol']['state'] == 'IDLE')

        async with websockets.connect(f'ws://127.0.0.1:{PORT}/ws') as ws:
            snap = json.loads(await ws.recv())
            check('스냅샷에 commands, last_command', snap['type'] == 'snapshot' and len(snap['commands']) == 4
                  and snap['robots']['pinky1']['last_command'] is None)
            # 두 로봇 동시 start -> 각자 자기 로봇 상태만 바뀐다
            s1, b1 = post('/api/robots/pinky1/command', {'cmd': 'start'})
            s2, b2 = post('/api/robots/pinky2/command', {'cmd': 'goto', 'points': ['p2', ' P3 ']})
            check('동시 명령 200', s1 == 200 and s2 == 200 and b2['sent'] == 'goto:P2,P3', (b1, b2))
            seen = {'pinky1': set(), 'pinky2': set()}
            results = {}
            end = time.monotonic() + 30
            while time.monotonic() < end and not ('DONE' in seen['pinky1'] and 'DONE' in seen['pinky2']):
                m = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if m['type'] == 'robot_update' and m['field'] == 'patrol':
                    seen[m['robot']].add(m['data']['state'])
                    if m['robot'] == 'pinky2':
                        seen.setdefault('p2detail', []).append((m['data']['state'], m['data']['waypoint'], m['data']['detail']))
                if m['type'] == 'command':
                    results[m['entry']['id']] = m['entry']['result']
            check('pinky1 start 흐름 DONE', 'DONE' in seen['pinky1'] and 'WAITING_ZONE' in seen['pinky1'])
            check('pinky2 goto 가 자기 상태로', ('STARTING', -1, 'P2,P3') in seen['p2detail'] and ('RETURNING', -1, 'P7') in seen['p2detail'])
            check('두 명령 모두 응답 확인', results.get(b1['id']) == 'acknowledged' and results.get(b2['id']) == 'acknowledged', results)
        st = json.load(urllib.request.urlopen(BASE + '/api/state'))
        check('last_task 기록', st['robots']['pinky2']['last_task']['points'] == ['P2', 'P3'])
    finally:
        srv.terminate()


if __name__ == '__main__':
    asyncio.run(main())
    print('FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASS')
    sys.exit(1 if FAILS else 0)
