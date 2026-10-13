"""알람 소리(Step A3)를 headless Chrome(CDP)으로 확인한다. mock 서버와 임시 사운드 폴더를 직접 만들고 정리한다.

실행: .venv/bin/python tests/verify_alarm_sound_ui.py [--shot docs/alarm_a3.png] [--hint-shot docs/alarm_a3_hint.png]   (google-chrome 필요, 없으면 SKIP)
확인: 자동재생 차단 안내와 해제, 알람이 오면 울림과 반복 주기, SIL/ACK 로 멈춤, 새 알람이 오면 SIL 해제, 전체 끔에서도 HH 시각 경고 유지,
      파일 재생, 파일이 지워지면/재생에 실패하면 합성음 대체, 레벨별 Test/Stop (알람을 만들지 않음), localStorage 저장과 저장소를 못 쓸 때의 동작, 고정 문구에 한글 없음.
소리는 들을 수 없으므로 AudioContext.createOscillator 와 HTMLMediaElement.play 호출을 가로채 센다 (합성음 비프 1개 = 발진기 1개, HH 한 번 = 4개).
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
import urllib.error
import urllib.request
import wave
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_step3_ui as v3  # noqa: E402  (Page 헬퍼 재사용)
from uitext import english_left  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PORT, CDP = 8019, 9237
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
  HTMLMediaElement.prototype.play = function () { window.__snd.files.push(new URL(this.src).pathname + '@' + this.volume.toFixed(2)); return play.apply(this, arguments); };
})();
"""
BLOCK_STORAGE = "Object.defineProperty(window, 'localStorage', { get() { throw new Error('storage blocked'); } });"


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


def make_wav(path, hz=660, sec=0.3):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(b''.join(struct.pack('<h', int(8000 * math.sin(2 * math.pi * hz * i / 8000))) for i in range(int(8000 * sec))))


OSC = 'window.__snd.osc'
FILES = 'JSON.stringify(window.__snd.files)'
ROW_COUNT = lambda code, robot, state: (  # noqa: E731
    "[...document.querySelectorAll('#alarmBody tr')].filter(tr => tr.dataset.code === '%s' && tr.children[1].textContent === '%s' && tr.dataset.state === '%s').length" % (code, robot, state))


