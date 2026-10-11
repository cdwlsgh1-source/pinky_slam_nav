"""알람바(Step A1)를 headless Chrome(CDP)으로 확인한다. mock 서버를 직접 띄우고 정리한다.

실행: .venv/bin/python tests/verify_alarm_ui.py [--shot docs/alarm_a1.png] [--narrow-shot docs/alarm_a1_narrow.png]   (google-chrome 필요, 없으면 SKIP)
확인: 서버 알람(오프라인, 구독자 없음, 비상정지 실패, patrol FAILED, 거절, 무응답) 표시와 해소, 같은 알람의 count 묶기,
      새로 연 두 번째 탭에서도 같은 알람, info 필터와 저장, 고정 문구에 한글 없음, 브라우저 전용 알람(서버 연결 끊김),
      하단 이력 패널 없음. /api/mock/* 훅(backend/main.py)으로 mock 에서 장애를 일으킨다.
"""
import argparse
import asyncio
import base64
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_step3_ui as v3  # noqa: E402  (Page 헬퍼 재사용)

ROOT = Path(__file__).resolve().parent.parent
PORT, CDP = 8018, 9236
BASE = f'http://127.0.0.1:{PORT}'
FAILS = []
HANGUL = re.compile('[가-힣]')


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {extra}' if extra and not cond else ''))
    if not cond:
        FAILS.append(name)


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


# 알람바 행 조회 (code, robot, state 로 찾는다)
ROW = lambda code, robot=None, state=None: (  # noqa: E731
    "[...document.querySelectorAll('#alarmBody tr')].filter(tr => tr.dataset.code === '%s'%s%s)" % (
        code, f" && tr.children[1].textContent === '{robot}'" if robot else '', f" && tr.dataset.state === '{state}'" if state else ''))
ROW_TEXT = lambda code, robot=None, state=None: ROW(code, robot, state) + ".map(tr => tr.children[2].textContent).join('|')"  # noqa: E731
ROW_COUNT = lambda code, robot=None, state=None: ROW(code, robot, state) + '.length'  # noqa: E731
SERVER_ROWS = "[...document.querySelectorAll('#alarmBody tr')].filter(tr => !tr.dataset.id.startsWith('local-')).map(tr => tr.dataset.id + ':' + tr.dataset.state + ':' + tr.children[2].textContent).sort().join('\\n')"


