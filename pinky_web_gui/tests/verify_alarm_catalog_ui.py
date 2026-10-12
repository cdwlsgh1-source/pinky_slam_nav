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
from uitext import english_left  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORT, CDP = 8020, 9238
BASE = f'http://127.0.0.1:{PORT}'
FAILS = []
HANGUL = re.compile('[가-힣]')
HEADERS = ['ID', '이름', '발생 조건', '메시지', '레벨', '소리', '사용', '최근 발생', '횟수', '편집']   # Edit 열은 manager(--no-auth 포함)에게만 보인다 (Step A5)


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
                            '--alarm-history-dir', str(hist_dir), '--alarms-local', str(hist_dir / 'alarms.local.yaml')], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            check('메뉴에 알람 버튼', await pg.js("document.getElementById('alarmMenu').textContent.trim()") == '알람')
            check('처음에는 Alarm 화면이 숨겨져 있다', await pg.js("document.getElementById('alarmView').hidden === true"))

            await pg.click_sel('#alarmMenu')
            check('Alarm 클릭: 화면이 바뀐다 (메인 작업 영역 숨김)', await pg.wait("!document.getElementById('alarmView').hidden && document.getElementById('workspace').hidden", 3))
            check('알람바와 비상 정지는 그대로 보인다', await pg.js("(() => { const v = (id) => { const e = document.getElementById(id); return !!e && e.getClientRects().length > 0; }; return v('alarmBody') && document.querySelector('.estop').getClientRects().length > 0; })()"))
            check('Alarm 메뉴가 선택 표시', await pg.js("document.getElementById('alarmMenu').classList.contains('active')"))
            want_h = json.dumps(HEADERS, ensure_ascii=False, separators=(',', ':'))
            got_h = lambda: pg.js("JSON.stringify([...document.querySelectorAll('#avTable th:not([hidden])')].map(t => t.textContent))")  # noqa: E731
            await pg.wait("document.getElementById('avEditTh').hidden === false", 4)
            check('열 제목이 한국어 (PRD FRA4-6, manager 는 편집 열 포함)', await got_h() == want_h, await got_h())
            st, cat = api('GET', '/api/alarms/catalog')
            check('표 행 수가 API 와 같다', await pg.wait(f"document.querySelectorAll('#avBody tr').length === {len(cat['alarms'])}", 4), str(len(cat['alarms'])))
            check('History 탭은 비활성, Catalog 탭이 선택', await pg.js("document.getElementById('avTabHistory').disabled && document.getElementById('avTabCatalog').getAttribute('aria-selected') === 'true'"))
            check('서버 알람 CMD_REJECTED: 레벨 L, 횟수 2', await pg.js(cell('CMD_REJECTED', 'lvl')) == 'L' and await pg.js(cell('CMD_REJECTED', 'cnt')) == '2')
            check('발생 시각이 채워져 있다', bool(re.match(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$', await pg.js(cell('CMD_REJECTED', 'last')))))
            check('아직 안 난 알람은 0회, 시각 -', await pg.js(cell('ROBOT_OFFLINE', 'cnt')) == '0' and await pg.js(cell('ROBOT_OFFLINE', 'last')) == '-')
            check('브라우저 전용 알람은 횟수 -', await pg.js(cell('SERVER_DISCONNECTED', 'cnt')) == '-' and await pg.js(cell('SERVER_DISCONNECTED', 'last')) == '-')
            check('info 알람은 소리 아니오', await pg.js(cell('CMD_SENT', 'snd')) == '아니오' and await pg.js(cell('ESTOP_FAILED', 'snd')) == '예')
            check('모든 항목이 사용 예', await pg.js("[...document.querySelectorAll('#avBody td.en')].every(t => t.textContent === '예')"))
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
            check('건수 안내 "알람 m개 중 n개"', await pg.js("document.getElementById('avNote').textContent") == f'알람 {total}개 중 {n_h}개')
            await pg.js("(() => { const s = document.getElementById('avOrigin'); s.value = 'browser'; s.dispatchEvent(new Event('change')); })()")
            n_hb = sum(1 for r in cat['alarms'] if r['level'] == 'H' and r['origin'] == 'browser')
            check('Level=H + Origin=Browser', await pg.wait(f"document.querySelectorAll('#avBody tr').length === {n_hb}", 3) and n_hb >= 3)
            await pg.click_sel('#avReset')
            check('Reset: 전부 다시', await pg.wait(f"document.querySelectorAll('#avBody tr').length === {total}", 3))
            await pg.js("(() => { const s = document.getElementById('avSearch'); s.value = 'offline'; s.dispatchEvent(new Event('input')); })()")
            check('검색 "offline": ROBOT_OFFLINE 포함', await pg.wait("!!document.querySelector('#avBody tr[data-code=\"ROBOT_OFFLINE\"]') && document.querySelectorAll('#avBody tr').length < " + str(total), 3))
            await pg.js("(() => { const s = document.getElementById('avSearch'); s.value = 'zzzz-nothing'; s.dispatchEvent(new Event('input')); })()")
            check('결과 없음: "알람이 없습니다"', await pg.wait("!document.getElementById('avEmpty').hidden && document.getElementById('avEmpty').textContent === '알람이 없습니다'", 3))
            await pg.click_sel('#avReset')

            # 열 너비 조절: 머리글 경계를 끌면 그 열만 넓어지고 새로 고침해도 유지, 더블클릭은 자동 배치로
            check('알람소리 버튼이 카탈로그 탭 옆에 있다', await pg.js("(() => { const t = document.getElementById('avTabCatalog'); const b = document.getElementById('alarmSoundBtn'); return t.nextElementSibling === b && b.textContent.trim() === '알람소리' && b.closest('#alarmView') !== null; })()"))
            check('왼쪽 메뉴에는 소리 버튼이 없다', await pg.js("![...document.querySelectorAll('.menubar .menu-item')].some(b => b.textContent.trim() === '소리' || b.id === 'alarmSoundBtn')"))
            w_of = lambda i: pg.js(f"Math.round(document.querySelectorAll('#avTable th')[{i}].getBoundingClientRect().width)")  # noqa: E731
            w0, w1 = await w_of(3), await w_of(4)
            hx = await pg.js("(() => { const r = document.querySelectorAll('#avTable th .av-rz')[3].getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
            await pg.call('Input.dispatchMouseEvent', type='mouseMoved', x=hx[0], y=hx[1])
            await pg.call('Input.dispatchMouseEvent', type='mousePressed', x=hx[0], y=hx[1], button='left', clickCount=1)
            for dx in (30, 80, 140):
                await pg.call('Input.dispatchMouseEvent', type='mouseMoved', x=hx[0] + dx, y=hx[1], buttons=1)
            await pg.call('Input.dispatchMouseEvent', type='mouseReleased', x=hx[0] + 140, y=hx[1], button='left', clickCount=1)
            w2 = await w_of(3)
            check('메시지 열을 끌면 약 140px 넓어진다', 125 <= w2 - w0 <= 155, f'{w0} -> {w2}')
            check('다른 열 너비는 그대로', await w_of(4) == w1)
            check('너비가 저장된다 (localStorage)', await pg.js("JSON.parse(localStorage.getItem('pinky.avColWidths'))[3]") == w2)
            await pg.call('Page.reload')
            await pg.wait("app.cfg && app.wsUp", 8)
            await pg.click_sel('#alarmMenu')
            await pg.wait("document.querySelectorAll('#avBody tr').length > 0", 4)
            check('새로 고침 후에도 너비 유지', abs(await w_of(3) - w2) <= 1, await w_of(3))
            await pg.js("document.querySelectorAll('#avTable th .av-rz')[3].dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))")
            check('더블클릭: 자동 배치로 복귀', await pg.js("!document.getElementById('avTable').classList.contains('fixed') && localStorage.getItem('pinky.avColWidths') === null"))

            # 읽기 전용: 값을 고치는 입력·버튼이 없다 (필터 칸과 Refresh/Reset 뿐)
            check('표 안에는 입력 칸이 없다 (편집은 Edit 버튼의 대화상자에서만, Step A5)', await pg.js("document.querySelectorAll('#avTable input, #avTable select, #avTable [contenteditable]').length === 0"))
            check('안내 문구', 'alarms.yaml' in await pg.js("document.getElementById('avFoot').textContent"))

            # 고정 문구는 한국어 (알람 이름·조건·메시지 칸 제외)
            fixed = await pg.js("JSON.stringify([...document.querySelectorAll('#alarmView th, #alarmView button, #alarmView label, #alarmView option, #avNote, #avFoot, #avEmpty')].map(e => e.textContent).concat([document.getElementById('avSearch').placeholder, document.getElementById('alarmMenu').textContent]))")
            check('Alarm 화면 고정 문구에 영문 문장 없음 (한국어)', not english_left(fixed), english_left(fixed))
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