async def run(shot, hint_shot):
    chrome = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not chrome:
        print('SKIP: Chrome 이 없다')
        return
    hist_dir, sound_dir = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
    make_wav(sound_dir / 'siren.wav', 880)
    make_wav(sound_dir / 'ding.wav', 440)
    (sound_dir / 'bad.wav').write_bytes(b'this is not audio')      # 재생에 실패하는 파일
    (sound_dir / 'notes.txt').write_text('not a sound')             # 목록에 나오면 안 된다
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--mock', '--mock-wait-scale', '0.2', '--port', str(PORT), '--no-auth',
                            '--alarm-history-dir', str(hist_dir), '--alarm-sound-dir', str(sound_dir)],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    prof = tempfile.mkdtemp()
    br = subprocess.Popen([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', f'--remote-debugging-port={CDP}',
                           '--autoplay-policy=document-user-activation-required', f'--user-data-dir={prof}', '--window-size=1400,900', 'about:blank'],
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
            await pg.call('Page.addScriptToEvaluateOnNewDocument', source=SPY)
            await pg.call('Page.navigate', url=BASE + '/')
            check('페이지 로드와 스냅샷', await pg.wait("app.cfg && app.order.length === 2 && app.wsUp"))

            async def open_dialog():
                await pg.click_sel('#alarmMenu')   # 알람소리 버튼은 알람 화면의 탭 줄에 있다
                await pg.click_sel('#alarmSoundBtn')
                return await pg.wait("document.getElementById('soundDlg').open", 3)

            async def close_dialog():
                await pg.click_sel('#sndClose')
                return await pg.wait("!document.getElementById('soundDlg').open", 3)

            async def set_value(sel, value):
                await pg.js(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); e.value = {json.dumps(value)}; e.dispatchEvent(new Event('change')); }})()")

            # ---- 자동재생 차단 안내 (FRA3-8) ----
            check('첫 클릭 전에는 소리가 막혀 "Click to enable alarm sound" 가 보임',
                  await pg.wait("!document.getElementById('soundHint').hidden && document.getElementById('soundHint').textContent === 'Click to enable alarm sound'", 4))
            if hint_shot:
                await screenshot(pg, hint_shot)
            await pg.mouse(40, 30)
            check('화면을 한 번 누르면 안내가 사라짐', await pg.wait("document.getElementById('soundHint').hidden && sound.info().blocked === false", 4))

            # ---- 소리 설정 화면의 고정 문구 (FRA3-10) ----
            check('소리 설정 열기', await open_dialog())
            txt = await pg.js("(() => { const d = document.getElementById('soundDlg'); return d.textContent.replace(/(\S)(?=[A-Z])/g, '$1 ') + ' ' + [...d.querySelectorAll('[aria-label],[title]')].map(e => (e.getAttribute('aria-label') || '') + ' ' + (e.title || '')).join(' ') + ' ' + [...d.querySelectorAll('option')].map(o => o.textContent).join(' '); })()")
            check('소리 설정 화면에 영문 문장이 없음 (한국어)', not english_left(txt), english_left(txt))
            for word in ('알람 소리', '음량', '반복 주기(초)', '사운드 파일', '합성 비프음', 'Test', 'Stop'):
                check(f'소리 설정 문구: {word}', word in txt)
            check('레벨 4개 행 (info 없음)', await pg.js("[...document.querySelectorAll('#sndRows tr td:first-child')].map(e => e.textContent).join()") == 'HH,H,L,LL')
            check('사운드 폴더의 허용 파일만 목록에 나옴 (txt 제외)',
                  await pg.js("[...document.querySelectorAll('#sndRows select')][0].options.length") == 4
                  and await pg.js("[...document.querySelectorAll('#sndRows select')][0].textContent") == '합성 비프음bad.wavding.wavsiren.wav')
            check('파일 안내 문구', '찾았습니다' in await pg.js("document.getElementById('sndFilesNote').textContent") and 'config/sounds/' in await pg.js("document.getElementById('sndFilesNote').textContent"))
            if shot:
                await screenshot(pg, shot)
            await set_value('#sndInterval', '1')
            check('반복 주기 1초로 설정', await pg.js("soundCfg.s.interval") == 1)
            await set_value('#sndInterval', '999')
            check('범위를 넘는 반복 주기는 60초로 맞춤', await pg.js("soundCfg.s.interval") == 60 and await pg.js("document.getElementById('sndInterval').value") == '60')
            await set_value('#sndInterval', '1')
            check('설정 화면 닫기', await close_dialog())

            # ---- 알람이 오면 울리고 반복 ----
            base = await pg.js(OSC)
            st, _ = api('POST', '/api/robots/pinky1/command', {'cmd': 'bogus'})
            check('거절 알람(L) 발생', st == 400 and await pg.wait(ROW_COUNT('CMD_REJECTED', 'pinky1', 'active') + ' === 1', 4))
            check('알람이 오면 L 합성음이 바로 남', await pg.wait(f"{OSC} >= {base + 1} && sound.info().playing === 'L'", 3))
            await asyncio.sleep(2.6)
            n = await pg.js(OSC)
            check('반복 주기(1초)마다 다시 울림', n - base >= 3, (base, n))

            # ---- SIL / 새 알람 / ACK ----
            await pg.click_sel('#alarmSilBtn')
            await asyncio.sleep(0.7)
            quiet = await pg.js(OSC)
            await asyncio.sleep(2.5)
            check('SIL 을 누르면 소리가 멈춤', await pg.js(OSC) == quiet and await pg.js("sound.info().playing") is None)
            api('POST', '/api/robots/pinky2/command', {'cmd': 'bogus'})
            check('새 알람이 오면 SIL 이 풀리고 다시 울림', await pg.wait(f"{OSC} > {quiet} && app.silenced === false", 3))
            await pg.click_sel('#alarmAckBtn')
            await asyncio.sleep(0.7)
            quiet = await pg.js(OSC)
            await asyncio.sleep(2.5)
            check('ACK 하면 소리가 멈춤 (활성 알람이 남아도 확인한 알람은 울리지 않음)', await pg.js(OSC) == quiet)

            # ---- 비상정지 실패(HH): 소리 설정과 시각 경고 (FRA3-9) ----
            api('POST', '/api/mock/no-subscriber', {'on': True})
            base = await pg.js(OSC)
            api('POST', '/api/robots/pinky1/estop', {})
            check('HH 알람이 오면 HH 합성음(비프 4개씩)이 남', await pg.wait(f"{OSC} - {base} >= 4 && sound.info().playing === 'HH'", 4) and (await pg.js(OSC) - base) % 4 == 0)
            await pg.click_sel('#alarmAckBtn')
            await asyncio.sleep(0.5)
            api('POST', '/api/robots/pinky1/estop', {})                  # ACK 뒤에 또 실패: 새 행
            check('두 번째 HH 알람 (새 행)', await pg.wait(ROW_COUNT('ESTOP_FAILED', 'pinky1', 'active') + ' === 2', 4))
            await open_dialog()
            await pg.click_sel('#sndEnabled')
            check('전체 끄기', await pg.js("soundCfg.s.enabled === false"))
            await close_dialog()
            await asyncio.sleep(0.6)
            quiet = await pg.js(OSC)
            await asyncio.sleep(2.5)
            hh_visible = await pg.js("[...document.querySelectorAll('#alarmBody tr.lvl-HH.st-active')].length")
            check('전체 음소거여도 소리가 안 나고 HH 경고 행은 그대로 보임', await pg.js(OSC) == quiet and hh_visible >= 1 and await pg.js("document.getElementById('soundHint').hidden"), hh_visible)
            await open_dialog()
            await pg.click_sel('#sndEnabled')
            await close_dialog()
            check('다시 켜면 울림', await pg.wait(f"{OSC} > {quiet}", 3))
            await open_dialog()
            await pg.click_sel('#sndRows tr:nth-child(1) input[type=checkbox]')   # HH 만 끔
            await close_dialog()
            await asyncio.sleep(0.6)
            quiet = await pg.js(OSC)
            await asyncio.sleep(2.2)
            check('HH 레벨만 끄면 HH 알람은 소리가 없음', await pg.js(OSC) == quiet)
            await open_dialog()
            await pg.click_sel('#sndRows tr:nth-child(1) input[type=checkbox]')
            await close_dialog()

            # ---- 파일 재생 ----
            await open_dialog()
            await set_value('#sndRows tr:nth-child(1) select', 'siren.wav')
            check('HH 에 siren.wav 선택', await pg.js("soundCfg.s.levels.HH.file") == 'siren.wav')
            base_osc = await pg.js(OSC)
            await close_dialog()
            check('파일을 고르면 그 파일을 음량과 함께 재생 (합성음 대신)', await pg.wait(f"{FILES}.includes('/sounds/siren.wav@0.70')", 3) and await pg.js(OSC) == base_osc, await pg.js(FILES))
            check('상태줄 대신 컨트롤러 상태가 파일 재생', await pg.js("sound.info().status") == 'HH: siren.wav 재생 중')

            # ---- 파일이 지워지면 합성음 ----
            (sound_dir / 'siren.wav').unlink()
            await open_dialog()
            check('지운 파일은 목록에서 빠지고 선택에는 (파일 없음) 으로 남음',
                  await pg.wait("[...document.querySelectorAll('#sndRows select')][0].selectedOptions[0].textContent === 'siren.wav (파일 없음)'", 3))
            await close_dialog()
            files_before = await pg.js(FILES)
            osc_before = await pg.js(OSC)
            check('파일이 없으면 소리 없이 넘어가지 않고 합성음으로 대체', await pg.wait(f"{OSC} > {osc_before} && sound.info().status === 'HH: 합성 비프음 (파일 없음: siren.wav)'", 4)
                  and await pg.js(FILES) == files_before)

            # ---- 재생에 실패하는 파일 ----
            await open_dialog()
            await set_value('#sndRows tr:nth-child(1) select', 'bad.wav')
            await close_dialog()
            osc_before = await pg.js(OSC)
            check('재생에 실패한 파일도 합성음으로 대체', await pg.wait(f"{OSC} > {osc_before} && sound.info().status === 'HH: 합성 비프음 (재생 실패: bad.wav)'", 5), await pg.js("sound.info().status"))
            await pg.click_sel('#alarmAckBtn')
            api('POST', '/api/mock/no-subscriber', {'on': False})

            # ---- 시험 재생: 알람을 만들지 않음 ----
            check('시험 전 소리 낼 알람 없음 (ACK 반영 대기)', await pg.wait("silTargets().length === 0", 4), await pg.js("JSON.stringify(silTargets().map(a => a.code + ':' + a.id))"))
            await open_dialog()
            alarms_before = await pg.js("app.alarms.length")
            base = await pg.js(OSC)
            await pg.click_sel('#sndRows button[data-test="L"]')
            check('L Test: 알람 없이 L 합성음이 울림', await pg.wait(f"{OSC} > {base} && sound.info().preview === 'L'", 3))
            check('Test 중에는 그 레벨의 Stop 만 켜짐', await pg.js("[...document.querySelectorAll('#sndRows [data-stop]')].map(b => b.disabled).join()") == 'true,true,false,true')
            await asyncio.sleep(2.6)
            check('Test 도 반복 주기(1초)를 따름', await pg.js(OSC) - base >= 3)
            check('Test 는 알람을 만들지 않음 (알람바 행 수 그대로)', await pg.js("app.alarms.length") == alarms_before)
            await pg.click_sel('#sndRows button[data-stop="L"]')
            await asyncio.sleep(0.5)
            quiet = await pg.js(OSC)
            await asyncio.sleep(2.2)
            check('Stop 을 누르면 멈춤', await pg.js(OSC) == quiet and await pg.js("sound.info().preview") is None
                  and await pg.js("[...document.querySelectorAll('#sndRows [data-stop]')].every(b => b.disabled)"))
            await pg.click_sel('#sndRows button[data-test="HH"]')
            check('Test 는 전체 켜짐/레벨 켜짐과 상관없이 들려 줌 (HH 는 파일 대신 합성음 4개씩, 선택 파일이 bad.wav 라 실패 후 대체)',
                  await pg.wait("sound.info().preview === 'HH'", 2) and await pg.wait(f"{OSC} - {quiet} >= 4", 4))
            await close_dialog()
            await asyncio.sleep(0.5)
            quiet = await pg.js(OSC)
            await asyncio.sleep(2.2)
            check('Test 중 설정 화면을 닫으면 멈춤', await pg.js(OSC) == quiet and await pg.js("sound.info().preview") is None)

            # ---- 저장 (FRA3-7) ----
            saved = await pg.js("localStorage.getItem('pinky.alarmsound.v1')")
            sj = json.loads(saved) if saved else {}
            check('localStorage pinky.alarmsound.v1 에 저장됨', sj.get('interval') == 1 and sj.get('levels', {}).get('HH', {}).get('file') == 'bad.wav' and sj.get('enabled') is True, saved)
            await pg.call('Page.navigate', url=BASE + '/')
            check('새로고침 뒤 로드', await pg.wait("app.cfg && app.wsUp && typeof soundCfg !== 'undefined'", 10))
            check('새로고침해도 설정이 유지됨', await pg.js("soundCfg.s.interval") == 1 and await pg.js("soundCfg.s.levels.HH.file") == 'bad.wav')

            # ---- 저장소를 못 써도 동작 ----
            await pg.call('Page.addScriptToEvaluateOnNewDocument', source=BLOCK_STORAGE)
            await pg.call('Page.navigate', url=BASE + '/')
            check('저장소가 막혀도 페이지가 뜸', await pg.wait("app.cfg && app.wsUp && typeof soundCfg !== 'undefined'", 10))
            check('저장소가 막히면 기본 설정', await pg.js("soundCfg.s.interval") == 5 and await pg.js("soundCfg.s.enabled") is True)
            await pg.mouse(40, 30)
            await open_dialog()
            await set_value('#sndInterval', '2')
            base = await pg.js(OSC)
            await pg.click_sel('#sndRows button[data-test="LL"]')
            check('저장소가 막혀도 설정 변경과 Test 가 동작', await pg.js("soundCfg.s.interval") == 2 and await pg.wait(f"{OSC} > {base}", 3))
            await pg.click_sel('#sndRows button[data-stop="LL"]')
            await close_dialog()

        print('\n전체 결과:', 'ALL PASS' if not FAILS else f'FAIL {len(FAILS)}건: {FAILS}')
    finally:
        br.terminate()
        srv.terminate()
        for p in (br, srv):
            try:
                p.wait(5)
            except Exception:
                p.kill()
        for d in (hist_dir, sound_dir):
            shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(prof, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shot')
    ap.add_argument('--hint-shot')
    a = ap.parse_args()
    asyncio.run(run(a.shot, a.hint_shot))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