async def run(shot, narrow_shot):
    chrome = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not chrome:
        print('SKIP: Chrome 이 없다')
        return
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--mock', '--mock-wait-scale', '0.2', '--port', str(PORT), '--no-auth'],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp()
    br = subprocess.Popen([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', f'--remote-debugging-port={CDP}',
                           f'--user-data-dir={prof}', '--window-size=1400,900', 'about:blank'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def screenshot(pg, path):
        data = (await pg.call('Page.captureScreenshot', format='png'))['data']
        Path(path).write_bytes(base64.b64decode(data))
        print('스크린샷:', path)

    try:
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{CDP}/json'))
                urllib.request.urlopen(BASE + '/api/health')
                break
            except Exception:
                time.sleep(0.1)
        tab = next(t for t in tabs if t['type'] == 'page')
        async with websockets.connect(tab['webSocketDebuggerUrl'], max_size=None) as ws:
            pg = v3.Page(ws)
            await pg.call('Page.enable')
            await pg.call('Emulation.setDeviceMetricsOverride', width=1400, height=900, deviceScaleFactor=1, mobile=False)
            await pg.call('Page.navigate', url=BASE + '/')
            check('페이지 로드와 스냅샷', await pg.wait("app.cfg && app.order.length === 2 && app.wsUp"))

            # ---- 구조와 고정 문구 ----
            check('하단 명령 이력 패널이 없음', await pg.js("!document.getElementById('panelHist') && !document.getElementById('historyBody')"))
            check('알람바 열 제목이 Time / Robot / Message / State',
                  await pg.js("[...document.querySelectorAll('#alarmList thead th')].map(t => t.textContent).join('/')") == 'Time/Robot/Message/State')
            fixed = await pg.js("[...document.querySelectorAll('#alarmList thead th, #alarmList button, #alarmEmpty')].map(e => e.textContent + ' ' + (e.title || '')).join(' ') + ' ' + document.querySelector('.alarmbar').getAttribute('aria-label')")
            check('알람바 고정 문구에 한글이 없음', not HANGUL.search(fixed), fixed)
            # 시작하면 두 로봇이 처음 신호를 보내므로 info 행(ROBOT_ONLINE)이 생긴다
            check('로봇 online 이 info 행으로 기록됨', await pg.wait(ROW_COUNT('ROBOT_ONLINE') + ' === 2', 5))

            # ---- 오프라인 알람 (mock 훅: 로봇이 신호를 끊음) ----
            st, _ = api('POST', '/api/mock/robot/pinky2/silent', {'on': True})
            check('mock 훅: pinky2 침묵', st == 200)
            check('5초 넘게 신호가 없으면 ROBOT_OFFLINE 활성 알람', await pg.wait(ROW_COUNT('ROBOT_OFFLINE', 'pinky2', 'active') + ' === 1', 12))
            check('오프라인 알람 문구', await pg.js(ROW_TEXT('ROBOT_OFFLINE', 'pinky2')) == 'pinky2 is offline')
            check('활성 알람은 Active 로 표시', await pg.js(ROW('ROBOT_OFFLINE', 'pinky2', 'active') + "[0].children[3].textContent") == 'Active')

            # ---- 새 탭에서도 같은 알람 (G5) ----
            req = urllib.request.Request(f'http://127.0.0.1:{CDP}/json/new?about:blank', method='PUT')
            tab2 = json.load(urllib.request.urlopen(req))
            async with websockets.connect(tab2['webSocketDebuggerUrl'], max_size=None) as ws2:
                pg2 = v3.Page(ws2)
                await pg2.call('Page.enable')
                await pg2.call('Emulation.setDeviceMetricsOverride', width=1400, height=900, deviceScaleFactor=1, mobile=False)
                await pg2.call('Page.navigate', url=BASE + '/')
                check('새 탭 로드', await pg2.wait("app.cfg && app.wsUp"))
                check('새 탭도 같은 오프라인 알람을 봄', await pg2.wait(ROW_COUNT('ROBOT_OFFLINE', 'pinky2', 'active') + ' === 1', 5))
                same = await pg.js(SERVER_ROWS) == await pg2.js(SERVER_ROWS)
                check('두 탭의 서버 알람 목록이 같음', same, (await pg.js(SERVER_ROWS), await pg2.js(SERVER_ROWS)))

                # ---- 해소 ----
                api('POST', '/api/mock/robot/pinky2/silent', {'on': False})
                check('신호가 돌아오면 알람이 해소(Cleared)됨 (두 탭 모두)',
                      await pg.wait(ROW_COUNT('ROBOT_OFFLINE', 'pinky2', 'cleared') + ' === 1', 5)
                      and await pg2.wait(ROW_COUNT('ROBOT_OFFLINE', 'pinky2', 'cleared') + ' === 1', 5))
                check('해소 시각이 State 에 표시됨', bool(re.fullmatch(r'Cleared \d\d:\d\d:\d\d', await pg.js(ROW('ROBOT_OFFLINE', 'pinky2', 'cleared') + '[0].children[3].textContent'))))

            # ---- 구독자 없음: 명령 전송 실패와 비상정지 실패 ----
            api('POST', '/api/mock/no-subscriber', {'on': True})
            st, body = api('POST', '/api/robots/pinky1/command', {'cmd': 'start'})
            check('구독자가 없으면 명령이 503', st == 503, (st, body))
            check('CMD_SEND_FAILED 알람 (H)', await pg.wait(ROW_COUNT('CMD_SEND_FAILED', 'pinky1', 'active') + ' === 1', 4)
                  and (await pg.js(ROW('CMD_SEND_FAILED', 'pinky1') + '[0].className')).startswith('lvl-H'))
            api('POST', '/api/robots/pinky1/command', {'cmd': 'start'})
            check('같은 알람이 반복되면 새 행이 아니라 ×2 로 묶임', await pg.wait(ROW_TEXT('CMD_SEND_FAILED', 'pinky1', 'active') + ".includes('×2')", 4)
                  and await pg.js(ROW_COUNT('CMD_SEND_FAILED', 'pinky1')) == 1)
            st, body = api('POST', '/api/robots/pinky1/estop', {})
            check('비상정지가 둘 다 실패하면 503', st == 503, (st, body))
            check('ESTOP_FAILED 알람 (HH, 해소 전까지 활성)', await pg.wait(ROW_COUNT('ESTOP_FAILED', 'pinky1', 'active') + ' === 1', 4)
                  and (await pg.js(ROW('ESTOP_FAILED', 'pinky1') + '[0].className')).startswith('lvl-HH'))
            check('비상정지 실패 문구', (await pg.js(ROW_TEXT('ESTOP_FAILED', 'pinky1'))).startswith('E-STOP failed on pinky1: '))
            if shot:
                await screenshot(pg, shot)
            api('POST', '/api/mock/no-subscriber', {'on': False})
            api('POST', '/api/robots/pinky1/estop', {})
            check('구독자가 돌아온 뒤 비상정지가 성공하면 ESTOP_FAILED 가 해소됨', await pg.wait(ROW_COUNT('ESTOP_FAILED', 'pinky1', 'cleared') + ' === 1 && ' + ROW_COUNT('ESTOP_FAILED', 'pinky1', 'active') + ' === 0', 4))
            check('비상정지 전송이 info 행으로 남음', await pg.wait(ROW_TEXT('CMD_SENT', 'pinky1') + ".includes('Command `estop` sent to pinky1')", 4))
            api('POST', '/api/robots/pinky1/command', {'cmd': 'start'})
            check('새 명령이 나가면 이전 전송 실패 알람이 해소됨', await pg.wait(ROW_COUNT('CMD_SEND_FAILED', 'pinky1', 'active') + ' === 0', 4))

            # ---- 거절 ----
            st, _ = api('POST', '/api/robots/pinky2/command', {'cmd': 'goto', 'points': ['P99']})
            check('허용되지 않은 지점은 400', st == 400)
            check('CMD_REJECTED 알람 (L)', await pg.wait(ROW_COUNT('CMD_REJECTED', 'pinky2', 'active') + ' === 1', 4)
                  and (await pg.js(ROW('CMD_REJECTED', 'pinky2') + '[0].className')).startswith('lvl-L'))

            # ---- patrol FAILED / RETRY (mock 훅으로 주입) ----
            api('POST', '/api/mock/robot/pinky2/patrol', {'state': 'FAILED', 'detail': 'not allowed: [P3]'})
            check('PATROL_FAILED 알람과 사유', await pg.wait(ROW_TEXT('PATROL_FAILED', 'pinky2', 'active') + ".includes('Patrol failed on pinky2: not allowed: [P3]')", 4))
            api('POST', '/api/mock/robot/pinky2/patrol', {'state': 'RETRY', 'detail': 'amcl'})
            check('RETRY 로 바뀌면 FAILED 는 해소되고 PATROL_RETRY 가 활성',
                  await pg.wait(ROW_COUNT('PATROL_FAILED', 'pinky2', 'cleared') + ' === 1 && ' + ROW_COUNT('PATROL_RETRY', 'pinky2', 'active') + ' === 1', 4))
            api('POST', '/api/mock/robot/pinky2/patrol', {'state': 'IDLE'})
            check('다른 상태로 돌아가면 순찰 알람이 모두 해소됨', await pg.wait(ROW_COUNT('PATROL_RETRY', 'pinky2', 'active') + ' === 0', 4))

            # ---- 정지 (STOPPED, LL) ----
            api('POST', '/api/mock/robot/pinky2/patrol', {'state': 'STOPPED'})
            check('PATROL_STOPPED 알람 (LL)', await pg.wait(ROW_COUNT('PATROL_STOPPED', 'pinky2', 'active') + ' === 1', 4)
                  and (await pg.js(ROW('PATROL_STOPPED', 'pinky2') + '[0].className')).startswith('lvl-LL'))
            api('POST', '/api/mock/robot/pinky2/patrol', {'state': 'IDLE'})

            # ---- 응답 없음: 작업 중에 다시 보낸 start 는 무시되어 patrol 상태가 안 바뀐다 ----
            await pg.wait(ROW_COUNT('CMD_SENT', 'pinky1') + ' >= 2', 3)
            api('POST', '/api/robots/pinky1/command', {'cmd': 'start'})   # 이미 순찰 중이라 무시된다
            check('5초 안에 응답이 없으면 CMD_NO_RESPONSE 알람 (L)', await pg.wait(ROW_COUNT('CMD_NO_RESPONSE', 'pinky1', 'active') + ' === 1', 9)
                  and (await pg.js(ROW('CMD_NO_RESPONSE', 'pinky1') + '[0].className')).startswith('lvl-L'))
            api('POST', '/api/robots/pinky1/command', {'cmd': 'stop'})

            # ---- info 필터와 저장 ----
            info_rows = lambda: pg.js("[...document.querySelectorAll('#alarmBody tr.lvl-info')].length")  # noqa: E731
            check('기본은 info 행이 보임', await info_rows() > 0 and await pg.js("document.getElementById('alarmInfoBtn').textContent") == 'Hide info')
            await pg.click_sel('#alarmInfoBtn')
            check('Hide info 를 누르면 info 행이 사라지고 버튼이 Show info', await info_rows() == 0 and await pg.js("document.getElementById('alarmInfoBtn').textContent") == 'Show info')
            check('info 가 아닌 알람은 그대로 보임', await pg.js(ROW_COUNT('ESTOP_FAILED') + ' === 1'))
            await pg.call('Page.navigate', url=BASE + '/')
            check('새로고침 뒤 다시 로드', await pg.wait("app.cfg && app.wsUp && document.querySelectorAll('#alarmBody tr').length > 0", 10))
            check('info 숨김이 새로고침 뒤에도 유지', await info_rows() == 0 and await pg.js("document.getElementById('alarmInfoBtn').textContent") == 'Show info')
            await pg.click_sel('#alarmInfoBtn')
            check('Show info 를 누르면 다시 보임', await info_rows() > 0)

            # ---- 브라우저 전용 알람: 서버 연결 끊김과 재연결 ----
            await pg.js("sock.close()")
            check('연결이 끊기면 Server disconnected 알람 (브라우저 전용)', await pg.wait(ROW_COUNT('SERVER_DISCONNECTED') + ' >= 1', 3))
            check('다시 연결되면 끊김은 해소(Cleared)되고 Server reconnected 가 기록됨',
                  await pg.wait(ROW_COUNT('SERVER_DISCONNECTED', None, 'active') + ' === 0 && ' + ROW_COUNT('SERVER_RECONNECTED') + ' === 1', 8))
            check('재연결 뒤에도 서버 알람이 유지됨 (새 스냅샷)', await pg.wait(ROW_COUNT('ESTOP_FAILED', 'pinky1', 'cleared') + ' === 1', 4))

            # ---- 좁은 화면 ----
            await pg.call('Emulation.setDeviceMetricsOverride', width=800, height=900, deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(0.4)
            check('좁은 화면: 가로 스크롤 없음', await pg.js("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"))
            check('좁은 화면: 비상 정지가 보임', await pg.js("document.getElementById('estopAll').getBoundingClientRect().height") > 0)
            if narrow_shot:
                await screenshot(pg, narrow_shot)
    finally:
        br.terminate()
        srv.terminate()
        try:
            srv.wait(5)
        except subprocess.TimeoutExpired:
            srv.kill()
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--shot')
    ap.add_argument('--narrow-shot')
    a = ap.parse_args()
    asyncio.run(run(a.shot, a.narrow_shot))
    print('ALL PASS' if not FAILS else f'FAILED: {FAILS}')
    sys.exit(1 if FAILS else 0)
