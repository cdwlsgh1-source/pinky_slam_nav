"""Step 3 화면을 headless Chrome(CDP)으로 확인한다. mock 서버를 직접 띄우고 정리한다.

실행: .venv/bin/python tests/verify_step3_ui.py [--shot docs/step3_screenshot.png]   (google-chrome 필요, 없으면 SKIP)
확인: 카드 버튼 활성 조건, 지점 클릭으로 경로 추가(좌표 기록과 분리), 확인 팝업 요약, 1초 연타 방지,
      진행 문구, 이력, 순찰 중 start 재전송 시 5초 후 "응답 없음", STARTING 안내, RED API 400.
"""
import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
PORT, CDP = 8013, 9233
FAILS = []


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {extra}' if extra and not cond else ''))
    if not cond:
        FAILS.append(name)


class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({'id': self.n, 'method': method, 'params': params}))
        while True:
            m = json.loads(await self.ws.recv())
            if m.get('id') == self.n:
                if 'error' in m:
                    raise RuntimeError(m['error'])
                return m['result']

    async def js(self, expr):
        r = await self.call('Runtime.evaluate', expression=expr, awaitPromise=True, returnByValue=True)
        if 'exceptionDetails' in r:
            raise RuntimeError(r['exceptionDetails'])
        return r['result'].get('value')

    async def wait(self, expr, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if await self.js(expr):
                    return True
            except RuntimeError:  # 페이지가 아직 로드되지 않았을 때
                pass
            await asyncio.sleep(0.1)
        return False

    async def click_point(self, name):
        """지도 위 goto 포인트를 실제 마우스 이벤트로 클릭한다."""
        xy = await self.js(f"""(() => {{ const p = gotoPoints().find(q => q.name === '{name}'); const s = worldToScreen(p.x, p.y);
          const r = canvas.getBoundingClientRect(); return [r.left + s.sx, r.top + s.sy]; }})()""")
        await self.mouse(xy[0], xy[1])

    async def mouse(self, x, y):
        await self.call('Input.dispatchMouseEvent', type='mouseMoved', x=x, y=y)   # 페이지가 막 다시 로드된 직후에는 첫 클릭이 무시될 수 있어 먼저 움직인다
        for t in ('mousePressed', 'mouseReleased'):
            await self.call('Input.dispatchMouseEvent', type=t, x=x, y=y, button='left', clickCount=1)

    async def click_sel(self, sel):
        # 사이드바는 탭이라 다른 탭의 요소는 숨겨져 있다: 그 요소가 든 탭을 먼저 연다
        await self.js(f"(() => {{ const s = document.querySelector({json.dumps(sel)}).closest('.sidebar > section[data-tab]'); if (s && s.hidden) app.showTab(s.dataset.tab); }})()")
        # 사이드바는 스크롤되므로, 화면 밖에 있으면 먼저 화면 안으로 가져온다 (좌표 클릭이 빗나가지 않게)
        await self.js(f"document.querySelector({json.dumps(sel)}).scrollIntoView({{block: 'center'}})")
        await asyncio.sleep(0.05)
        xy = await self.js(f"(() => {{ const r = document.querySelector({json.dumps(sel)}).getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; }})()")
        await self.mouse(xy[0], xy[1])


CARD_BTN = lambda rid, i: f"#robotCards .card:nth-child({1 + ['pinky1', 'pinky2'].index(rid)}) .cmd-row:not(.estop-row) button:nth-child({i})"  # noqa: E731


async def run(shot):
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
    try:
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{CDP}/json'))
                urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/health')
                break
            except Exception:
                time.sleep(0.1)
        tab = next(t for t in tabs if t['type'] == 'page')
        async with websockets.connect(tab['webSocketDebuggerUrl'], max_size=None) as ws:
            pg = Page(ws)
            await pg.call('Page.enable')
            await pg.call('Emulation.setDeviceMetricsOverride', width=1400, height=900, deviceScaleFactor=1, mobile=False)
            await pg.call('Page.navigate', url=f'http://127.0.0.1:{PORT}/')
            check('페이지 로드와 설정/지도/스냅샷', await pg.wait("app.cfg && app.meta && app.order.length === 2 && app.wsUp"))
            await asyncio.sleep(0.5)

            # 초기 상태: IDLE 이므로 start/goto(경로 있을 때)만, stop 은 비활성
            check('IDLE: 순찰 시작 활성, 정지 비활성', await pg.js(f"""!document.querySelector('{CARD_BTN("pinky1", 1)}').disabled
              && document.querySelector('{CARD_BTN("pinky1", 2)}').disabled"""))
            check('경로가 비어 있으면 경로 이동 비활성', await pg.js(f"document.querySelector('{CARD_BTN('pinky1', 3)}').disabled"))
            check('카드에 복귀/대기 표시', await pg.js("document.querySelector('#robotCards .card:nth-child(2) .meta').textContent.includes('복귀: P7')"))

            # 지점 클릭 -> 경로 추가, 좌표 기록은 늘지 않음
            clicks0 = await pg.js('app.clicks.length')
            for n in ('P2', 'P3', 'P6'):
                await pg.click_point(n)
            check('포인트 클릭으로 경로 추가', await pg.js("JSON.stringify(app.routes.pinky1) === '[\"P2\",\"P3\",\"P6\"]'"))
            check('포인트 클릭은 좌표 기록을 늘리지 않음', await pg.js('app.clicks.length') == clicks0)
            # 지도 안쪽의 빈 곳 (포인트에서 먼 곳). 캔버스 모서리는 레이아웃에 따라 지도 밖일 수 있어서 월드 좌표로 고른다
            await pg.mouse(*(await pg.js("(() => { const s = worldToScreen(0.3, -0.3); const r = canvas.getBoundingClientRect(); return [r.left + s.sx, r.top + s.sy]; })()")))
            check('빈 곳 클릭은 좌표 기록', await pg.js('app.clicks.length') == clicks0 + 1)
            await asyncio.sleep(0.6)
            check('경로 목록 UI 3개', await pg.js("document.querySelectorAll('#routeList li').length") == 3)
            check('경로 이동 활성', await pg.js(f"!document.querySelector('{CARD_BTN('pinky1', 3)}').disabled"))
            # 순서 변경/삭제
            await pg.js("document.querySelector('#routeList li:nth-child(3) button').click()")  # P6 ▲
            check('▲ 순서 변경', await pg.js("JSON.stringify(app.routes.pinky1) === '[\"P2\",\"P6\",\"P3\"]'"))
            await pg.js("document.querySelector('#routeList li:nth-child(2) button:nth-of-type(3)').click()")  # P6 삭제
            check('삭제', await pg.js("JSON.stringify(app.routes.pinky1) === '[\"P2\",\"P3\"]'"))
            await pg.click_point('P6')

            # 확인 팝업: 취소하면 전송 안 됨, 확인하면 전송
            await pg.js(f"document.querySelector('{CARD_BTN('pinky1', 3)}').click()")
            check('goto 확인 팝업이 열림', await pg.wait("document.getElementById('confirmDlg').open"))
            summary = await pg.js("document.getElementById('confirmText').textContent")
            check('요약 문구', summary.startswith('pinky1: P2 → P3 → P6 → 홈(P1), 지점마다 10') and '초 대기' in summary, summary)
            await pg.js("document.querySelector('#confirmDlg button[value=cancel]').click()")
            await asyncio.sleep(0.4)
            check('취소하면 이력 없음', await pg.js('app.history.length') == 0)
            await pg.js(f"document.querySelector('{CARD_BTN('pinky1', 3)}').click()")
            await pg.wait("document.getElementById('confirmDlg').open")
            await pg.js("document.getElementById('confirmOk').click()")
            check('보내면 이력에 goto 기록', await pg.wait("app.history.length === 1 && app.history[0].cmd === 'goto'"))
            check('연타 방지: 1초 잠금', await pg.js(f"document.querySelector('{CARD_BTN('pinky1', 3)}').disabled") is True)
            check('상태 STARTING + 응답 확인', await pg.wait("app.robots.pinky1.patrol && app.robots.pinky1.patrol.state === 'STARTING' && app.history[0].result === 'acknowledged'"))
            check('작업 중: 정지 활성, 시작 비활성', await pg.js(f"""!document.querySelector('{CARD_BTN("pinky1", 2)}').disabled
              && document.querySelector('{CARD_BTN("pinky1", 1)}').disabled"""))
            check('다른 로봇 버튼은 영향 없음', await pg.js(f"!document.querySelector('{CARD_BTN('pinky2', 1)}').disabled"))
            # STARTING 5초 초과 -> 안내 (mock 첫 작업 보정 6초)
            check('STARTING 5초 넘으면 시작 준비 안내', await pg.wait("document.querySelector('#robotCards .card:nth-child(1) .notice').style.display === 'block'", 9))
            check('안내 문구', await pg.js("document.querySelector('#robotCards .card:nth-child(1) .notice').textContent.includes('초기 위치 보정')"))
            # 진행 문구
            check('goto 진행 문구 ("지점 n/3")', await pg.wait("/지점 \\d\\/3/.test(document.querySelector('#robotCards .card:nth-child(1) dd').textContent)", 15))
            await pg.call('Page.captureScreenshot')
            # 작업 중 start 재전송은 확인 팝업 없이 UI 가 막지만, API 로 직접 보내면 "응답 없음"
            r = await pg.js("fetch('/api/robots/pinky1/command', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cmd:'start'})}).then(r => r.status)")
            check('작업 중 start API 는 200 (노드가 무시)', r == 200)
            check('5초 후 응답 없음 (이력 + 카드 경고)', await pg.wait("app.history[0].cmd === 'start' && app.history[0].result === 'no_response' && document.querySelector('#robotCards .card:nth-child(1) p:nth-of-type(3)').style.display === 'block'", 9))
            # 검증 거절
            r = await pg.js("fetch('/api/robots/pinky1/command', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({cmd:'goto', points:['RED1IN']})}).then(r => r.status)")
            check('RED1IN 포함 goto -> 400', r == 400)
            check('거절이 이력에 표시', await pg.wait("app.history[0].result === 'rejected'"))
            # 정지
            await pg.js(f"document.querySelector('{CARD_BTN('pinky1', 2)}').click()")
            check('정지 -> STOPPED', await pg.wait("app.robots.pinky1.patrol.state === 'STOPPED'", 10))
            check('STOPPED 배지 회색', await pg.js("document.querySelector('#robotCards .card:nth-child(1) .state-badge').dataset.level") == 'idle')
            # start (확인 팝업 거쳐서)
            await pg.js(f"document.querySelector('{CARD_BTN('pinky2', 1)}').click()")
            await pg.wait("document.getElementById('confirmDlg').open")
            check('start 팝업 요약', (await pg.js("document.getElementById('confirmText').textContent")).startswith('pinky2: 순찰 시작 (RED2IN → P3'))
            await pg.js("document.getElementById('confirmOk').click()")
            check('pinky2 start 진행 표시', await pg.wait("app.robots.pinky2.patrol && app.robots.pinky2.patrol.state !== 'IDLE'", 5))
            check('pinky1 은 그대로 STOPPED', await pg.js("app.robots.pinky1.patrol.state") == 'STOPPED')
            # 로봇 offline 시 그 로봇 버튼만 비활성
            await pg.js("app.robots.pinky2.online = false; app.dirtyCards = true;")
            await asyncio.sleep(0.6)
            check('offline 로봇 버튼 전부 비활성', await pg.js(f"[1,2,3].every(i => document.querySelector('{CARD_BTN('pinky2', 1)}'.replace('1)', i + ')')).disabled)"))
            check('다른 로봇은 유지', await pg.js(f"!document.querySelector('{CARD_BTN('pinky1', 1)}').disabled"))
            await pg.js("app.robots.pinky2.online = true; app.dirtyCards = true;")
            if shot:
                await asyncio.sleep(1.0)
                data = (await pg.call('Page.captureScreenshot', format='png'))['data']
                import base64
                Path(shot).write_bytes(base64.b64decode(data))
                print('스크린샷:', shot)
    finally:
        br.terminate()
        srv.terminate()
        shutil.rmtree(prof, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shot', default=None)
    args = ap.parse_args()
    asyncio.run(run(args.shot))
    print('FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASS')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
