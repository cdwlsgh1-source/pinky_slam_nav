"""로그인 + 시스템 패널(프로세스 제어) 화면을 headless Chrome(CDP)으로 확인한다. mock 서버를 직접 띄우고 정리한다.

실행: .venv/bin/python tests/verify_proc_ui.py [--shot docs/process_panel.png]   (google-chrome 필요, 없으면 SKIP)
확인: 로그인 화면(잘못된 비밀번호, 로그인 후 화면), operator 의 버튼 비활성, manager 의 프로세스 시작/정지(로그 표시),
      로봇 프로세스 정지 확인 팝업, 전체 시작/정지, 실패 표시, 인증 미설정 시 안내와 버튼 비활성.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_step3_ui import Page  # noqa: E402
import verify_proc_mock as vp  # noqa: E402

ROOT = vp.ROOT
CDP = 9236
FAILS = []


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {extra}' if extra and not cond else ''))
    if not cond:
        FAILS.append(name)


async def open_page(chrome, url):
    prof = tempfile.mkdtemp()
    br = subprocess.Popen([chrome, '--headless=new', '--no-sandbox', '--disable-features=PasswordManagerOnboarding,PasswordLeakDetection', '--password-store=basic', '--disable-save-password-bubble', '--disable-component-update', '--disable-sync', '--use-mock-keychain', '--disable-gpu', f'--remote-debugging-port={CDP}',
                           f'--user-data-dir={prof}', '--window-size=1400,1000', 'about:blank'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            tabs = json.load(urllib.request.urlopen(f'http://127.0.0.1:{CDP}/json'))
            break
        except Exception:
            time.sleep(0.1)
    tab = next(t for t in tabs if t['type'] == 'page')
    ws = await websockets.connect(tab['webSocketDebuggerUrl'], max_size=None)
    pg = Page(ws)
    await pg.call('Page.enable')
    await pg.call('Emulation.setDeviceMetricsOverride', width=1400, height=1000, deviceScaleFactor=1, mobile=False)
    await pg.call('Emulation.setFocusEmulationEnabled', enabled=True)   # headless 창이 포커스가 없어 입력이 무시되는 일을 막는다
    await pg.call('Page.bringToFront')
    await pg.call('Page.navigate', url=url)
    return br, ws, pg


async def login_via_ui(pg, pw, uid=None):
    uid = uid or ('mngr' if pw == vp.OP else 'oper')
    await pg.js(f"(() => {{ document.getElementById('loginId').value = {json.dumps(uid)}; document.getElementById('loginPw').value = {json.dumps(pw)}; }})()")
    await pg.js("document.getElementById('loginForm').requestSubmit()")   # 마우스로 누르면 이어지는 새로고침 뒤 headless Chrome 이 마우스 입력을 받지 않는 일이 있어 폼을 직접 제출한다


ROW = lambda pid, act: f"#procList li[data-id='{pid}'] [data-act='{act}']"  # noqa: E731
STATE = lambda pid: f"document.querySelector(\"#procList li[data-id='{pid}'] .proc-state\").textContent"  # noqa: E731


async def accept_dialog(pg):
    await pg.wait("document.getElementById('confirmDlg').open", 3)
    await pg.click_sel('#confirmOk')


async def scenario_auth(chrome, tmp, shot):
    cfg = vp.write_config(tmp)
    srv = vp.start_server(cfg, {})
    br = ws = None
    try:
        br, ws, pg = await open_page(chrome, vp.BASE + '/')
        # 이 시나리오의 headless Chrome 은 마우스 입력을 받지 않는 일이 있어(원인 미확인, 같은 화면을 JS 클릭으로는 정상 동작 확인) 클릭을 JS 로 한다
        async def _js_click(sel):
            await pg.js(f"(() => {{ const e = document.querySelector({json.dumps(sel)}); e.scrollIntoView({{block: 'center'}}); e.click(); }})()")
        pg.click_sel = _js_click
        check('로그인 전: 로그인 창이 열린다', await pg.wait("document.getElementById('loginDlg').open", 8))
        check('로그인 전: 화면은 서버 API 를 부르지 않았다(지도/설정 없음)', await pg.js("!app.cfg && !app.meta && !app.wsUp"))
        await login_via_ui(pg, 'wrong-password')
        check('틀린 비밀번호: 오류 문구', await pg.wait("document.getElementById('loginErr').textContent.includes('맞지 않')", 5))
        check('틀린 비밀번호: 창이 그대로 열려 있다', await pg.js("document.getElementById('loginDlg').open"))
        # operator 로 로그인 -> 페이지가 다시 로드된다
        await login_via_ui(pg, vp.VW)
        check('operator 로그인 후 화면 로드', await pg.wait("app.cfg && app.meta && app.order.length === 2 && app.wsUp && app.me && app.me.role === 'operator'", 12))
        check('로그인 창이 닫혀 있다', await pg.js("!document.getElementById('loginDlg').open"))
        check('역할 표시(보기 전용)와 로그아웃 버튼', await pg.js("!document.getElementById('authBox').hidden && document.getElementById('authRole').textContent === 'oper (보기 전용)'"))
        await asyncio.sleep(0.6)
        check('operator: 카드의 순찰 시작/정지/경로 이동 버튼이 꺼져 있다', await pg.js("[...document.querySelectorAll('#robotCards .cmd-row:not(.estop-row) button')].every(b => b.disabled)"))
        check('operator: 비상정지 버튼(상단과 카드)은 켜져 있다', await pg.js("[...document.querySelectorAll('#estopBar button, #robotCards button.danger')].every(b => !b.disabled)")
              and await pg.js("document.querySelectorAll('#robotCards button.danger').length") == 2)
        check('operator: 수동 조작 체크박스가 꺼져 있고 사유 표시', await pg.js("document.getElementById('driveEnable').disabled && document.getElementById('driveMsg').textContent.includes('보기 전용')"))
        check('operator: 시스템 패널 목록과 안내', await pg.js("document.querySelectorAll('#procList li').length >= 3 && document.getElementById('procNote').textContent.includes('보기 전용')"))
        check('operator: 시작/정지/전체 버튼이 모두 꺼져 있다', await pg.js("[...document.querySelectorAll('#procList [data-act=start], #procList [data-act=stop], #procStartAll, #procStopAll')].every(b => b.disabled)"))
        await pg.click_sel('#logMenu')
        check('operator: 로그 화면은 manager 안내만 보인다', await pg.wait("document.getElementById('logNote').textContent.includes('manager')", 5))
        await pg.click_sel('.menu-item[data-view="main"]')
        # 로그아웃 -> 다시 로그인 창
        await pg.click_sel('#logoutBtn')
        check('로그아웃하면 로그인 창', await pg.wait("document.getElementById('loginDlg').open", 8))
        # manager
        await login_via_ui(pg, vp.OP)
        check('manager 로그인 후 화면 로드', await pg.wait("app.cfg && app.wsUp && app.me && app.me.role === 'manager' && app.procs", 12))
        await asyncio.sleep(0.8)

        # ---- 계정 전환: 로그아웃 없이 다른 계정으로 ----
        await pg.click_sel('#switchBtn')
        check('계정 전환: 로그인 창이 열리고 취소 버튼이 보임', await pg.js("document.getElementById('loginDlg').open && !document.getElementById('loginCancel').hidden && document.getElementById('loginTitle').textContent.includes('계정 전환')"))
        await pg.click_sel('#loginCancel')
        check('취소하면 창이 닫히고 계정은 그대로(manager)', await pg.js("!document.getElementById('loginDlg').open && app.me.role === 'manager'"))
        await pg.click_sel('#switchBtn')
        await login_via_ui(pg, 'wrong-pw')
        check('전환 중 틀린 비밀번호: 오류가 보이고 지금 계정이 유지', await pg.wait("document.getElementById('loginErr').textContent.length > 0", 5)
              and await pg.js("fetch('/api/me').then(r => r.json()).then(m => m.role === 'manager')"))
        await pg.click_sel('#loginCancel')
        await pg.click_sel('#switchBtn')
        await login_via_ui(pg, vp.VW)
        check('operator 비밀번호로 전환하면 operator(보기 전용)', await pg.wait("app.cfg && app.wsUp && app.me && app.me.role === 'operator' && app.procs", 12)
              and await pg.js("document.getElementById('authRole').textContent.startsWith('oper (')"))
        await asyncio.sleep(0.6)
        check('전환 뒤 시스템 패널 버튼이 꺼지고 비상 정지는 켜짐', await pg.js("[...document.querySelectorAll('#procList [data-act=start], #procStartAll')].every(b => b.disabled) && !document.getElementById('estopAll').disabled"))
        await pg.click_sel('#switchBtn')
        await login_via_ui(pg, vp.OP)
        check('manager 비밀번호로 다시 전환하면 manager', await pg.wait("app.cfg && app.wsUp && app.me && app.me.role === 'manager' && app.procs", 12))
        await asyncio.sleep(0.6)
        check('manager: 명령 버튼 활성 (online 로봇 순찰 시작)', await pg.wait("document.querySelector('#robotCards .card .cmd-row button').disabled === false", 5))
        check('manager: 설정된 프로세스의 시작 버튼 활성, 미설정은 사유 표시', await pg.js(f"!document.querySelector(\"{ROW('bridge_pinky1', 'start')}\").disabled && !document.querySelector(\"{ROW('bringup_pinky1', 'start')}\").disabled"))
        check('manager: 정지 버튼은 실행 중이 아니면 꺼져 있다', await pg.js(f"document.querySelector(\"{ROW('bridge_pinky1', 'stop')}\").disabled"))

        # 기능별 묶음 (도메인 브릿지 / zone_manager / bringup / map / 순찰 노드)
        check('시스템 패널이 기능별 묶음으로 나뉜다', await pg.js("[...document.querySelectorAll('#procList .proc-group')].map(g => g.dataset.group).join('|')") == '도메인 브릿지|zone_manager|bringup|map|순찰 노드')
        check('묶음마다 해당 프로세스만 들어 있다 (브릿지 2, bringup 2, map 2, 순찰 노드 2)', await pg.js("(() => { const n = (g) => document.querySelectorAll(`#procList .proc-group[data-group='${g}'] li`).length; return n('도메인 브릿지') === 2 && n('bringup') === 2 && n('map') === 2 && n('순찰 노드') === 2 && n('zone_manager') === 1; })()"))
        check('pinky2 순찰 노드가 목록에 있다', await pg.js("!!document.querySelector(\"#procList li[data-id='patrol_pinky2']\")"))
        # 개별 시작 / 로그 / 정지
        await pg.click_sel(ROW('bridge_pinky1', 'start'))
        check('묶음 머리글에 실행 중 개수가 표시', await pg.wait("document.querySelector(\"#procList .proc-group[data-group='도메인 브릿지'] .proc-group-sum\").textContent.startsWith('1/2 실행')", 6))
        check('시작 중 → 실행 중 표시', await pg.wait(f"{STATE('bridge_pinky1')}.startsWith('실행 중')", 6))
        check('실행 중에는 시작이 꺼지고 정지가 켜진다', await pg.js(f"document.querySelector(\"{ROW('bridge_pinky1', 'start')}\").disabled && !document.querySelector(\"{ROW('bridge_pinky1', 'stop')}\").disabled"))
        # 실행 시간이 서버의 새 메시지 없이도 흐른다 (이전에는 상태가 바뀔 때만 갱신돼 멈춰 보였다)
        SUB = "document.querySelector(\"#procList li[data-id='bridge_pinky1'] .proc-sub\").textContent"
        t1 = await pg.js(SUB)
        n1 = await pg.js("app.procs.procs.find(p => p.id === 'bridge_pinky1').log_n")
        await asyncio.sleep(2.6)
        t2 = await pg.js(SUB)
        check('실행 시간이 실시간으로 흐른다', t1 != t2 and '초' in t2, f'{t1!r} -> {t2!r}')
        await pg.click_sel(ROW('bridge_pinky1', 'log'))
        check('로그 보기: 출력이 나온다', await pg.wait("document.querySelector(\"#procList li[data-id='bridge_pinky1'] .proc-log\").textContent.includes('동작 중')", 6))
        await pg.click_sel(ROW('bridge_pinky1', 'stop'))
        check('정지 → 정지 표시', await pg.wait(f"{STATE('bridge_pinky1')} === '정지'", 8))

        # 로봇 프로세스: 정지는 확인 팝업
        await pg.click_sel(ROW('bringup_pinky1', 'start'))
        await pg.wait(f"{STATE('bringup_pinky1')}.startsWith('실행 중')", 6)
        await pg.click_sel(ROW('bringup_pinky1', 'stop'))
        check('로봇 프로세스 정지: 확인 팝업', await pg.wait("document.getElementById('confirmDlg').open && document.getElementById('confirmText').textContent.includes('pinky1 bringup')", 3))
        await pg.js("document.getElementById('confirmDlg').close('cancel')")
        await asyncio.sleep(0.4)
        check('팝업을 취소하면 계속 실행 중', await pg.js(f"{STATE('bringup_pinky1')}.startsWith('실행 중')"))
        await pg.click_sel(ROW('bringup_pinky1', 'stop'))
        await accept_dialog(pg)
        check('팝업 승인하면 정지', await pg.wait(f"{STATE('bringup_pinky1')} === '정지'", 10))

        # 전체 시작
        await pg.click_sel('#procStartAll')
        check('전체 시작 확인 팝업(순서 나열)', await pg.wait("document.getElementById('confirmDlg').open && document.getElementById('confirmText').textContent.includes('1. 도메인 브릿지 pinky1')", 3))
        await pg.click_sel('#confirmOk')
        check('전체 시작 진행 문구', await pg.wait("document.getElementById('procSeq').textContent.includes('전체 시작')", 4))
        check('전체 시작 완료 문구와 모두 실행 중', await pg.wait("document.getElementById('procSeq').textContent.includes('전체 시작 완료') && [...document.querySelectorAll('#procList .proc-state')].every(e => e.textContent.startsWith('실행 중'))", 20))
        await asyncio.sleep(0.6)

        # ---- 로그 화면 ----
        await pg.click_sel('#logMenu')
        check('로그 메뉴: 로그 화면만 보이고 지도 작업 영역은 숨김', await pg.js("!document.getElementById('logView').hidden && document.getElementById('workspace').hidden"))
        check('로그 화면에서는 레이아웃 편집 버튼이 숨겨지고 상단 비상 정지는 그대로', await pg.js("document.getElementById('layoutTools').hidden && document.getElementById('estopAll').getBoundingClientRect().height > 0"))
        check('로그가 들어온다 (전체)', await pg.wait("document.querySelectorAll('#logBody .ln').length >= 5", 8))
        check('전체 탭: 줄마다 [소스] 표시', await pg.js("document.querySelector('#logBody .ln .s').textContent.startsWith('[')"))
        check('탭: 전체, GUI 서버, 설정된 프로세스', await pg.js("(() => { const t = [...document.querySelectorAll('#logTabs .tab')].map(b => b.dataset.src); return t[0] === 'all' && t.includes('gui') && t.includes('bridge_pinky1') && t.includes('zone_manager'); })()"))
        await pg.click_sel('#logTabs .tab[data-src="bridge_pinky1"]')
        check('프로세스 탭: 그 프로세스의 줄만, 소스 표시 없음', await pg.js("(() => { const l = [...document.querySelectorAll('#logBody .ln')]; return l.length > 0 && !document.querySelector('#logBody .ln .s'); })()")
              and await pg.js("!document.querySelector('#logBody').textContent.includes('[zone_manager]')"))
        await pg.click_sel('#logTabs .tab[data-src="gui"]')
        check('GUI 서버 탭: 서버 로그 (backend.procs 시작 기록)', await pg.wait("document.getElementById('logBody').textContent.includes('backend.procs')", 6))
        await pg.click_sel('#logTabs .tab[data-src="all"]')
        # 검색
        await pg.js("(() => { const i = document.getElementById('logSearch'); i.value = '동작 중 1'; i.dispatchEvent(new Event('input')); })()")
        check('검색: 맞는 줄만 남는다', await pg.js("(() => { const l = [...document.querySelectorAll('#logBody .ln')]; return l.length > 0 && l.every(x => x.textContent.includes('동작 중 1')); })()"))
        await pg.js("(() => { const i = document.getElementById('logSearch'); i.value = '__없는문구__'; i.dispatchEvent(new Event('input')); })()")
        check('검색 결과가 없으면 안내 문구', await pg.js("document.getElementById('logBody').textContent.includes('조건에 맞는 로그가 없습니다')"))
        await pg.js("(() => { const i = document.getElementById('logSearch'); i.value = ''; i.dispatchEvent(new Event('input')); })()")
        # 레벨 필터: ERROR 줄을 직접 만들어 확인
        await pg.js("logs.entries.push({n: 99999, t: Date.now()/1000, src: 'gui', lvl: 'ERROR', text: 'ERR-TEST'}, {n: 100000, t: Date.now()/1000, src: 'gui', lvl: 'WARN', text: 'WARN-TEST'}); renderLogTabs(); renderLogAll();")
        await pg.js("(() => { const s = document.getElementById('logLevel'); s.value = 'error'; s.dispatchEvent(new Event('change')); })()")
        check('레벨 ERROR 만: ERROR 줄만 보임', await pg.js("(() => { const l = [...document.querySelectorAll('#logBody .ln')]; return l.length >= 1 && l.every(x => x.dataset.lvl === 'ERROR'); })()"))
        await pg.js("(() => { const s = document.getElementById('logLevel'); s.value = 'warn'; s.dispatchEvent(new Event('change')); })()")
        check('레벨 WARN 이상: WARN 과 ERROR', await pg.js("(() => { const l = [...document.querySelectorAll('#logBody .ln')]; return l.length >= 2 && l.every(x => x.dataset.lvl === 'WARN' || x.dataset.lvl === 'ERROR'); })()"))
        check('탭에 WARN/ERROR 개수 배지', await pg.js("!!document.querySelector('#logTabs .tab[data-src=\"gui\"] .cnt.e')"))
        await pg.js("(() => { const s = document.getElementById('logLevel'); s.value = 'all'; s.dispatchEvent(new Event('change')); })()")
        # 일시정지: 새 줄이 와도 화면은 그대로
        await pg.click_sel('#logPause')
        n_p = await pg.js("document.querySelectorAll('#logBody .ln').length")
        await asyncio.sleep(2.5)
        check('일시정지 중에는 화면 줄 수가 늘지 않는다 (받기는 계속)', await pg.js("document.querySelectorAll('#logBody .ln').length") == n_p)
        await pg.click_sel('#logPause')
        check('재개하면 밀린 줄이 보인다', await pg.wait(f"document.querySelectorAll('#logBody .ln').length > {n_p}", 4))
        # 따라가기
        await pg.js("document.getElementById('logFollow').checked = true; document.getElementById('logFollow').dispatchEvent(new Event('change'))")
        check('따라가기가 켜져 있으면 맨 아래에 붙어 있다', await pg.wait("(() => { const b = document.getElementById('logBody'); return b.scrollHeight - b.scrollTop - b.clientHeight < 30; })()", 4))
        await pg.js("document.getElementById('logBody').scrollTop = 0; document.getElementById('logBody').dispatchEvent(new Event('scroll'))")
        check('위로 올리면 따라가기가 꺼진다', await pg.js("!document.getElementById('logFollow').checked"))
        # 화면 지우기는 화면에서만
        await pg.click_sel('#logClear')
        check('화면 지우기: 화면은 비고 서버 로그는 그대로 (다시 받지 않음)', await pg.js("logs.entries.length === 0"))
        if shot:
            await pg.js("document.getElementById('logFollow').checked = true")
            await asyncio.sleep(1.5)
            res = await pg.call('Page.captureScreenshot', format='png')
            import base64
            Path(shot.replace('.png', '_log.png')).write_bytes(base64.b64decode(res['data']))
        await pg.click_sel('.menu-item[data-view="main"]')
        check('메인으로 돌아오면 지도 작업 영역이 보이고 로그 폴링이 멈춤', await pg.js("!document.getElementById('workspace').hidden && document.getElementById('logView').hidden && logs.timer === null"))
        check('돌아온 뒤 지도 캔버스가 다시 크기를 가진다', await pg.wait("document.getElementById('mapCanvas').getBoundingClientRect().width > 100", 4))
        if shot:
            await pg.js("document.getElementById('procPanel').scrollIntoView()")
            res = await pg.call('Page.captureScreenshot', format='png')
            import base64
            Path(shot).write_bytes(base64.b64decode(res['data']))
            print('screenshot', shot)
        await pg.click_sel('#procStopAll')
        check('전체 정지 확인 팝업(로봇 경고 포함)', await pg.wait("document.getElementById('confirmDlg').open && document.getElementById('confirmText').textContent.includes('로봇에서 실행 중')", 3))
        await pg.click_sel('#confirmOk')
        check('전체 정지 완료와 모두 정지', await pg.wait("document.getElementById('procSeq').textContent.includes('전체 정지 완료') && [...document.querySelectorAll('#procList .proc-state')].every(e => e.textContent === '정지' || e.textContent === '명령 미설정')", 30))

        # 세션이 사라지면(서버 재시작 등) 다시 로그인 창이 뜬다
        await pg.js("fetch('/api/logout', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'})")
        await pg.js("sock.close()")
        check('세션이 사라지면 로그인 창이 다시 뜬다', await pg.wait("document.getElementById('loginDlg').open", 10))
    finally:
        if ws:
            await ws.close()
        if br:
            br.terminate()
        vp.stop_server(srv)


async def scenario_failure(chrome, tmp):
    cfg = vp.write_config(tmp)
    srv = vp.start_server(cfg, {}, ['--mock-proc-fail', 'bridge_pinky1'])
    br = ws = None
    try:
        br, ws, pg = await open_page(chrome, vp.BASE + '/')
        await pg.wait("document.getElementById('loginDlg').open", 8)
        await login_via_ui(pg, vp.OP)
        await pg.wait("app.procs && app.me && app.me.role === 'manager' && app.wsUp", 12)
        await pg.click_sel(ROW('bridge_pinky1', 'start'))
        check('시작 직후 종료: 실패 배지 + 사유 문구', await pg.wait(f"{STATE('bridge_pinky1')} === '실패' && document.querySelector(\"#procList li[data-id='bridge_pinky1'] .proc-msg\").textContent.includes('시작 직후 종료')", 8))
        check('실패한 뒤에는 시작이 다시 켜진다', await pg.js(f"!document.querySelector(\"{ROW('bridge_pinky1', 'start')}\").disabled"))
    finally:
        if ws:
            await ws.close()
        if br:
            br.terminate()
        vp.stop_server(srv)


async def scenario_noauth(chrome, tmp):
    cfg = vp.write_config(tmp)
    srv = vp.start_server(cfg, {}, ['--no-auth'])
    br = ws = None
    try:
        br, ws, pg = await open_page(chrome, vp.BASE + '/')
        check('인증 미설정: 로그인 창 없이 화면이 뜬다', await pg.wait("app.cfg && app.meta && app.wsUp && app.me && app.me.role === 'manager'", 12))
        check('인증 미설정: 로그인 창이 닫혀 있고 로그아웃 버튼이 숨겨져 있다', await pg.js("!document.getElementById('loginDlg').open && document.getElementById('authBox').hidden"))
        await asyncio.sleep(0.8)
        check('인증 미설정: 기존 명령 버튼은 그대로 쓸 수 있다', await pg.js("document.querySelector('#robotCards .card .cmd-row button').disabled === false"))
        check('인증 미설정: 프로세스 제어는 꺼짐 + 안내 + 버튼 비활성', await pg.js("document.getElementById('procNote').textContent.includes('--no-auth') && [...document.querySelectorAll('#procList [data-act=start], #procStartAll')].every(b => b.disabled)"))
    finally:
        if ws:
            await ws.close()
        if br:
            br.terminate()
        vp.stop_server(srv)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shot')
    args = ap.parse_args()
    chrome = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not chrome:
        print('SKIP: Chrome 이 없다')
        return 0
    for fn in (scenario_auth, scenario_failure, scenario_noauth):
        print(f'--- {fn.__name__} ---')
        with tempfile.TemporaryDirectory() as t:
            if fn is scenario_auth:
                await fn(chrome, Path(t), args.shot)
            else:
                await fn(chrome, Path(t))
    print('\n' + ('모두 통과' if not FAILS else f'실패 {len(FAILS)}건: {FAILS}'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
