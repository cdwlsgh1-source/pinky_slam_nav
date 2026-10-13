"""알람 카탈로그 편집(Step A5)을 headless Chrome(CDP)으로 확인한다. 인증을 켠 mock 서버, 임시 사운드·이력·변경 파일 폴더를 직접 만들고 정리한다.

실행: .venv/bin/python tests/verify_alarm_edit_ui.py [--shot docs/alarm_a5.png] [--narrow-shot docs/alarm_a5_narrow.png]   (google-chrome 필요, 없으면 SKIP)
확인: operator 에게는 편집 UI 가 없고 서버가 403(영문), manager 는 편집 대화상자(보호 알람은 잠금), 검증 오류 영문 표시(화면·서버 양쪽),
      저장 -> 표 반영·다른 브라우저(탭) 반영·알람바 반영, 알람별 사운드 파일과 반복 주기로 재생, 끈 알람(서버·브라우저 모두)이 표시·재생되지 않음,
      알람별 소리 끄기, 기본값으로 되돌리기, 로그 화면 감사 기록, 고정 문구가 한국어, 860px 이하에서 가로 스크롤 없음.
소리는 들을 수 없으므로 createOscillator 와 HTMLMediaElement.play 호출을 가로채 센다 (HH 합성음 한 번 = 발진기 4개).
"""
import argparse
import asyncio
import base64
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_step3_ui as v3  # noqa: E402  (Page 헬퍼 재사용)
from uitext import english_left  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORT, CDP = 8021, 9239
BASE = f'http://127.0.0.1:{PORT}'
FAILS = []
HANGUL = re.compile('[가-힣]')

SPY = """
window.__snd = {osc: 0, files: []};
(() => {
  const AC = window.AudioContext;
  const co = AC.prototype.createOscillator;
  AC.prototype.createOscillator = function () { window.__snd.osc++; return co.apply(this, arguments); };
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () { window.__snd.files.push(new URL(this.src).pathname); return play.apply(this, arguments); };
})();
"""


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {extra}' if extra and not cond else ''))
    if not cond:
        FAILS.append(name)


def make_wav(path, hz=660, sec=0.3):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * hz * i / 8000))) for i in range(int(8000 * sec))))


ROWS = "[...document.querySelectorAll('#alarmBody tr')]"
BAR = lambda code, state='active': f"{ROWS}.filter(tr => tr.dataset.code === '{code}' && tr.dataset.state === '{state}')"  # noqa: E731


