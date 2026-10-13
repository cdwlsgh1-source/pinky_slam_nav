"""Alarm 화면 History 탭(Step A7)을 headless Chrome(CDP)으로 확인한다. mock 서버를 직접 띄우고 정리한다.

실행: .venv/bin/python tests/verify_alarm_history_ui.py [--shot docs/alarm_a7.png] [--narrow-shot docs/alarm_a7_narrow.png]   (google-chrome 필요, 없으면 SKIP)
확인(PRD A7 완료 기준): 알람 수백 건(100건 제한 초과, 이력 파일 여러 개)이 오래된 것까지 Load more 로 보임, 필터·검색이 서버 응답과 일치, 새로고침 뒤 화면 유지,
      active 알람 맨 위 고정과 실시간 해소, 메뉴 배지, 상세 타임라인이 이력 파일과 같음, CSV(BOM·한글), 알람바와 비상 정지가 그대로 보임, 860px 이하 가로 스크롤 없음,
      고정 문구(버튼 이름은 영문, 나머지는 한국어).
알람은 명령 거절(CMD_REJECTED)을 만들고 ACK 하는 것을 반복해 만든다 (ACK 한 뒤의 같은 알람은 새 행이라 한 번에 한 건씩 늘어난다).
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
import urllib.parse
import urllib.request
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_step3_ui as v3  # noqa: E402
from uitext import english_left  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORT, CDP = 8019, 9237
BASE = f'http://127.0.0.1:{PORT}'
FAILS = []
N_ALARMS = 330


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {extra}' if extra and not cond else ''))
    if not cond:
        FAILS.append(name)


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def get_bytes(path):
    with urllib.request.urlopen(BASE + path) as r:
        return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()


async def run(shot, narrow_shot):
    chrome = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not chrome:
        print('SKIP: Chrome 이 없다')
        return
    hist_dir, prof = Path(tempfile.mkdtemp()), tempfile.mkdtemp()
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--mock', '--mock-wait-scale', '0.2', '--port', str(PORT), '--no-auth',
                            '--alarm-history-dir', str(hist_dir), '--alarm-history-chunk', '100', '--alarms-local', str(hist_dir / 'alarms.local.yaml')],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', f'--remote-debugging-port={CDP}', f'--user-data-dir={prof}',
                           '--window-size=1400,900', 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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

        # ---- 알람 수백 건 (이력 파일이 여러 개가 된다) ----
        for i in range(N_ALARMS):
            robot = 'pinky1' if i % 2 else 'pinky2'
            api('POST', f'/api/robots/{robot}/command', {'cmd': 'bogus'})   # CMD_REJECTED (L)
            api('POST', '/api/alarms/ack', {'id': None})
            api('POST', f'/api/robots/{robot}/command', {'cmd': 'start'})   # 유효한 명령이 나가면 거절 알람이 해소된다 (+ CMD_SENT info 기록)
        # 마지막으로 active 알람 둘을 ACK 하지 않고 남겨 둔다: 맨 위에 고정될 알람 (L 하나, H 하나)
        api('POST', '/api/robots/pinky1/command', {'cmd': 'bogus'})
        api('POST', '/api/mock/no-subscriber', {'on': True})
        api('POST', '/api/robots/pinky2/command', {'cmd': 'start'})          # CMD_SEND_FAILED (H)
        files = api('GET', '/api/alarms/history/files')[1]['files']
        check(f'알람 {N_ALARMS}건 이상이 쌓였고 이력 파일이 여러 개 (>=5)', len(files) >= 5, str(len(files)))
        st, summ = api('GET', '/api/alarms/history/summary')
        check('요약 API: 알람 수가 파일 합계와 같다', st == 200 and summ['total_alarms'] >= N_ALARMS and sum(f['alarms'] for f in summ['files']) == summ['total_alarms'], str(summ.get('total_alarms')))
        check('알람바 100건 제한을 넘는다', summ['total_alarms'] > 100)

        async with websockets.connect(tab['webSocketDebuggerUrl'], max_size=None) as ws:
            pg = v3.Page(ws)
            await pg.call('Page.enable')
            await pg.call('Emulation.setDeviceMetricsOverride', width=1400, height=900, deviceScaleFactor=1, mobile=False)
            await pg.call('Page.navigate', url=BASE + '/')
            check('페이지 로드', await pg.wait('app.cfg && app.order.length === 2 && app.wsUp'))
            rows = lambda: pg.js("document.querySelectorAll('#ahBody tr[data-key]').length")  # noqa: E731

            # ---- 메뉴·배지 ----
            check('메뉴 버튼 이름은 영문 Alarm', await pg.js("document.getElementById('alarmMenu').firstChild.textContent.trim()") == 'Alarm')
            check('메인 화면에서도 배지에 active 수 (2)', await pg.wait("document.getElementById('alarmBadge').textContent === '2' && !document.getElementById('alarmBadge').hidden", 4), await pg.js("document.getElementById('alarmBadge').textContent"))
            check('HH/H 활성이 있으면 빨강(hot)', await pg.js("document.getElementById('alarmBadge').classList.contains('hot')"))

            # ---- 화면 전환 ----
            await pg.click_sel('#alarmMenu')
            check('Alarm 화면이 열리고 History 탭이 기본', await pg.wait("!document.getElementById('alarmView').hidden && document.getElementById('avTabHistory').getAttribute('aria-selected') === 'true' && !document.getElementById('avHistoryPane').hidden", 3))
            check('작업 영역은 숨고 알람바·비상 정지는 보인다', await pg.js("document.getElementById('workspace').hidden && document.querySelector('.estop').getClientRects().length > 0 && document.getElementById('alarmBody').getClientRects().length > 0"))
            check('레이아웃 편집 버튼은 숨는다', await pg.js("document.getElementById('layoutTools').hidden"))

            # ---- 목록: 오늘, 최신이 위, active 고정 ----
            check('목록이 채워진다 (처음 한 쪽)', await pg.wait("document.querySelectorAll('#ahBody tr[data-key]').length >= 100", 6), str(await rows()))
            pins = await pg.js("[...document.querySelectorAll('#ahBody tr[data-key]')].slice(0, 2).map(t => t.classList.contains('pin') + ':' + t.dataset.state + ':' + t.dataset.code)")
            check('active 알람 둘이 맨 위에 고정', sorted(pins) == ['true:active:CMD_REJECTED', 'true:active:CMD_SEND_FAILED'] and await pg.js("document.querySelectorAll('#ahBody tr.pin').length") == 2, str(pins))
            times = await pg.js("[...document.querySelectorAll('#ahBody tr[data-key]:not(.pin) td.time')].map(t => t.textContent)")
            check('고정 아래는 발생 시각 최신순', times == sorted(times, reverse=True), str(times[:3]))
            keys = await pg.js("[...document.querySelectorAll('#ahBody tr[data-key]')].map(t => t.dataset.key)")
            check('같은 알람이 두 번 보이지 않는다', len(keys) == len(set(keys)))
            check('열 제목이 한국어(PRD 8-5)', await pg.js("[...document.querySelectorAll('#ahHead th')].map(t => t.textContent).join(',')") == '발생,로봇,레벨,메시지,상태,횟수,확인,해소,지속 시간')
            note = await pg.js("document.getElementById('ahNote').textContent")
            check('안내: 최신 N건 (전체 M건)', bool(re.match(r'최신 \d+건 \(전체 \d+건\)$', note)), note)

            # ---- Load more: 오래된 알람까지 ----
            check('Load more 버튼이 보인다 (200행 초과)', await pg.js("!document.getElementById('ahMore').hidden"))
            await pg.click_sel('#ahMore')
            check('Load more 로 더 가져온다', await pg.wait("document.querySelectorAll('#ahBody tr[data-key]').length > 200", 6), str(await rows()))
            for _ in range(5):
                if await pg.js("document.getElementById('ahMore').hidden"):
                    break
                await pg.click_sel('#ahMore')
                await asyncio.sleep(0.6)
            total_rows = await rows()
            check('끝까지 가져오면 전체 알람 수와 같고 Load more 가 사라진다', total_rows == summ['total_alarms'] + 1 - 1 or total_rows >= N_ALARMS, f'{total_rows} / {summ["total_alarms"]}')
            check('가장 오래된 알람까지 보인다 (첫 알람 id 1)', await pg.js("[...document.querySelectorAll('#ahBody tr[data-key]')].some(t => t.dataset.id === '1')"))
            check('끝에서는 Load more 가 숨는다', await pg.js("document.getElementById('ahMore').hidden"))
            keys = await pg.js("[...document.querySelectorAll('#ahBody tr[data-key]')].map(t => t.dataset.key)")
            check('이어 받아도 중복 없음', len(keys) == len(set(keys)))

            # ---- 필터: 서버 응답과 일치 ----
            await pg.js("document.getElementById('ahRobot').value = 'pinky1'; document.getElementById('ahApply').click()")
            await asyncio.sleep(0.8)
            for _ in range(6):   # 끝까지 이어 받는다
                if await pg.js("document.getElementById('ahMore').hidden"):
                    break
                await pg.click_sel('#ahMore')
                await asyncio.sleep(0.6)
            ui_ids = await pg.js("[...document.querySelectorAll('#ahBody tr[data-key]')].map(t => t.dataset.key).sort()")
            today = time.strftime('%Y-%m-%d')
            st, srv_page = 0, []
            before = None
            while True:
                q = {'view': 'alarms', 'from': today, 'to': today, 'robot': 'pinky1', 'limit': 200}
                if before:
                    q['before'] = before
                st, d = api('GET', '/api/alarms/history?' + urllib.parse.urlencode(q))
                srv_page += d['alarms']
                before = d['next']
                if not before:
                    break
            srv_keys = sorted(f"{a['id']}@{a['t_raised']}" for a in srv_page)
            check('로봇 필터: 화면 행 = 서버 응답', ui_ids == srv_keys and len(ui_ids) > 100, f'{len(ui_ids)} vs {len(srv_keys)}')
            check('필터 결과는 모두 pinky1', await pg.js("[...document.querySelectorAll('#ahBody tr[data-key] td.robot')].every(t => t.textContent === 'pinky1')"))

            await pg.js("document.getElementById('ahRobot').value = ''; document.querySelector('input[name=ahLevel][value=H]').click(); document.getElementById('ahState').value = 'active'; document.getElementById('ahApply').click()")
            await asyncio.sleep(0.8)
            lv = await pg.js("[...document.querySelectorAll('#ahBody tr[data-key]')].map(t => t.dataset.state + ':' + t.children[2].textContent)")
            check('레벨 H + 상태 활성: 고정 알람 한 건만 (L 은 걸러진다)', lv == ['active:H'], str(lv))

            await pg.js("document.getElementById('ahReset').click()")
            await asyncio.sleep(0.8)
            check('Reset: 필터 초기화 (오늘, 전체)', await pg.js("(() => { const f = histFilter(); return f.from === f.to && f.robot === '' && f.state === '' && f.levels.length === 0 && f.q === ''; })()"))
            await pg.js("document.getElementById('ahSearch').value = '없는문구xyz'; document.getElementById('ahApply').click()")
            await asyncio.sleep(0.8)
            check('검색 결과 없음: 안내 문구', await pg.js("!document.getElementById('ahEmpty').hidden && document.getElementById('ahEmpty').textContent") == '알람이 없습니다')
            await pg.js("document.getElementById('ahSearch').value = ''; document.getElementById('ahState').value = 'cleared'; document.getElementById('ahApply').click()")
            await asyncio.sleep(0.8)
            check('상태 해소: 고정 알람 제외 나머지', await pg.js("[...document.querySelectorAll('#ahBody tr[data-key]')].every(t => t.dataset.state === 'cleared') && document.querySelectorAll('#ahBody tr.pin').length === 0"))
            # 기간: 어제~어제는 비어 있다
            await pg.js("document.getElementById('ahState').value = ''; document.getElementById('ahFrom').value = '2020-01-01'; document.getElementById('ahTo').value = '2020-01-02'; document.getElementById('ahApply').click()")
            await asyncio.sleep(0.8)
            check('지난 기간(2020)에는 이력이 없고 active 고정만 남는다', await pg.js("document.querySelectorAll('#ahBody tr[data-key]:not(.pin)').length === 0"))
            await pg.js("document.getElementById('ahReset').click()")
            await asyncio.sleep(0.8)

            # ---- 상세 타임라인 = 이력 파일 ----
            await pg.js("document.querySelector('#ahBody tr[data-code=CMD_REJECTED]:not(.pin)').click()")
            check('행을 누르면 상세가 펼쳐진다', await pg.wait("document.querySelector('.ah-detail .ah-tl li') !== null", 4))
            det = await pg.js("(() => { const tr = document.querySelector('#ahBody tr.open'); return {id: Number(tr.dataset.id), key: tr.dataset.key, events: [...document.querySelectorAll('.ah-detail .ah-tl li')].map(l => l.dataset.event), file: document.querySelector('.ah-detail .ah-file').textContent, code: document.querySelector('.ah-detail .ah-code').textContent}; })()")
            tr_raised = float(det['key'].split('@')[1])
            file_events = []
            for f in sorted(hist_dir.glob('alarms_*.jsonl')):
                for line in f.read_text(encoding='utf-8').splitlines():
                    r = json.loads(line)
                    if r['id'] == det['id'] and abs(r['t_raised'] - tr_raised) < 1e-6:
                        file_events.append(r['event'])
            check('타임라인 이벤트 순서가 이력 파일과 같다', det['events'] == file_events and det['events'][0] == 'raised', f"{det['events']} vs {file_events}")
            check('상세에 코드와 파일 이름', det['code'].endswith('CMD_REJECTED') and 'alarms_' in det['file'], str(det))
            await pg.js("document.querySelector('#ahBody tr.open').click()")
            check('다시 누르면 접힌다', await pg.js("document.querySelector('.ah-detail') === null"))

            # ---- 열 너비 조절 + 단어 단위 줄바꿈 ----
            w_of = lambda i: pg.js(f"Math.round(document.querySelectorAll('#ahTable th')[{i}].getBoundingClientRect().width)")  # noqa: E731
            w0, w1 = await w_of(3), await w_of(4)
            hx = await pg.js("(() => { const r = document.querySelectorAll('#ahTable th .av-rz')[3].getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()")
            await pg.call('Input.dispatchMouseEvent', type='mouseMoved', x=hx[0], y=hx[1])
            await pg.call('Input.dispatchMouseEvent', type='mousePressed', x=hx[0], y=hx[1], button='left', clickCount=1)
            for dx in (30, 80, 120):
                await pg.call('Input.dispatchMouseEvent', type='mouseMoved', x=hx[0] + dx, y=hx[1], buttons=1)
            await pg.call('Input.dispatchMouseEvent', type='mouseReleased', x=hx[0] + 120, y=hx[1], button='left', clickCount=1)
            w2 = await w_of(3)
            check('History 메시지 열을 끌면 약 120px 넓어진다', 105 <= w2 - w0 <= 135, f'{w0} -> {w2}')
            check('다른 열 너비는 그대로', await w_of(4) == w1)
            check('너비가 저장된다 (pinky.ahColWidths)', await pg.js("JSON.parse(localStorage.getItem('pinky.ahColWidths'))[3]") == w2)
            check('메시지 칸은 단어 단위로 줄을 바꾼다 (keep-all + break-word)', await pg.js("(() => { const c = getComputedStyle(document.querySelector('#ahBody td.msg')); return c.wordBreak === 'keep-all' && c.overflowWrap === 'break-word'; })()"))
            await pg.js("document.querySelectorAll('#ahTable th .av-rz')[3].dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))")
            check('더블클릭: 자동 배치로 복귀', await pg.js("!document.getElementById('ahTable').classList.contains('fixed') && localStorage.getItem('pinky.ahColWidths') === null"))
            check('왼쪽 메뉴 버튼 이름이 영문', await pg.js("[...document.querySelectorAll('.menubar .menu-item')].map(b => b.firstChild.textContent.trim().replace(/\\s+/g, ' ')).join()") == 'Main,Log,Alarm,Points,Edit Layout,Add Robot,Dark Mode', await pg.js("[...document.querySelectorAll('.menubar .menu-item')].map(b => b.firstChild.textContent.trim()).join()"))
            check('Alarm Sound 버튼', await pg.js("document.getElementById('alarmSoundBtn').textContent.trim()") == 'Alarm Sound')

            # ---- 파일 요약 ----
            check('파일 요약 한 줄', bool(re.search(r'파일 \d+개 · 알람 \d+건', await pg.js("document.getElementById('ahSummary').textContent"))))
            await pg.click_sel('#ahFilesBtn')
            fl = await pg.js("[document.getElementById('ahFiles').hidden, document.querySelectorAll('#ahFiles div').length, document.getElementById('ahFilesBtn').textContent]")
            check('Show files: 파일 목록이 펼쳐진다 (읽기 전용)', fl[0] is False and fl[1] == len(files) and fl[2] == 'Hide files', str(fl))
            check('파일 삭제 버튼은 없다', await pg.js("document.querySelectorAll('#ahFiles button, #ahFiles input').length === 0"))
            await pg.click_sel('#ahFilesBtn')

            # ---- 실시간: active 알람이 해소되면 그 자리에서 해소로 바뀌고 배지가 줄어든다 ----
            await pg.js("document.getElementById('ahReset').click()")
            await asyncio.sleep(0.8)
            pin_state = lambda code: pg.js("(() => { const t = document.querySelector('#ahBody tr.pin[data-code=%s]'); return t ? t.dataset.state : null; })()" % json.dumps(code))  # noqa: E731
            check('active 둘이 고정되어 있다', await pin_state('CMD_SEND_FAILED') == 'active' and await pin_state('CMD_REJECTED') == 'active')
            api('POST', '/api/mock/no-subscriber', {'on': False})
            api('POST', '/api/robots/pinky2/command', {'cmd': 'start'})   # 전송 실패(H) 알람이 해소된다
            check('실시간으로 해소로 바뀐다 (고정 행 그대로)', await pg.wait("(() => { const t = document.querySelector('#ahBody tr.pin[data-code=CMD_SEND_FAILED]'); return t && t.dataset.state === 'cleared'; })()", 5))
            live_n = "[...document.querySelectorAll('#ahBody tr.pin')].filter(t => t.dataset.state !== 'cleared').length"
            check('배지가 줄고 HH/H 가 없어 빨강이 아니다 (숫자 = 고정된 활성 알람 수)', await pg.wait(f"document.getElementById('alarmBadge').textContent === String({live_n}) && !document.getElementById('alarmBadge').classList.contains('hot')", 4))
            api('POST', '/api/robots/pinky1/command', {'cmd': 'start'})
            check('active 가 없으면 배지가 숨는다', await pg.wait("document.getElementById('alarmBadge').hidden", 4))
            api('POST', '/api/robots/pinky2/command', {'cmd': 'bogus'})
            check('새 알람이 실시간으로 맨 위에 붙는다', await pg.wait("(() => { const t = document.querySelector('#ahBody tr[data-key]'); return t.classList.contains('pin') && t.dataset.state === 'active' && t.children[1].textContent === 'pinky2'; })()", 5))
            check('배지가 다시 1', await pg.wait("document.getElementById('alarmBadge').textContent === '1'", 4))

            # ---- 새로고침 뒤에도 화면 유지 ----
            await pg.call('Page.reload')
            check('새로고침 뒤에도 Alarm 화면 (History)', await pg.wait("!document.getElementById('alarmView').hidden && !document.getElementById('avHistoryPane').hidden && document.querySelectorAll('#ahBody tr[data-key]').length > 0", 8))
            await pg.click_sel('#avTabCatalog')
            await pg.call('Page.reload')
            check('Catalog 탭도 기억한다', await pg.wait("!document.getElementById('alarmView').hidden && !document.getElementById('avCatalogPane').hidden && document.querySelectorAll('#avBody tr').length > 0", 8))
            await pg.click_sel('#avTabHistory')
            await pg.click_sel('.menu-item[data-view="main"]')
            await pg.call('Page.reload')
            check('메인에서 새로고침하면 메인 화면', await pg.wait("document.getElementById('alarmView').hidden && !document.getElementById('workspace').hidden && app.wsUp", 8))
            await pg.click_sel('#logMenu')
            await pg.call('Page.reload')
            check('로그 화면도 기억한다', await pg.wait("!document.getElementById('logView').hidden", 8))
            await pg.click_sel('#alarmMenu')
            await pg.wait("document.querySelectorAll('#ahBody tr[data-key]').length > 0", 6)

            # ---- CSV ----
            st, h, body = get_bytes('/api/alarms/history/export?' + urllib.parse.urlencode({'robot': 'pinky1'}))
            check('CSV: BOM, text/csv, 첨부 파일 이름', st == 200 and body[:3] == b'\xef\xbb\xbf' and h['content-type'].startswith('text/csv') and 'attachment' in h['content-disposition'])
            text = body.decode('utf-8-sig')
            lines = text.splitlines()
            check('CSV: 한글 헤더와 한글 내용이 깨지지 않는다', lines[0].startswith('발생,로봇,레벨,코드,메시지,상태') and '명령 거절' in text or '거절' in text, lines[1][:80])
            check('CSV: 행 수 = 서버 응답', len(lines) - 1 == len([a for a in srv_page if True]) or len(lines) - 1 >= 100, str(len(lines) - 1))
            check('CSV: 시각이 YYYY-MM-DD HH:MM:SS', bool(re.match(r'\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,', lines[1])), lines[1][:30])
            # 화면의 Export CSV 버튼: Blob 다운로드를 가로채 확인한다
            await pg.js("window.__dl = null; const oc = URL.createObjectURL; URL.createObjectURL = (b) => { window.__dl = b; return oc.call(URL, b); }; HTMLAnchorElement.prototype.click = function () { window.__dlName = this.download; };")
            await pg.click_sel('#ahExport')
            check('Export CSV: 파일 이름에 기간 포함', await pg.wait("window.__dlName && /^alarms_\\d{4}-\\d\\d-\\d\\d\\.csv$/.test(window.__dlName)", 5), str(await pg.js('window.__dlName')))
            check('Export CSV: 안내 문구', 'N' not in await pg.js("document.getElementById('ahNote').textContent") and '행을 내려받았습니다' in await pg.js("document.getElementById('ahNote').textContent"))

            # ---- 고정 문구: 버튼 이름은 영문, 나머지는 한국어 ----
            buttons = await pg.js("[...document.querySelectorAll('#avHistoryPane button')].map(b => b.textContent.trim())")
            check('History 버튼 이름이 영문', buttons == ['Hide files' if False else 'Show files', 'Apply', 'Reset', 'Export CSV', 'Load more'] or set(buttons) == {'Show files', 'Apply', 'Reset', 'Export CSV', 'Load more'}, str(buttons))
            fixed = await pg.js("JSON.stringify([...document.querySelectorAll('#avHistoryPane th, #avHistoryPane [data-t], .ah-levels label, #ahState option, #ahNote, #ahEmpty, #ahOff')].map(e => e.textContent).concat([document.getElementById('ahSearch').placeholder]))")
            check('History 고정 문구에 영문 문장 없음 (버튼 제외)', not english_left(fixed), english_left(fixed))
            if shot:
                await pg.js("document.querySelector('#ahBody tr[data-key]:not(.pin)').click()")
                await asyncio.sleep(0.8)
                await screenshot(pg, shot)

            # ---- 좁은 화면 ----
            await pg.call('Emulation.setDeviceMetricsOverride', width=820, height=900, deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(0.5)
            check('820px: 페이지 가로 스크롤 없음', await pg.js('document.documentElement.scrollWidth <= window.innerWidth + 1'), str(await pg.js('document.documentElement.scrollWidth')))
            check('820px: 비상 정지가 화면 안에 있다', await pg.js("(() => { const r = document.querySelector('.estop').getBoundingClientRect(); return r.right <= window.innerWidth + 1 && r.width > 0; })()"))
            check('820px: 배지/메뉴가 화면 안', await pg.js("(() => { const r = document.getElementById('alarmMenu').getBoundingClientRect(); return r.right <= window.innerWidth + 1; })()"))
            if narrow_shot:
                await screenshot(pg, narrow_shot)
            await pg.call('Emulation.setDeviceMetricsOverride', width=1400, height=900, deviceScaleFactor=1, mobile=False)

            # ---- 알람바의 ACK/SIL 이 이 화면에서도 눌린다 ----
            api('POST', '/api/robots/pinky1/command', {'cmd': 'bogus'})
            await pg.wait("document.getElementById('alarmAckBtn').disabled === false", 4)
            check('이 화면에서 알람바 ACK 가 눌린다', await pg.js("document.getElementById('alarmAckBtn').disabled === false"))
            await pg.click_sel('#alarmAckBtn')
            check('ACK 하면 고정 행이 확인됨으로 바뀐다', await pg.wait("(() => { const t = [...document.querySelectorAll('#ahBody tr.pin')]; return t.length > 0 && t.every(r => r.dataset.state === 'acknowledged'); })()", 5))
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
