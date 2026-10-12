"""알람 카탈로그 화면(Step A4)을 headless Chrome(CDP)으로 확인한다. mock 서버(--no-auth)를 직접 만들고 정리한다.

실행: .venv/bin/python tests/verify_alarm_catalog_ui.py [--shot docs/alarm_a4.png] [--narrow-shot docs/alarm_a4_narrow.png]   (google-chrome 필요, 없으면 SKIP)
확인: 왼쪽 메뉴 Alarm 으로 화면 전환(알람바와 비상 정지는 그대로), 열 제목, API 와 표 행 일치, 발생 횟수 갱신, 필터·검색, History 탭 비활성,
      읽기 전용(입력 칸·편집 버튼 없음), 고정 문구에 한글 없음, 860px 이하에서 가로 스크롤 없음, 메인으로 돌아오기.
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
PORT, CDP = 8020, 9238
BASE = f'http://127.0.0.1:{PORT}'
FAILS = []
HANGUL = re.compile('[가-힣]')
HEADERS = ['ID', 'Name', 'Condition', 'Message', 'Level', 'Sound', 'Enabled', 'Last raised', 'Count']


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


def cell(code, col):
    return f"(() => {{ const tr = document.querySelector('#avBody tr[data-code=\"{code}\"]'); return tr ? tr.querySelector('td.{col}').textContent : null; }})()"


async def run(shot, narrow_shot):
    chrome = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not chrome:
        print('SKIP: Chrome 이 없다')
        return
    hist_dir, prof = Path(tempfile.mkdtemp()), tempfile.mkdtemp()
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--mock', '--mock-wait-scale', '0.2', '--port', str(PORT), '--no-auth',
                            '--alarm-history-dir', str(hist_dir)], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', f'--remote-debugging-port={CDP}',
                           f'--user-data-dir={prof}', '--window-size=1400,900', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def screenshot(pg, path):
        Path(path).write_bytes(base64.b64decode((await pg.call('Page.captureScreenshot', format='png'))['data']))
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
            check('페이지 로드', await pg.wait("app.cfg && app.order.length === 2 && app.wsUp"))
            # 알람 몇 개: 거절 두 번(같은 알람이 ACK 전이라 묶임, 발생은 2회)
            for _ in range(2):
                api('POST', '/api/robots/pinky1/command', {'cmd': 'bogus'})
            check('메뉴에 Alarm 버튼', await pg.js("document.getElementById('alarmMenu').textContent.trim()") == 'Alarm')
            check('처음에는 Alarm 화면이 숨겨져 있다', await pg.js("document.getElementById('alarmView').hidden === true"))

            await pg.click_sel('#alarmMenu')
            check('Alarm 클릭: 화면이 바뀐다 (메인 작업 영역 숨김)', await pg.wait("!document.getElementById('alarmView').hidden && document.getElementById('workspace').hidden", 3))
            check('알람바와 비상 정지는 그대로 보인다', await pg.js("(() => { const v = (id) => { const e = document.getElementById(id); return !!e && e.getClientRects().length > 0; }; return v('alarmBody') && document.querySelector('.estop').getClientRects().length > 0; })()"))
            check('Alarm 메뉴가 선택 표시', await pg.js("document.getElementById('alarmMenu').classList.contains('active')"))
            check('열 제목이 영문 9개 (PRD FRA4-6)', await pg.js("JSON.stringify([...document.querySelectorAll('#avTable th')].map(t => t.textContent))") == json.dumps(HEADERS, separators=(',', ':')))
            st, cat = api('GET', '/api/alarms/catalog')
            check('표 행 수가 API 와 같다', await pg.wait(f"document.querySelectorAll('#avBody tr').length === {len(cat['alarms'])}", 4), str(len(cat['alarms'])))
            check('History 탭은 비활성, Catalog 탭이 선택', await pg.js("document.getElementById('avTabHistory').disabled && document.getElementById('avTabCatalog').getAttribute('aria-selected') === 'true'"))
            check('서버 알람 CMD_REJECTED: 레벨 L, 횟수 2', await pg.js(cell('CMD_REJECTED', 'lvl')) == 'L' and await pg.js(cell('CMD_REJECTED', 'cnt')) == '2')
            check('발생 시각이 채워져 있다', bool(re.match(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$', await pg.js(cell('CMD_REJECTED', 'last')))))
            check('아직 안 난 알람은 0회, 시각 -', await pg.js(cell('ROBOT_OFFLINE', 'cnt')) == '0' and await pg.js(cell('ROBOT_OFFLINE', 'last')) == '-')
            check('브라우저 전용 알람은 횟수 -', await pg.js(cell('SERVER_DISCONNECTED', 'cnt')) == '-' and await pg.js(cell('SERVER_DISCONNECTED', 'last')) == '-')
            check('info 알람은 Sound No', await pg.js(cell('CMD_SENT', 'snd')) == 'No' and await pg.js(cell('ESTOP_FAILED', 'snd')) == 'Yes')
            check('모든 항목이 Enabled Yes', await pg.js("[...document.querySelectorAll('#avBody td.en')].every(t => t.textContent === 'Yes')"))
            check('비상정지 알람이 맨 위(HH)', (await pg.js("document.querySelector('#avBody tr').dataset.code")) in ('ESTOP_FAILED', 'ESTOP_PARTIAL'))

            # 발생 횟수는 화면이 열려 있는 동안 따라간다 (Refresh 버튼으로도)
            api('POST', '/api/robots/pinky1/command', {'cmd': 'bogus'})
            await pg.click_sel('#avRefresh')
            check('Refresh: 횟수 3', await pg.wait(cell('CMD_REJECTED', 'cnt') + " === '3'", 4))

            # 필터와 검색
            total = len(cat['alarms'])
            await pg.js("(() => { const s = document.getElementById('avLevel'); s.value = 'H'; s.dispatchEvent(new Event('change')); })()")
            n_h = sum(1 for r in cat['alarms'] if r['level'] == 'H')
            check('Level=H 필터', await pg.wait(f"document.querySelectorAll('#avBody tr').length === {n_h}", 3) and await pg.js("[...document.querySelectorAll('#avBody td.lvl')].every(t => t.textContent === 'H')"))
            check('건수 안내 "n of m alarms"', await pg.js("document.getElementById('avNote').textContent") == f'{n_h} of {total} alarms')
            await pg.js("(() => { const s = document.getElementById('avOrigin'); s.value = 'browser'; s.dispatchEvent(new Event('change')); })()")
            n_hb = sum(1 for r in cat['alarms'] if r['level'] == 'H' and r['origin'] == 'browser')
            check('Level=H + Origin=Browser', await pg.wait(f"document.querySelectorAll('#avBody tr').length === {n_hb}", 3) and n_hb >= 3)
            await pg.click_sel('#avReset')
            check('Reset: 전부 다시', await pg.wait(f"document.querySelectorAll('#avBody tr').length === {total}", 3))
            await pg.js("(() => { const s = document.getElementById('avSearch'); s.value = 'offline'; s.dispatchEvent(new Event('input')); })()")
            check('검색 "offline": ROBOT_OFFLINE 포함', await pg.wait("!!document.querySelector('#avBody tr[data-code=\"ROBOT_OFFLINE\"]') && document.querySelectorAll('#avBody tr').length < " + str(total), 3))
            await pg.js("(() => { const s = document.getElementById('avSearch'); s.value = 'zzzz-nothing'; s.dispatchEvent(new Event('input')); })()")
            check('결과 없음: "No alarms found"', await pg.wait("!document.getElementById('avEmpty').hidden && document.getElementById('avEmpty').textContent === 'No alarms found'", 3))
            await pg.click_sel('#avReset')

            # 읽기 전용: 값을 고치는 입력·버튼이 없다 (필터 칸과 Refresh/Reset 뿐)
            check('읽기 전용: 표 안에 입력 칸·버튼 없음', await pg.js("document.querySelectorAll('#avTable input, #avTable button, #avTable select, #avTable [contenteditable]').length === 0"))
            check('안내 문구에 Read-only', 'Read-only' in await pg.js("document.getElementById('avFoot').textContent"))

            # 고정 문구에 한글 없음 (알람 이름·조건·메시지 칸 제외)
            fixed = await pg.js("JSON.stringify([...document.querySelectorAll('#alarmView th, #alarmView button, #alarmView label, #alarmView option, #avNote, #avFoot, #avEmpty')].map(e => e.textContent).concat([document.getElementById('avSearch').placeholder, document.getElementById('alarmMenu').textContent]))")
            check('Alarm 화면 고정 문구에 한글 없음', not HANGUL.search(fixed), fixed[:200])
            if shot:
                await screenshot(pg, shot)

            # 좁은 화면 (860px 이하): 페이지 가로 스크롤 없음
            await pg.call('Emulation.setDeviceMetricsOverride', width=820, height=900, deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(0.4)
            check('820px: 페이지 가로 스크롤 없음', await pg.js("document.documentElement.scrollWidth <= window.innerWidth + 1"), str(await pg.js("document.documentElement.scrollWidth")))
            check('820px: 비상 정지가 화면 안에 있다', await pg.js("(() => { const r = document.querySelector('.estop').getBoundingClientRect(); return r.right <= window.innerWidth + 1 && r.width > 0; })()"))
            if narrow_shot:
                await screenshot(pg, narrow_shot)
            await pg.call('Emulation.setDeviceMetricsOverride', width=1400, height=900, deviceScaleFactor=1, mobile=False)

            # 메인으로 돌아오기, 로그로 갔다가 Alarm 으로
            await pg.click_sel('.menu-item[data-view="main"]')
            check('메인으로 돌아오면 Alarm 화면이 숨겨진다', await pg.wait("document.getElementById('alarmView').hidden && !document.getElementById('workspace').hidden", 3))
            await pg.click_sel('#logMenu')
            check('로그 화면: Alarm 화면은 숨김', await pg.wait("!document.getElementById('logView').hidden && document.getElementById('alarmView').hidden", 3))
            await pg.click_sel('#alarmMenu')
            check('로그에서 Alarm 으로: 로그 숨김', await pg.wait("document.getElementById('logView').hidden && !document.getElementById('alarmView').hidden", 3))
            check('화면을 벗어나면 5초 갱신이 멈춘다', await pg.js("(() => { document.querySelector('.menu-item[data-view=\"main\"]').click(); return catalog.timer === null; })()"))
    finally:
        for p in (br, srv):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        shutil.rmtree(hist_dir, ignore_errors=True)
        shutil.rmtree(prof, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shot')
    ap.add_argument('--narrow-shot')
    a = ap.parse_args()
    asyncio.run(run(a.shot, a.narrow_shot))
    print('실패 %d건' % len(FAILS) if FAILS else '모두 통과')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