async def run(shot, narrow_shot):
    chrome = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not chrome:
        print('SKIP: Chrome 이 없다')
        return
    work = Path(tempfile.mkdtemp())
    (work / 'sounds').mkdir()
    make_wav(work / 'sounds' / 'a.wav', 880)
    make_wav(work / 'sounds' / 'b.wav', 440)
    local = work / 'alarms.local.yaml'
    prof = tempfile.mkdtemp()
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--mock', '--mock-wait-scale', '0.2', '--port', str(PORT),
                            '--alarm-history-dir', str(work / 'hist'), '--alarm-sound-dir', str(work / 'sounds'), '--alarms-local', str(local)],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    br = subprocess.Popen([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', f'--remote-debugging-port={CDP}', '--autoplay-policy=no-user-gesture-required',
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
            await pg.call('Page.addScriptToEvaluateOnNewDocument', source=SPY)

            async def load():
                await pg.call('Page.navigate', url=BASE + '/')
                return await pg.wait("app.cfg && app.order.length === 2 && app.wsUp", 15)

            async def login(user):
                return await pg.js("fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: %s, password: %s})}).then(r => r.status)" % (json.dumps(user), json.dumps(user)))

            async def open_alarm_screen():
                await pg.click_sel('#alarmMenu')
                await pg.click_sel('#avTabCatalog')   # 기본 탭은 History (Step A7)
                return await pg.wait("!document.getElementById('alarmView').hidden && document.querySelectorAll('#avBody tr').length > 0", 5)

            async def edit(code):
                await pg.click_sel(f'#avBody button[data-code="{code}"]')
                return await pg.wait("document.getElementById('avEditDlg').open && document.getElementById('avEdTitle').textContent.length > 0", 5)

            async def put_fields(**f):
                parts = []
                for sel, key in (('avEdMsg', 'msg'), ('avEdLevel', 'level'), ('avEdFile', 'file'), ('avEdRepeat', 'repeat')):
                    if key in f:
                        parts.append(f"(() => {{ const e = document.getElementById('{sel}'); e.value = {json.dumps(str(f[key]))}; e.dispatchEvent(new Event('change')); e.dispatchEvent(new Event('input')); }})()")
                for sel, key in (('avEdEnabled', 'enabled'), ('avEdSound', 'sound')):
                    if key in f:
                        parts.append(f"(() => {{ const e = document.getElementById('{sel}'); e.checked = {str(bool(f[key])).lower()}; e.dispatchEvent(new Event('change')); }})()")
                for p in parts:
                    await pg.js(p)

            async def save_and_wait_close():
                await pg.click_sel('#avEdSave')
                return await pg.wait("!document.getElementById('avEditDlg').open", 5)

            await pg.call('Page.navigate', url=BASE + '/')   # 로그인 전의 페이지에서 시작한다 (쿠키를 받으려면 같은 출처여야 한다)
            await asyncio.sleep(0.8)

            # ---- operator: 편집 UI 없음, 서버 403 ----
            check('oper 로그인', await login('oper') == 200)
            check('페이지 다시 로드', await load())
            check('Alarm 화면 열림', await open_alarm_screen())
            check('operator: Edit 열과 버튼이 없다', await pg.js("document.getElementById('avEditTh').hidden === true && document.querySelectorAll('#avBody button').length === 0"))
            r = await pg.js("fetch('/api/alarms/catalog/CMD_REJECTED', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({level: 'H'})}).then(async r => [r.status, (await r.json()).error])")
            check('operator: 서버가 403 과 한국어 오류', r == [403, '알람 카탈로그는 manager 만 바꿀 수 있습니다'], str(r))
            check('operator: 카탈로그는 읽을 수 있다', await pg.js("document.querySelectorAll('#avBody tr').length") == 24 or await pg.js("document.querySelectorAll('#avBody tr').length") > 20)

            # ---- manager ----
            await pg.js("fetch('/api/logout', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'}).then(r => r.status)")
            check('mngr 로그인', await login('mngr') == 200)
            check('페이지 다시 로드(manager)', await load())
            check('Alarm 화면 열림(manager)', await open_alarm_screen())
            n_rows = await pg.js("document.querySelectorAll('#avBody tr').length")
            check('manager: Edit 열과 행마다 Edit 버튼', await pg.js("document.getElementById('avEditTh').hidden === false") and await pg.js("document.querySelectorAll('#avBody button[data-code]').length") == n_rows)

            # 보호 알람은 잠긴다
            check('보호 알람(ESTOP_FAILED) 편집창 열림', await edit('ESTOP_FAILED'))
            check('보호 알람: Enabled·Level·Sound 잠김, 안내 문구', await pg.js("['avEdEnabled', 'avEdLevel', 'avEdSound'].every(i => document.getElementById(i).disabled)")
                  and '끌 수 없습니다' in await pg.js("document.getElementById('avEdNote').textContent"))
            check('보호 알람: 서버도 거부 (화면을 우회해도)', await pg.js("fetch('/api/alarms/catalog/ESTOP_FAILED', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({enabled: false})}).then(async r => [r.status, (await r.json()).error])") == [400, '이 알람은 끌 수 없습니다'])
            await pg.click_sel('#avEdCancel')
            check('Cancel 로 닫힘', await pg.wait("!document.getElementById('avEditDlg').open", 3))

            # 브라우저 알람은 메시지가 잠긴다
            check('브라우저 알람(SERVER_DISCONNECTED) 편집창 열림', await edit('SERVER_DISCONNECTED'))
            check('브라우저 알람: 메시지 잠김, Level/Enabled 가능', await pg.js("document.getElementById('avEdMsg').disabled && !document.getElementById('avEdLevel').disabled && !document.getElementById('avEdEnabled').disabled"))
            await pg.click_sel('#avEdCancel')
            check('일회성 브라우저 알람(DRIVE_DENIED): 소리 칸이 잠기고 안내', await edit('DRIVE_DENIED') and await pg.js("['avEdSound', 'avEdFile', 'avEdRepeat'].every(i => document.getElementById(i).disabled) && document.getElementById('avEdNote').textContent.includes('소리가 없습니다')"))
            await pg.click_sel('#avEdCancel')

            # 검증 오류 (영문): 화면 검사와 서버 검사
            check('CMD_REJECTED 편집창', await edit('CMD_REJECTED'))
            check('폼이 현재 값으로 채워짐', await pg.js("(() => { const g = (i) => document.getElementById(i); return g('avEdEnabled').checked && g('avEdLevel').value === 'L' && g('avEdMsg').value.includes('{cmd}') && g('avEdSound').checked && g('avEdRepeat').value === ''; })()"))
            check('Reset to default 는 변경 전에는 비활성', await pg.js("document.getElementById('avEdReset').disabled"))
            orig_msg = await pg.js("document.getElementById('avEdMsg').value")
            await put_fields(msg='bad {nope}')
            await pg.click_sel('#avEdSave')
            check('화면 검증: 모르는 자리표시자', '알 수 없는 자리표시자 {nope}' in await pg.js("document.getElementById('avEdErr').textContent"))
            await put_fields(msg='')
            await pg.click_sel('#avEdSave')
            check('화면 검증: 빈 메시지', await pg.js("document.getElementById('avEdErr').textContent") == '메시지는 줄바꿈 없이 1~120글자여야 합니다')
            await put_fields(msg='ok {cmd}', repeat='99')
            await pg.click_sel('#avEdSave')
            check('화면 검증: 반복 주기 범위', await pg.js("document.getElementById('avEdErr').textContent") == '반복 주기는 1~60초여야 합니다')
            await put_fields(repeat='')
            await pg.js("document.getElementById('avEdFile').add(new Option('ghost.wav', 'ghost.wav')); document.getElementById('avEdFile').value = 'ghost.wav';")
            await pg.click_sel('#avEdSave')
            check('서버 검증 오류가 대화상자에 한국어로 보인다 (모르는 사운드 파일)', await pg.wait("document.getElementById('avEdErr').textContent === '알 수 없는 사운드 파일입니다'", 4) and await pg.js("document.getElementById('avEditDlg').open"))
            check('오류 문구가 한국어', bool(HANGUL.search(await pg.js("document.getElementById('avEdErr').textContent"))))
            await put_fields(file='', msg=orig_msg)
            await pg.click_sel('#avEdSave')
            check('바뀐 것이 없으면 "바뀐 것이 없습니다" (서버를 부르지 않는다)', await pg.js("document.getElementById('avEdErr').textContent") == '바뀐 것이 없습니다' and await pg.js("document.getElementById('avEditDlg').open"))

            # 두 번째 브라우저 탭 (같은 manager 세션): 정책 방송을 받는다
            new = json.load(urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{CDP}/json/new?{BASE}/', method='PUT')))
            async with websockets.connect(new['webSocketDebuggerUrl'], max_size=None) as ws2:
                pg2 = v3.Page(ws2)
                await pg2.call('Page.enable')
                check('두 번째 탭 로드', await pg2.wait("app.cfg && app.order.length === 2 && app.wsUp", 15))

                # 정상 저장: 레벨 H, 문구, 사운드 파일 a.wav, 반복 2초
                await put_fields(msg='Rejected {cmd} ({robot})', level='H', file='a.wav', repeat='2')
                check('저장하면 대화상자가 닫힌다', await save_and_wait_close())
                check('표에 반영: 레벨 H, 수정 표시, 소리 칸', await pg.wait("(() => { const tr = document.querySelector('#avBody tr[data-code=CMD_REJECTED]'); return tr && tr.querySelector('td.lvl').textContent === 'H' && tr.classList.contains('modified') && tr.querySelector('td.snd').textContent === '예 · a.wav · 2초마다' && tr.querySelector('td.msg').textContent === 'Rejected {cmd} ({robot})'; })()", 5))
                check('다른 탭(다른 화면)에도 정책이 방송됨', await pg2.wait("app.alarmPolicy.CMD_REJECTED && app.alarmPolicy.CMD_REJECTED.level === 'H' && app.alarmPolicy.CMD_REJECTED.sound_file === 'a.wav' && app.alarmPolicy.CMD_REJECTED.repeat === 2", 5))

                # 실제 발생: 새 문구·레벨, 알람별 파일과 주기로 재생 (합성음 아님)
                await pg.js("(window.__snd.osc = 0, window.__snd.files.length = 0)")
                await pg.js("fetch('/api/robots/pinky1/command', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'bogus'})}).then(r => r.status)")
                check('알람바: 바뀐 문구와 레벨', await pg.wait(f"{BAR('CMD_REJECTED')}.length === 1 && {BAR('CMD_REJECTED')}[0].classList.contains('lvl-H') && {BAR('CMD_REJECTED')}[0].children[2].textContent.startsWith('Rejected bogus (pinky1)')", 5))
                check('알람별 사운드 파일 a.wav 재생 (합성음 아님)', await pg.wait("window.__snd.files.includes('/sounds/a.wav') && window.__snd.osc === 0", 5), str(await pg.js("JSON.stringify(window.__snd)")))
                check('알람별 반복 주기 2초 (A3 설정 5초보다 짧게)', await pg.wait("window.__snd.files.filter(f => f === '/sounds/a.wav').length >= 3", 8), str(await pg.js("JSON.stringify(window.__snd)")))
                await pg.click_sel('#alarmAckBtn')
                check('ACK 하면 소리가 멈춘다', await pg.wait("silTargets().length === 0", 4))

                # 알람별 소리 끄기 (브라우저 알람 SERVER_DISCONNECTED, 레벨 HH 로 올려 합성음이 들리게 한 뒤 소리를 끈다)
                await pg.click_sel('#avBody button[data-code="SERVER_DISCONNECTED"]')
                await pg.wait("document.getElementById('avEditDlg').open", 3)
                await put_fields(level='HH')
                check('브라우저 알람 레벨 변경 저장', await save_and_wait_close())
                await pg.js("(window.__snd.osc = 0, window.__snd.files.length = 0)")
                await pg.js("addAlarm('bad', 'link lost test', { code: 'SERVER_DISCONNECTED', state: 'active' })")
                check('브라우저 알람이 정책 레벨(HH)로 생긴다', await pg.wait(f"{BAR('SERVER_DISCONNECTED')}.length === 1 && {BAR('SERVER_DISCONNECTED')}[0].classList.contains('lvl-HH')", 4))
                check('HH 합성음이 난다 (발진기 4개)', await pg.wait("window.__snd.osc >= 4", 4), str(await pg.js("window.__snd.osc")))
                await pg.click_sel('#alarmAckBtn')
                await pg.wait("silTargets().length === 0", 4)
                await pg.click_sel('#avBody button[data-code="SERVER_DISCONNECTED"]')
                await pg.wait("document.getElementById('avEditDlg').open", 3)
                await put_fields(sound=False)
                check('소리 끄기 저장 (소리 아니오)', await save_and_wait_close() and await pg.wait("document.querySelector('#avBody tr[data-code=SERVER_DISCONNECTED] td.snd').textContent === '아니오'", 4))
                await pg.js("(window.__snd.osc = 0, window.__snd.files.length = 0)")
                await pg.js("addAlarm('bad', 'silent test', { code: 'SERVER_DISCONNECTED', state: 'active' })")
                await asyncio.sleep(1.5)
                check('소리가 꺼진 알람: 알람바에는 보이지만 소리와 SIL 대상이 아니다', await pg.js(f"{BAR('SERVER_DISCONNECTED')}.length >= 1") and await pg.js("window.__snd.osc === 0 && window.__snd.files.length === 0 && silTargets().length === 0 && document.getElementById('alarmSilBtn').disabled"))

                # 끈 브라우저 알람은 만들어지지 않는다
                await pg.click_sel('#avBody button[data-code="SCAN_PENDING"]')
                await pg.wait("document.getElementById('avEditDlg').open", 3)
                await put_fields(enabled=False)
                check('SCAN_PENDING 끄기 저장', await save_and_wait_close())
                before = await pg.js(f"{ROWS}.filter(tr => tr.dataset.code === 'LOCAL' || true).length")
                await pg.js("addAlarm('warn', 'lidar waits', { code: 'SCAN_PENDING' }); addAlarm('warn', 'control', { code: 'ROUTE_INVALID' })")
                check('끈 브라우저 알람(SCAN_PENDING)은 안 생기고 켠 알람은 생긴다', await pg.wait("document.getElementById('alarmBody').textContent.includes('control')", 3) and not await pg.js("document.getElementById('alarmBody').textContent.includes('lidar waits')"))
                check('다른 탭에서도 꺼진 브라우저 알람은 안 생긴다', await pg2.js("addAlarm('warn', 'lidar waits 2', { code: 'SCAN_PENDING' }); document.getElementById('alarmBody').textContent.includes('lidar waits 2')") is False)

                # 끈 서버 알람: 표시·재생 없음 + 이미 떠 있던 알람은 해소되어 소리가 멈춘다
                await pg.js("(window.__snd.osc = 0, window.__snd.files.length = 0)")
                await pg.js("fetch('/api/robots/pinky1/command', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'bogus'})}).then(r => r.status)")
                await pg.wait("silTargets().some(a => a.code === 'CMD_REJECTED')", 4)
                await pg.click_sel('#avBody button[data-code="CMD_REJECTED"]')
                await pg.wait("document.getElementById('avEditDlg').open", 3)
                await put_fields(enabled=False)
                check('CMD_REJECTED 끄기 저장', await save_and_wait_close() and await pg.wait("document.querySelector('#avBody tr[data-code=CMD_REJECTED]').classList.contains('off')", 4))
                check('끄는 순간 떠 있던 알람이 해소되어 소리 대상에서 빠진다', await pg.wait(f"{BAR('CMD_REJECTED')}.length === 0 && !silTargets().some(a => a.code === 'CMD_REJECTED')", 4))
                await pg.js("(window.__snd.osc = 0, window.__snd.files.length = 0)")
                rows_before = await pg.js(f"{ROWS}.filter(tr => tr.dataset.code === 'CMD_REJECTED').length")
                for _ in range(3):
                    await pg.js("fetch('/api/robots/pinky1/command', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'bogus'})}).then(r => r.status)")
                await asyncio.sleep(2.5)
                check('끈 서버 알람이 발생해도 알람바에 새 행이 없다', await pg.js(f"{ROWS}.filter(tr => tr.dataset.code === 'CMD_REJECTED').length") == rows_before)
                check('끈 서버 알람이 발생해도 소리가 나지 않는다', await pg.js("window.__snd.osc === 0 && window.__snd.files.length === 0"))
                check('두 번째 탭에도 끈 알람 행이 새로 생기지 않는다', await pg2.js(f"{ROWS}.filter(tr => tr.dataset.code === 'CMD_REJECTED' && tr.dataset.state === 'active').length") == 0)

                # 되돌리기
                await pg.click_sel('#avBody button[data-code="CMD_REJECTED"]')
                await pg.wait("document.getElementById('avEditDlg').open", 3)
                check('변경이 있으면 기본값으로 되돌리기가 켜진다', not await pg.js("document.getElementById('avEdReset').disabled"))
                await pg.click_sel('#avEdReset')
                check('기본값으로 되돌리기: 기본값(L, 사용, 수정 표시 없음)', await pg.wait("(() => { const tr = document.querySelector('#avBody tr[data-code=CMD_REJECTED]'); return !document.getElementById('avEditDlg').open && tr.querySelector('td.lvl').textContent === 'L' && !tr.classList.contains('off') && !tr.classList.contains('modified') && tr.querySelector('td.snd').textContent === '예'; })()", 5))
                check('되돌린 알람은 다시 발생한다', await pg.js("fetch('/api/robots/pinky1/command', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'bogus'})}).then(r => r.status)") == 400 and await pg.wait(f"{BAR('CMD_REJECTED')}.length === 1", 4))
                saved = await pg.js("fetch('/api/logs').then(r => r.json()).then(j => j.entries.filter(e => e.text.includes('알람 카탈로그')).map(e => e.lvl + ' ' + e.text))")
                check('로그 화면에 누가 무엇을 바꿨는지 남는다 (끄기는 WARN)', any('변경 (mngr): CMD_REJECTED' in t for t in saved) and any(t.startswith('WARN') and 'enabled True→False' in t for t in saved) and any('되돌리기 (mngr): CMD_REJECTED' in t for t in saved) and any('변경 거절 (mngr, CMD_REJECTED)' in t for t in saved), str(saved[-4:]))

            check('저장 파일에는 바꾼 항목만 있다 (alarms.yaml 은 그대로)', local.exists() and 'CMD_REJECTED' not in local.read_text(encoding='utf-8') and 'SERVER_DISCONNECTED' in local.read_text(encoding='utf-8') and 'SCAN_PENDING' in local.read_text(encoding='utf-8'))

            # 고정 문구에 한글 없음 (알람 이름·조건·문구와 입력값 제외)
            await edit('ROBOT_OFFLINE')
            fixed = await pg.js("JSON.stringify([...document.querySelectorAll('#avEditDlg label, #avEditDlg button, #avEditDlg option, #avEdNote, #avEdPh, #avEditDlg th, #avTable th')].map(e => e.textContent))")
            check('편집 대화상자와 표 제목의 고정 문구가 한국어 (영문 문장 없음)', not english_left(fixed), english_left(fixed))
            if shot:   # 스크린샷은 모든 조작이 끝난 뒤에 찍는다 (캡처가 이어지는 마우스 좌표를 흐트러뜨린다)
                await pg.click_sel('#avEdCancel')
                await edit('SERVER_DISCONNECTED')
                await screenshot(pg, shot)
            if narrow_shot:
                await pg.call('Emulation.setDeviceMetricsOverride', width=820, height=900, deviceScaleFactor=1, mobile=False)
                await asyncio.sleep(0.4)
                check('820px: 대화상자가 화면 안에 있고 가로 스크롤 없음', await pg.js("(() => { const r = document.getElementById('avEditDlg').getBoundingClientRect(); return r.left >= 0 && r.right <= window.innerWidth + 1 && document.documentElement.scrollWidth <= window.innerWidth + 1; })()"))
                await screenshot(pg, narrow_shot)
    finally:
        for p in (br, srv):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        shutil.rmtree(work, ignore_errors=True)
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
