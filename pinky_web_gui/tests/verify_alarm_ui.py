"""알람바(Step A1)를 headless Chrome(CDP)으로 확인한다. mock 서버를 직접 띄우고 정리한다.

실행: .venv/bin/python tests/verify_alarm_ui.py [--shot docs/alarm_a1.png] [--narrow-shot docs/alarm_a1_narrow.png]   (google-chrome 필요, 없으면 SKIP)
확인: 서버 알람(오프라인, 구독자 없음, 비상정지 실패, patrol FAILED, 거절, 무응답) 표시와 해소, 반복 알람은 묶지 않음(사건형 새 행, 상태형 한 행),
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
from datetime import datetime
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_step3_ui as v3  # noqa: E402  (Page 헬퍼 재사용)
from uitext import english_left  # noqa: E402

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
    hist_dir = Path(tempfile.mkdtemp())   # 알람 이력 폴더 (실제 data/alarms 와 섞이지 않게 임시 폴더)
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--mock', '--mock-wait-scale', '0.2', '--port', str(PORT), '--no-auth',
                            '--alarm-history-dir', str(hist_dir), '--alarm-history-chunk', '100'],
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
            check('알람바 열 제목이 시각 / 로봇 / 메시지 / 상태',
                  await pg.js("[...document.querySelectorAll('#alarmList thead th')].map(t => t.textContent).join('/')") == '시각/로봇/메시지/상태')
            fixed = await pg.js("[...document.querySelectorAll('#alarmList thead th, #alarmList button, #alarmEmpty')].map(e => e.textContent + ' ' + (e.title || '')).join(' ') + ' ' + document.querySelector('.alarmbar').getAttribute('aria-label')")
            check('알람바 고정 문구에 영문 문장이 없음 (한국어)', not english_left(fixed), english_left(fixed))
            # 시작하면 두 로봇이 처음 신호를 보내므로 info 행(ROBOT_ONLINE)이 생긴다
            check('로봇 online 이 info 행으로 기록됨', await pg.wait(ROW_COUNT('ROBOT_ONLINE') + ' === 2', 5))

            # ---- 오프라인 알람 (mock 훅: 로봇이 신호를 끊음) ----
            st, _ = api('POST', '/api/mock/robot/pinky2/silent', {'on': True})
            check('mock 훅: pinky2 침묵', st == 200)
            check('5초 넘게 신호가 없으면 ROBOT_OFFLINE 활성 알람', await pg.wait(ROW_COUNT('ROBOT_OFFLINE', 'pinky2', 'active') + ' === 1', 12))
            check('오프라인 알람 문구', await pg.js(ROW_TEXT('ROBOT_OFFLINE', 'pinky2')) == 'pinky2 오프라인')
            check('활성 알람은 활성 으로 표시', await pg.js(ROW('ROBOT_OFFLINE', 'pinky2', 'active') + "[0].children[3].textContent") == '활성')

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
                check('해소 시각이 State 에 표시됨', bool(re.fullmatch(r'해소 \d\d:\d\d:\d\d', await pg.js(ROW('ROBOT_OFFLINE', 'pinky2', 'cleared') + '[0].children[3].textContent'))))

            # ---- 구독자 없음: 명령 전송 실패와 비상정지 실패 ----
            api('POST', '/api/mock/no-subscriber', {'on': True})
            st, body = api('POST', '/api/robots/pinky1/command', {'cmd': 'start'})
            check('구독자가 없으면 명령이 503', st == 503, (st, body))
            check('CMD_SEND_FAILED 알람 (H)', await pg.wait(ROW_COUNT('CMD_SEND_FAILED', 'pinky1', 'active') + ' === 1', 4)
                  and (await pg.js(ROW('CMD_SEND_FAILED', 'pinky1') + '[0].className')).startswith('lvl-H'))
            api('POST', '/api/robots/pinky1/command', {'cmd': 'start'})
            check('ACK 전의 같은 알람이 반복되면 새 행이 아니라 ×2 로 묶임', await pg.wait(ROW_TEXT('CMD_SEND_FAILED', 'pinky1', 'active') + ".includes('×2')", 4)
                  and await pg.js(ROW_COUNT('CMD_SEND_FAILED', 'pinky1')) == 1)
            st, body = api('POST', '/api/robots/pinky1/estop', {})
            check('비상정지가 둘 다 실패하면 503', st == 503, (st, body))
            check('ESTOP_FAILED 알람 (HH, 해소 전까지 활성)', await pg.wait(ROW_COUNT('ESTOP_FAILED', 'pinky1', 'active') + ' === 1', 4)
                  and (await pg.js(ROW('ESTOP_FAILED', 'pinky1') + '[0].className')).startswith('lvl-HH'))
            check('비상정지 실패 문구', (await pg.js(ROW_TEXT('ESTOP_FAILED', 'pinky1'))).startswith('pinky1 비상정지 실패: '))
            if shot:
                await screenshot(pg, shot)
            api('POST', '/api/mock/no-subscriber', {'on': False})
            api('POST', '/api/robots/pinky1/estop', {})
            check('구독자가 돌아온 뒤 비상정지가 성공하면 ESTOP_FAILED 가 해소됨', await pg.wait(ROW_COUNT('ESTOP_FAILED', 'pinky1', 'cleared') + ' === 1 && ' + ROW_COUNT('ESTOP_FAILED', 'pinky1', 'active') + ' === 0', 4))
            check('비상정지 전송이 info 행으로 남음', await pg.wait(ROW_TEXT('CMD_SENT', 'pinky1') + ".includes('pinky1 에 명령 `estop` 전송')", 4))
            api('POST', '/api/robots/pinky1/command', {'cmd': 'start'})
            check('새 명령이 나가면 이전 전송 실패 알람이 해소됨', await pg.wait(ROW_COUNT('CMD_SEND_FAILED', 'pinky1', 'active') + ' === 0', 4))

            # ---- 거절 ----
            st, _ = api('POST', '/api/robots/pinky2/command', {'cmd': 'goto', 'points': ['P99']})
            check('허용되지 않은 지점은 400', st == 400)
            check('CMD_REJECTED 알람 (L)', await pg.wait(ROW_COUNT('CMD_REJECTED', 'pinky2', 'active') + ' === 1', 4)
                  and (await pg.js(ROW('CMD_REJECTED', 'pinky2') + '[0].className')).startswith('lvl-L'))

            # ---- patrol FAILED / RETRY (mock 훅으로 주입) ----
            api('POST', '/api/mock/robot/pinky2/patrol', {'state': 'FAILED', 'detail': 'not allowed: [P3]'})
            check('PATROL_FAILED 알람과 사유', await pg.wait(ROW_TEXT('PATROL_FAILED', 'pinky2', 'active') + ".includes('pinky2 순찰 실패: not allowed: [P3]')", 4))
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
            check('기본은 info 행이 보임', await info_rows() > 0 and await pg.js("document.getElementById('alarmInfoBtn').textContent") == 'Hide Info')
            await pg.click_sel('#alarmInfoBtn')
            check('Hide Info 를 누르면 info 행이 사라지고 버튼이 Show Info', await info_rows() == 0 and await pg.js("document.getElementById('alarmInfoBtn').textContent") == 'Show Info')
            check('info 가 아닌 알람은 그대로 보임', await pg.js(ROW_COUNT('ESTOP_FAILED') + ' === 1'))
            await pg.call('Page.navigate', url=BASE + '/')
            check('새로고침 뒤 다시 로드', await pg.wait("app.cfg && app.wsUp && document.querySelectorAll('#alarmBody tr').length > 0", 10))
            check('info 숨김이 새로고침 뒤에도 유지', await info_rows() == 0 and await pg.js("document.getElementById('alarmInfoBtn').textContent") == 'Show Info')
            await pg.click_sel('#alarmInfoBtn')
            check('Show Info 를 누르면 다시 보임', await info_rows() > 0)

            # ---- 브라우저 전용 알람: 서버 연결 끊김과 재연결 ----
            await pg.js("sock.close()")
            check('연결이 끊기면 서버 연결 끊김 알람 (브라우저 전용)', await pg.wait(ROW_COUNT('SERVER_DISCONNECTED') + ' >= 1', 3))
            check('다시 연결되면 끊김은 해소(Cleared)되고 서버 재연결이 기록됨',
                  await pg.wait(ROW_COUNT('SERVER_DISCONNECTED', None, 'active') + ' === 0 && ' + ROW_COUNT('SERVER_RECONNECTED') + ' === 1', 8))
            check('재연결 뒤에도 서버 알람이 유지됨 (새 스냅샷)', await pg.wait(ROW_COUNT('ESTOP_FAILED', 'pinky1', 'cleared') + ' === 1', 4))

            # ---- 목록 스크롤과 높이 조절 ----
            for i in range(14):   # 거절 알람을 서로 다른 문구로 늘려 목록이 넘치게 한다
                api('POST', '/api/robots/pinky2/command', {'cmd': 'goto', 'points': [f'X{i}']})
            await asyncio.sleep(0.6)
            box = "(() => { const s = document.getElementById('alarmScroll'); return [s.clientHeight, s.scrollHeight, s.scrollTop]; })()"
            ch0, sh0, _ = await pg.js(box)
            check('알람이 많으면 목록 안에서 스크롤됨 (내용 > 보이는 높이)', sh0 > ch0 + 20, (ch0, sh0))
            await pg.js("document.getElementById('alarmScroll').scrollTop = 40")
            check('스크롤하면 목록 안에서만 움직임 (페이지는 그대로)', (await pg.js(box))[2] > 0 and await pg.js('window.scrollY') == 0)
            await pg.js("document.getElementById('alarmScroll').scrollTop = 0")
            gx, gy = await pg.js("(() => { const r = document.getElementById('alarmGrip').getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; })()")
            await pg.call('Input.dispatchMouseEvent', type='mousePressed', x=gx, y=gy, button='left', clickCount=1)
            for dy in (40, 100, 160):
                await pg.call('Input.dispatchMouseEvent', type='mouseMoved', x=gx, y=gy + dy, button='left')
            await pg.call('Input.dispatchMouseEvent', type='mouseReleased', x=gx, y=gy + 160, button='left', clickCount=1)
            await asyncio.sleep(0.3)
            ch1 = (await pg.js(box))[0]
            check('손잡이를 아래로 끌면 목록이 그만큼 커짐 (상하 확장)', abs((ch1 - ch0) - 160) < 6, (ch0, ch1))
            check('끄는 중 켜진 body 클래스가 정리됨', await pg.js("!document.body.classList.contains('resizing')"))
            await pg.call('Page.navigate', url=BASE + '/')
            check('새로고침 뒤 다시 로드', await pg.wait("app.cfg && app.wsUp && document.querySelectorAll('#alarmBody tr').length > 0", 10))
            check('늘린 높이가 새로고침 뒤에도 유지', abs((await pg.js(box))[0] - ch1) < 3, (ch1, await pg.js(box)))
            await pg.js("document.getElementById('alarmGrip').focus()")
            await pg.call('Input.dispatchKeyEvent', type='rawKeyDown', key='ArrowUp', windowsVirtualKeyCode=38)
            await pg.call('Input.dispatchKeyEvent', type='keyUp', key='ArrowUp', windowsVirtualKeyCode=38)
            await asyncio.sleep(0.2)
            check('손잡이에서 위 방향키를 누르면 목록이 줄어듦', (await pg.js(box))[0] < ch1 - 10)
            await pg.js("document.getElementById('alarmGrip').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))")
            await asyncio.sleep(0.2)
            check('더블클릭하면 기본 높이로 돌아감', abs((await pg.js(box))[0] - 132) < 4, await pg.js(box))
            await pg.call('Input.dispatchMouseEvent', type='mouseMoved', x=gx, y=gy)
            await pg.js("setAlarmH(99999, true)")
            check('최대 높이는 화면의 60% 로 제한됨', (await pg.js("document.getElementById('alarmScroll').style.maxHeight")) == f"{round(900 * 0.6)}px")
            await pg.js("setAlarmH(0, true)")
            check('최소 높이(56px) 아래로는 줄지 않음', (await pg.js("document.getElementById('alarmScroll').style.maxHeight")) == '56px')
            await pg.js("setAlarmH(132, true)")

            # ---- 새로고침해도 남는 브라우저 전용 알람 ----
            local_before = await pg.js("app.alarms.filter(a => a.local).length")
            await pg.js("addAlarm('warn', 'Browser-only test alarm', { code: 'LOAD_FAILED' })")
            check('브라우저 전용 알람이 목록에 보임', await pg.js(ROW_COUNT('LOAD_FAILED') + ' === 1'))
            await pg.call('Page.navigate', url=BASE + '/')
            check('새로고침 뒤 다시 로드', await pg.wait("app.cfg && app.wsUp", 10))
            check('새로고침해도 브라우저 전용 알람이 남음', await pg.wait(ROW_TEXT('LOAD_FAILED') + ".includes('Browser-only test alarm')", 4))
            check('앞서 있던 끊김/재연결 기록도 남음', await pg.js(ROW_COUNT('SERVER_RECONNECTED') + ' >= 1'))
            check('끊김 중이던(active) 알람은 새로고침 뒤 해소로 바뀜', await pg.js(ROW_COUNT('SERVER_DISCONNECTED', None, 'active') + ' === 0'))
            check('복원 뒤 새 브라우저 전용 알람 id 가 겹치지 않음', await pg.js("(() => { app.alarms.length; addAlarm('warn', 'second'); const ids = app.alarms.filter(a => a.local).map(a => a.id); return new Set(ids).size === ids.length; })()"))
            await pg.js("localStorage.removeItem('pinky.alarmlocal.v1'); app.alarms = app.alarms.filter(a => !a.local); renderAlarms()")
            check('저장 데이터가 깨져 있어도 화면이 뜸', await pg.js("(() => { localStorage.setItem('pinky.alarmlocal.v1', '{broken'); return true; })()"))
            await pg.call('Page.navigate', url=BASE + '/')
            check('깨진 저장값에서도 정상 로드', await pg.wait("app.cfg && app.wsUp && Array.isArray(app.alarms)", 10))
            await pg.js("localStorage.removeItem('pinky.alarmlocal.v1')")

            # ---- 알람 이력 저장: 모니터링은 100건, 이력은 100건씩 파일로 ----
            _, before = api('GET', '/api/alarms/history/files')
            n0 = sum(f['records'] for f in before['files'])
            check('이력 파일이 생겼고 지금까지의 알람 이벤트가 저장됨', n0 > 10 and len(before['files']) >= 1, before)
            for i in range(130):   # 알람은 묶지 않으므로 하나씩 새 알람이 된다
                api('POST', '/api/robots/pinky1/command', {'cmd': 'start'})
            await asyncio.sleep(1.0)
            check('모니터링 화면은 100건까지만 보임', await pg.js("app.alarms.length") <= 100 and await pg.js("document.querySelectorAll('#alarmBody tr').length") <= 100,
                  await pg.js("app.alarms.length"))
            _, snap = api('GET', '/api/alarms')
            check('서버가 들고 있는 알람도 100건 이하', len(snap['alarms']) <= 100, len(snap['alarms']))
            st, hist = api('GET', '/api/alarms/history/files')
            files = hist['files']
            total = sum(f['records'] for f in files)
            check('저장된 이력은 모니터링 100건 제한과 무관하게 모두 남음 (130건 이상 추가)', total >= n0 + 130, (n0, total))
            check('파일은 100건씩 나뉨 (마지막 하나만 100건 미만)', all(f['records'] == 100 for f in files[1:]) and 1 <= files[0]['records'] <= 100, [f['records'] for f in files])
            today = datetime.now().strftime('%Y-%m-%d')
            check('파일 이름에 파일을 만든 날짜와 시각이 들어감 (alarms_<날짜>_<HHMMSS>.jsonl)', all(re.fullmatch(rf'alarms_{today}_\d{{6}}(-\d+)?\.jsonl', f['name']) for f in files), [f['name'] for f in files])
            on_disk = sorted(p.name for p in hist_dir.iterdir())
            check('실제 파일이 이력 폴더에 있음', on_disk == sorted(f['name'] for f in files), on_disk)
            first = json.loads((hist_dir / sorted(on_disk)[0]).read_text(encoding='utf-8').splitlines()[0])
            check('레코드에 저장 일시와 이벤트가 적힘', first['saved_at'].startswith(today) and first['event'] in ('raised', 'repeated', 'acked', 'cleared') and 'code' in first, first)
            st, rec = api('GET', f'/api/alarms/history?date={today}&limit=5')
            check('이력 조회 API (최신이 앞, limit 적용)', st == 200 and len(rec['records']) == 5 and rec['records'][0]['t'] >= rec['records'][-1]['t'])
            st, _ = api('GET', '/api/alarms/history?date=bad')
            check('날짜 형식이 틀리면 400', st == 400)

            # ---- Step A2: ACK / SIL (브라우저 2개) ----
            api('POST', '/api/alarms/ack', {'id': None})       # 앞 단계의 알람을 모두 확인 처리해 두고 새로 시작한다
            api('POST', '/api/robots/pinky2/command', {'cmd': 'stop'})   # 앞 단계에서 쌓인 거절 알람(활성)을 해소 -> ACK 했으므로 숨겨진다
            api('POST', '/api/robots/pinky1/command', {'cmd': 'stop'})
            await pg.call('Page.navigate', url=BASE + '/')
            check('A2 시작: 새로고침 뒤 로드', await pg.wait("app.cfg && app.wsUp", 10))
            req = urllib.request.Request(f'http://127.0.0.1:{CDP}/json/new?about:blank', method='PUT')
            tab3 = json.load(urllib.request.urlopen(req))
            async with websockets.connect(tab3['webSocketDebuggerUrl'], max_size=None) as ws3:
                pgb = v3.Page(ws3)
                await pgb.call('Page.enable')
                await pgb.call('Emulation.setDeviceMetricsOverride', width=1400, height=900, deviceScaleFactor=1, mobile=False)
                await pgb.call('Page.navigate', url=BASE + '/')
                check('두 번째 브라우저 로드', await pgb.wait("app.cfg && app.wsUp", 10))
                BTN = lambda i, prop: f"document.getElementById('{i}').{prop}"  # noqa: E731
                check('ACK / SIL 버튼과 툴팁 문구', await pg.js(BTN('alarmAckBtn', 'title')) == '알람을 확인(ACK)합니다: 해소된 알람은 목록에서 숨기고 활성 알람은 확인됨으로 표시'
                      and await pg.js(BTN('alarmSilBtn', 'title')) == '지금 울리는 알람 소리를 끕니다 (이 브라우저만)'
                      and await pg.js(BTN('alarmAckBtn', 'textContent')) == 'ACK' and await pg.js(BTN('alarmSilBtn', 'textContent')) == 'SIL')
                await asyncio.sleep(1.0)
                api('POST', '/api/alarms/ack', {'id': None})   # stop 으로 새로 생긴 PATROL_STOPPED 도 확인 처리해 깨끗한 상태로 시작
                check('대상이 없으면 ACK / SIL 이 비활성', await pg.wait(BTN('alarmAckBtn', 'disabled') + ' && ' + BTN('alarmSilBtn', 'disabled'), 4)
                      and await pgb.js(BTN('alarmAckBtn', 'disabled') + ' && ' + BTN('alarmSilBtn', 'disabled')))

                # 활성 알람을 ACK: 숨기지 않고 누가 언제 확인했는지 표시
                api('POST', '/api/mock/robot/pinky2/silent', {'on': True})
                both = lambda expr, t=12: asyncio.gather(pg.wait(expr, t), pgb.wait(expr, t))  # noqa: E731
                check('활성 알람이 생기면 ACK / SIL 이 켜짐 (두 브라우저)', all(await both(ROW_COUNT('ROBOT_OFFLINE', 'pinky2', 'active') + ' === 1 && !' + BTN('alarmAckBtn', 'disabled') + ' && !' + BTN('alarmSilBtn', 'disabled'))))
                await pg.click_sel('#alarmAckBtn')
                ack_state = ROW('ROBOT_OFFLINE', 'pinky2', 'active') + '[0].children[3].textContent'
                check('활성 알람을 ACK 하면 숨기지 않고 Acknowledged (계정, 시각) 표시 - 두 브라우저',
                      all(await both(ack_state + ".startsWith('확인됨 no-auth ')")), (await pg.js(ack_state), await pgb.js(ack_state)))
                check('ACK 로 대상이 없어지면 ACK / SIL 이 모두 비활성 (ACK 한 알람은 소리가 멈춤)',
                      await pg.wait(BTN('alarmAckBtn', 'disabled') + ' && ' + BTN('alarmSilBtn', 'disabled'), 4))
                # ACK 한 활성 알람이 해소되면 숨는다
                api('POST', '/api/mock/robot/pinky2/silent', {'on': False})
                check('ACK 한 알람이 해소되면 두 브라우저에서 사라짐', all(await both(ROW_COUNT('ROBOT_OFFLINE', 'pinky2') + ' === 0')))

                # 해소된 알람 ACK: 한 브라우저에서 누르면 두 브라우저 모두에서 사라짐, 활성 알람은 유지
                api('POST', '/api/robots/pinky1/command', {'cmd': 'stop'})
                api('POST', '/api/robots/pinky2/command', {'cmd': 'goto', 'points': ['P99']})   # 거절 -> 활성 (pinky2)
                api('POST', '/api/robots/pinky1/command', {'cmd': 'goto', 'points': ['P99']})   # 거절 -> 활성 (pinky1)
                api('POST', '/api/robots/pinky1/command', {'cmd': 'stop'})                      # pinky1 새 명령 -> pinky1 거절 해소
                check('pinky1 거절은 해소(Cleared), pinky2 거절은 활성', all(await both(ROW_COUNT('CMD_REJECTED', 'pinky1', 'cleared') + ' === 1 && '
                                                                                        + ROW_COUNT('CMD_REJECTED', 'pinky2', 'active') + ' === 1')))
                await pgb.click_sel('#alarmAckBtn')                                              # 다른 브라우저에서 누른다
                check('해소 알람 ACK 는 두 브라우저 모두에서 사라지고 활성 알람은 유지됨',
                      all(await both(ROW_COUNT('CMD_REJECTED', 'pinky1') + ' === 0 && ' + ROW_COUNT('CMD_REJECTED', 'pinky2', 'active') + ' === 1')))
                check('유지된 활성 알람에 ACK 한 계정이 표시됨', await pg.wait(ROW('CMD_REJECTED', 'pinky2', 'active') + "[0].children[3].textContent.startsWith('확인됨 no-auth')", 4))

                # 같은 사건이 또 일어나면 새 행 (ACK 는 행마다)
                api('POST', '/api/robots/pinky2/command', {'cmd': 'goto', 'points': ['P99']})
                check('ACK 한 뒤 같은 사건이 또 일어나면 ACK 안 된 새 행이 생김', all(await both(ROW_COUNT('CMD_REJECTED', 'pinky2', 'active') + ' === 2'))
                      and await pg.js(ROW('CMD_REJECTED', 'pinky2', 'active') + ".filter(tr => tr.children[3].textContent === '활성').length") == 1)

                # SIL: 이 브라우저의 소리만 끈다 (상태는 서버에 저장되지 않는다), 새 알람이 오면 해제
                await pg.click_sel('#alarmSilBtn')
                check('SIL 을 누르면 이 브라우저만 눌림 상태', await pg.js("app.silenced === true && " + BTN('alarmSilBtn', "getAttribute('aria-pressed')") + " === 'true'")
                      and await pgb.js("app.silenced === false && " + BTN('alarmSilBtn', "getAttribute('aria-pressed')") + " === 'false'"))
                check('SIL 은 알람 상태를 바꾸지 않음 (서버 알람 목록 그대로)', len(api('GET', '/api/alarms')[1]['alarms']) == await pg.js("app.alarms.filter(a => !a.local).length"))
                api('POST', '/api/robots/pinky2/command', {'cmd': 'goto', 'points': ['P98']})
                check('새 알람이 오면 SIL 이 해제됨', await pg.wait("app.silenced === false && " + BTN('alarmSilBtn', "getAttribute('aria-pressed')") + " === 'false'", 4))

                # 새로 접속한 화면에는 숨겨진 알람이 없다
                api('POST', '/api/robots/pinky1/command', {'cmd': 'goto', 'points': ['P97']})
                api('POST', '/api/robots/pinky1/command', {'cmd': 'stop'})
                await pgb.click_sel('#alarmAckBtn')
                await pg.call('Page.navigate', url=BASE + '/')
                check('새로고침 뒤 다시 로드', await pg.wait("app.cfg && app.wsUp", 10))
                check('새로 접속한 화면에도 ACK 로 숨긴 알람이 없음', await pg.js(ROW_COUNT('CMD_REJECTED', 'pinky1') + ' === 0'))

                # 이력 파일: 시간 순서로 쌓이고 ACK 줄이 남는다
                await asyncio.sleep(0.3)
                rows = [json.loads(l) for f in sorted(hist_dir.glob('alarms_*.jsonl')) for l in f.read_text(encoding='utf-8').splitlines()]
                acked = [r for r in rows if r['event'] == 'acked']
                check('이력에 acked 줄이 남고 계정이 기록됨', len(acked) >= 3 and all(r['acked_by'] == 'no-auth' and r['acked_at'] for r in acked), acked[:1])
                check('이력은 시간 순서로 계속 쌓임 (줄을 고치지 않음)', [r['t'] for r in rows] == sorted(r['t'] for r in rows))
                check('이력 레코드에 count 가 있음', all('count' in r for r in rows))
                sf = [r for r in rows if r['code'] == 'CMD_SEND_FAILED']
                check('조작 반복(CMD_SEND_FAILED)도 시도마다 한 줄이 시간순으로 남음 (raised 후 repeated, count 증가)',
                      [r['event'] for r in sf][:2] == ['raised', 'repeated'] and [r['count'] for r in sf][:2] == [1, 2], [(r['event'], r['count']) for r in sf])
                api('POST', '/api/mock/robot/pinky2/patrol', {'state': 'FAILED', 'detail': 'first'})
                api('POST', '/api/mock/robot/pinky2/patrol', {'state': 'FAILED', 'detail': 'second'})
                await asyncio.sleep(0.5)
                rows = [json.loads(l) for f in sorted(hist_dir.glob('alarms_*.jsonl')) for l in f.read_text(encoding='utf-8').splitlines()]
                ev = [(r['event'], r['count']) for r in rows if r['code'] == 'PATROL_FAILED'][-2:]
                check('수신 이벤트(patrol FAILED)도 발생마다 repeated 줄이 남음', ev == [('raised', 1), ('repeated', 2)], ev)
                check('알람바에는 ×2 로 묶여 보임', await pg.wait(ROW_TEXT('PATROL_FAILED', 'pinky2', 'active') + ".includes('×2')", 4))
                st, body = api('POST', '/api/alarms/ack', {'id': 'x'})
                check('잘못된 ACK 요청은 400', st == 400, (st, body))

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
        shutil.rmtree(hist_dir, ignore_errors=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--shot')
    ap.add_argument('--narrow-shot')
    a = ap.parse_args()
    asyncio.run(run(a.shot, a.narrow_shot))
    print('ALL PASS' if not FAILS else f'FAILED: {FAILS}')
    sys.exit(1 if FAILS else 0)
