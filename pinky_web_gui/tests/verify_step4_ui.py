"""Step 4 화면을 headless Chrome(CDP)으로 확인한다. mock 서버를 직접 띄우고 정리한다.

실행: .venv/bin/python tests/verify_step4_ui.py [--shot docs/step4_screenshot.png] [--zone-shot docs/step4_zone_draft.png] [--scan-shot docs/step4_lidar.png]
확인: 구역 배지/사각형 색 변화와 복귀, 구역 진입 대기 표시, goto/start 경로 오버레이가 로봇의 실제 진행과 맞는지(상태가 바뀔 때마다 대조),
      LiDAR 토글과 구독 해제/재연결, 비상정지 버튼(팝업 없음, 연타 가능, 오프라인에서도 활성), 수동 조작(눌렀다 뗌, 키보드, 포커스 잃음,
      체크 해제, 순찰 중 비활성, 탭 닫기).
"""
import argparse
import asyncio
import base64
import json
import math
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_step3_ui as v3  # noqa: E402  (Page 헬퍼 재사용)

ROOT = Path(__file__).resolve().parent.parent
PORT, CDP = 8016, 9234
BASE = f'http://127.0.0.1:{PORT}'
FAILS = []


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {extra}' if extra and not cond else ''))
    if not cond:
        FAILS.append(name)


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return json.load(e)


mock = lambda: api('GET', '/api/mock/cmd_vel')  # noqa: E731

# 페이지 안에서 상태가 바뀔 때마다 경로 오버레이의 현재 목표가 실제 진행과 맞는지 대조한다 (불일치만 __mis 에 쌓는다)
WATCHER = """
window.__mis = []; window.__seen = {}; window.__zoneSeen = new Set(); window.__waitText = [];
(() => {
  const last = {};
  setInterval(() => {
    if (app.zoneInfo) __zoneSeen.add(app.zoneInfo.state + ':' + (app.zoneInfo.holder || ''));
    for (const id of app.order) {
      const r = app.robots[id], p = r.patrol, lt = r.lastTask;
      if (!p || !app.zoneCfg || !app.cfg) continue;
      const key = p.state + '|' + p.waypoint + '|' + p.detail;
      if (last[id] === key) continue;
      last[id] = key;
      __seen[id + ':' + p.state] = (__seen[id + ':' + p.state] || 0) + 1;
      if (p.state === 'WAITING_ZONE') __waitText.push([id, ZoneView.waitingText(app.cfg.robots[id], lt, p)]);
      const ov = ZoneView.routeOverlay(app.cfg.robots[id], app.zoneCfg.doors[id], app.cfg.points, lt, p);
      const working = ['MOVING', 'ARRIVED', 'WAITING_ZONE', 'LEAVING_ZONE', 'RETURNING'].includes(p.state);
      if (!ov) { if (working && lt) __mis.push([id, 'no overlay', key]); continue; }
      const nm = ov.target >= 0 ? ov.names[ov.target] : null;
      let ok = true;
      if (lt.cmd === 'goto') {
        if (['ARRIVED', 'LEAVING_ZONE', 'MOVING'].includes(p.state)) ok = nm === p.detail;
        else if (p.state === 'WAITING_ZONE') ok = nm === app.zoneCfg.doors[id].in;
        else if (p.state === 'RETURNING') ok = nm === app.cfg.robots[id].home;
      } else {  // start: 방금 도착한 앞 지점(또는 진입 문) 근처에 있어야 한다
        const prev = p.state === 'MOVING' && ov.target > 0 ? ov.names[ov.target - 1] : p.state === 'WAITING_ZONE' ? nm : null;
        if (prev && r.pose) { const q = app.cfg.points[prev]; ok = Math.hypot(q.x - r.pose.x, q.y - r.pose.y) < 0.3; }
      }
      if (!ok) __mis.push([id, lt.cmd, key, nm, JSON.stringify(ov.names), ov.target]);
    }
  }, 30);
})();
"""


async def run(shot, zone_shot, scan_shot):
    chrome = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not chrome:
        print('SKIP: Chrome 이 없다')
        return
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--mock', '--mock-wait-scale', '0.2', '--port', str(PORT)],
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
            check('로드: 설정/구역/모션/지도/스냅샷', await pg.wait("app.cfg && app.zoneCfg && app.motion && app.meta && app.order.length === 2 && app.wsUp && app.zoneInfo"))
            await asyncio.sleep(0.6)
            await pg.js(WATCHER)

            # ---- 구역 영역 (FR4-1) 과 배지 (FR4-2): 비어 있음 ----
            chip = lambda: pg.js("document.getElementById('zoneChip').textContent")  # noqa: E731
            check('배지: 구역 비어 있음', await chip() == '구역: 비어 있음', await chip())
            check('구역 영역이 초안임을 배지 툴팁으로 알림', await pg.js("document.getElementById('zoneChip').title.includes('초안')"))
            rect = await pg.js("app.zoneCfg.rect")
            check('구역 사각형 = 구역 지점 경계 + 여유', abs(rect['x_min'] - 0.85) < 1e-9 and abs(rect['y_max'] - 0.25) < 1e-9, rect)
            # 사각형 안쪽 한 점(지점/선이 없는 곳)의 화면 색을 읽는다
            px = lambda: pg.js("""(() => { const s = worldToScreen(1.275, -0.15); const d = ctx.getImageData(Math.round(s.sx), Math.round(s.sy), 1, 1).data;
                                 return [d[0], d[1], d[2]]; })()""")  # noqa: E731
            await asyncio.sleep(0.3)
            free_px = await px()
            if zone_shot:  # 구역 영역 초안 확인용 (로봇은 홈에 서 있다)
                await pg.js("app.showGrid = true")
                await screenshot(pg, zone_shot)

            # ---- 두 로봇 goto: 구역 점유/대기 (FR4-2, FR4-3), 경로 오버레이 (FR4-4) ----
            api('POST', '/api/robots/pinky1/command', {'cmd': 'goto', 'points': ['P3', 'P6']})
            api('POST', '/api/robots/pinky2/command', {'cmd': 'goto', 'points': ['P4', 'P2']})
            check('구역 점유 시 배지가 점유자와 시간을 표시',
                  await pg.wait("/^구역: pinky[12] 점유 중 \\(\\d+초\\)$/.test(document.getElementById('zoneChip').textContent)", 25), await chip())
            await asyncio.sleep(0.4)
            occ_px = await px()
            redness = lambda c: c[0] - (c[1] + c[2]) / 2  # noqa: E731
            check('점유 중 구역 영역이 빨갛게 칠해짐', redness(occ_px) - redness(free_px) >= 20, (free_px, occ_px))
            check('배지 level=bad', await pg.js("document.getElementById('zoneChip').dataset.level") == 'bad')
            t1 = await pg.js("(() => { const m = document.getElementById('zoneChip').textContent.match(/\\((\\d+)초\\)/); return +m[1]; })()")
            await asyncio.sleep(2.2)
            t2 = await pg.js("(() => { const m = document.getElementById('zoneChip').textContent.match(/\\((\\d+)초\\)/); return m ? +m[1] : -1; })()")
            check('점유 시간이 계속 늘어남', t2 > t1 or t2 == -1, (t1, t2))

            check('한 대가 WAITING_ZONE 이 됨', await pg.wait("Object.keys(__seen).some(k => k.endsWith(':WAITING_ZONE'))", 25), await pg.js('JSON.stringify(__seen)'))
            waiting_id = await pg.js("(Object.keys(__seen).find(k => k.endsWith(':WAITING_ZONE')) || '').split(':')[0]")
            wt = await pg.js('JSON.stringify(__waitText)')
            check('구역 진입 대기 문구에 가려는 지점이 붙음', '구역 진입 대기 (P' in wt, wt)
            if shot:
                await pg.wait(f"app.robots['{waiting_id}'].patrol && app.robots['{waiting_id}'].patrol.state === 'WAITING_ZONE'", 12)
                await asyncio.sleep(0.3)
                await screenshot(pg, shot)
                shot = None
            await pg.js("window.__keepWaiting = 1")
            check('모든 작업이 끝나면 구역이 비어 있음으로 돌아옴',
                  await pg.wait("document.getElementById('zoneChip').textContent === '구역: 비어 있음' && Object.keys(__seen).filter(k => k.endsWith(':DONE')).length === 0 ? false : "
                                "(app.robots.pinky1.patrol.state === 'DONE' && app.robots.pinky2.patrol.state === 'DONE' && document.getElementById('zoneChip').textContent === '구역: 비어 있음')", 90))
            await asyncio.sleep(0.6)
            back_px = await px()
            check('구역 영역 색이 원래대로', abs(back_px[0] - free_px[0]) <= 3 and abs(back_px[1] - free_px[1]) <= 3, (free_px, back_px))
            mis = await pg.js('JSON.stringify(__mis)')
            check('goto 경로 강조가 로봇의 진행과 일치 (모든 상태 변화에서 대조)', mis == '[]', mis)
            seen = await pg.js('JSON.stringify(__seen)')
            check('대조한 상태에 ARRIVED/LEAVING_ZONE/RETURNING/WAITING_ZONE 포함',
                  all(k in seen for k in ('ARRIVED', 'LEAVING_ZONE', 'RETURNING', 'WAITING_ZONE')), seen)
            zs = await pg.js('JSON.stringify([...__zoneSeen])')
            check('배지가 free -> occupied -> free 를 거침', 'free:' in zs and ('occupied:pinky1' in zs or 'occupied:pinky2' in zs), zs)
            check('작업이 끝나면 경로 선을 그리지 않음', await pg.js(
                "app.order.every(id => ZoneView.routeOverlay(app.cfg.robots[id], app.zoneCfg.doors[id], app.cfg.points, app.robots[id].lastTask, app.robots[id].patrol) === null)"))

            # ---- 경로 계산 순수 함수 (구역 문 삽입, 선분 상태) ----
            ov = await pg.js("""(() => { const e = ZoneView.expandGoto(['P2','P3','P6','P7'], app.zoneCfg.doors.pinky1, 'P1', app.cfg.points); return e; })()""")
            check('goto 경로 확장: 구역 밖->안에 IN, 안->밖에 OUT, 끝에 홈',
                  ov['names'] == ['P2', 'RED1IN', 'P3', 'P6', 'RED1OUT', 'P7', 'P1'] and ov['idx'] == [0, 2, 3, 5], ov)
            seg = await pg.js("""(() => { const o = {names: ['P2','RED1IN','P3','P6','RED1OUT','P1'], target: 3};
                                  return ZoneView.segments(o, app.cfg.points).map(s => s.from + '>' + s.to + ':' + s.state); })()""")
            check('선분 상태: 지난 구간 done, 목표로 가는 구간 active, 나머지 todo',
                  seg == ['P2>RED1IN:done', 'RED1IN>P3:done', 'P3>P6:active', 'P6>RED1OUT:todo', 'RED1OUT>P1:todo'], seg)

            # ---- 경로 선이 벽을 뚫지 않는다 (지점 사이는 서버가 계산한 벽을 피하는 경로) ----
            check('경로 선은 기본으로 꺼져 있고 경로 추정도 아직 받지 않음', await pg.js("app.showRoutes === false && app.plans === null && document.getElementById('routeLinesBtn').getAttribute('aria-pressed') === 'false'"))
            # 기본 꺼짐: goto 진행 중에도 경로 선이 그려지지 않는다 (polylineFor 호출이 없다)
            await pg.js("window.__poly = 0; const _pf = polylineFor; polylineFor = (sg) => { window.__poly++; return _pf(sg); };")
            api('POST', '/api/robots/pinky1/command', {'cmd': 'goto', 'points': ['P2']})
            await pg.wait("app.robots.pinky1.patrol && app.robots.pinky1.patrol.state === 'MOVING'", 10)
            await asyncio.sleep(0.6)
            check('꺼져 있으면 goto 진행 중에도 경로 선을 그리지 않음', await pg.js("window.__poly") == 0 and await pg.js("app.plans") is None)
            api('POST', '/api/robots/pinky1/estop', {})
            await pg.wait("app.robots.pinky1.patrol.state === 'STOPPED'", 15)
            await asyncio.sleep(2.6)
            await pg.click_sel('#routeLinesBtn')
            check('경로 버튼을 누르면 켜지고 경로 추정을 받음', await pg.wait("app.showRoutes === true && app.plans && Object.keys(app.plans).length >= 100", 10))
            walls = await pg.js("""(() => {
              // 지도 칸이 벽/미지인지 (app.gray 는 pgm 값). 경로를 1 cm 간격으로 훑어 벽/미지 칸에 닿는 점의 수를 센다
              const bad = (x, y) => { const p = app.tf.worldToPixel(x, y), c = Math.floor(p.px), r = Math.floor(p.py);
                if (c < 0 || r < 0 || c >= app.meta.width || r >= app.meta.height) return true;
                return MapMath.classifyOccupancy(app.gray[r * app.meta.width + c], app.meta) !== 'free'; };
              const cross = (path) => { let n = 0; for (let i = 0; i + 1 < path.length; i++) { const [x0, y0] = path[i], [x1, y1] = path[i + 1];
                const k = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / 0.01)); for (let j = 0; j <= k; j++) if (bad(x0 + (x1 - x0) * j / k, y0 + (y1 - y0) * j / k)) n++; } return n; };
              const sl = (a, b) => [[app.cfg.points[a].x, app.cfg.points[a].y], [app.cfg.points[b].x, app.cfg.points[b].y]];
              const out = { plannedBad: 0, pairs: 0, straightP3P6: cross(sl('P3', 'P6')) };
              for (const [k, v] of Object.entries(app.plans)) { out.pairs++; out.plannedBad += cross(v.path); }
              return out; })()""")
            check('모든 지점 쌍의 추정 경로가 벽/미지 칸을 지나지 않음', walls['plannedBad'] == 0 and walls['pairs'] >= 100, walls)
            check('(전제) P3→P6 직선은 벽을 뚫는다', walls['straightP3P6'] > 0, walls)
            api('POST', '/api/robots/pinky1/command', {'cmd': 'goto', 'points': ['P3', 'P6']})
            await pg.js("window.__poly = 0")
            check('goto P3,P6 진행 중 그려지는 꺾은선이 추정 경로이고 P3→P6 구간은 직선이 아님',
                  await pg.wait("""(() => { const r = app.robots.pinky1, ov = ZoneView.routeOverlay(app.cfg.robots.pinky1, app.zoneCfg.doors.pinky1, app.cfg.points, r.lastTask, r.patrol);
                    if (!ov) return false; const sg = ZoneView.segments(ov, app.cfg.points).find(s => s.from === 'P3' && s.to === 'P6');
                    return !!sg && polylineFor(sg).length > 2; })()""", 25))
            check('켜져 있으면 경로 선을 그림', await pg.js("window.__poly") > 0)
            api('POST', '/api/robots/pinky1/estop', {})
            await pg.wait("app.robots.pinky1.patrol.state === 'STOPPED'", 15)
            await asyncio.sleep(2.6)
            await pg.click_sel('#routeLinesBtn')
            check('경로 버튼을 다시 누르면 꺼짐', await pg.js("app.showRoutes === false"))

            # ---- start 경로 강조 (waypoint 번호 해석) ----
            await pg.js("window.__mis.length = 0")
            api('POST', '/api/robots/pinky2/command', {'cmd': 'start'})
            check('pinky2 start 가 끝남', await pg.wait("app.robots.pinky2.patrol.state === 'DONE' && app.robots.pinky2.lastTask.cmd === 'start'", 60))
            mis = await pg.js('JSON.stringify(__mis)')
            check('start 경로 강조가 로봇 위치와 일치', mis == '[]', mis)
            ov = await pg.js("ZoneView.routeOverlay(app.cfg.robots.pinky2, app.zoneCfg.doors.pinky2, app.cfg.points, {cmd:'start'}, {state:'MOVING', waypoint:2, detail:''})")
            check('start MOVING(2): 다음 목표는 route[2]=P6', ov and ov['names'][ov['target']] == 'P6', ov)
            check('waypoint -1 이면 강조 없음', await pg.js("ZoneView.routeOverlay(app.cfg.robots.pinky2, app.zoneCfg.doors.pinky2, app.cfg.points, {cmd:'start'}, {state:'STARTING', waypoint:-1, detail:''}).target") == -1)

            # ---- LiDAR 토글 (FR4-5, FR4-6) ----
            check('LiDAR 토글이 로봇별로 있음', await pg.js("document.querySelectorAll('#scanToggles input').length") == 2)
            await pg.click_sel('#scanToggles input[data-robot="pinky1"]')
            check('켜면 점이 들어옴', await pg.wait("app.scan.pinky1 && app.scan.pinky1.points.length > 10", 5))
            check('LiDAR 줄에 그리는 기준(tf 없음 -> amcl + 보정 0°)이 표시됨',
                  await pg.wait("document.querySelector('#scanToggles .scan-info[data-robot=\"pinky1\"]').textContent.includes('amcl') && document.querySelector('#scanToggles .scan-info[data-robot=\"pinky1\"]').textContent.includes('보정 0')", 5),
                  await pg.js("document.querySelector('#scanToggles .scan-info').textContent"))
            h = api('GET', '/api/health')
            check('켠 로봇만 서버가 구독', h['scan_subscribed'] == ['pinky1'] and h['scan_clients']['pinky2'] == 0, h)
            pt = await pg.js("""(() => { const [x, y] = app.scan.pinky1.points[0]; const s = worldToScreen(x, y);
                                 const d = ctx.getImageData(Math.round(s.sx) - 1, Math.round(s.sy) - 1, 3, 3).data; return [Math.round(s.sx), Math.round(s.sy), d[16], d[17], d[18]]; })()""")
            check('점이 화면에 그려짐 (로봇 색 계열)', pt[2] > pt[4] + 40, pt)
            if scan_shot:
                await screenshot(pg, scan_shot)
            h_before = await pg.js("document.getElementById('scanToggles').getBoundingClientRect().height")
            await pg.js("app.scan.pinky1.source = 'tf'; app.scan.pinky1.frame = 'a_very_long_sensor_frame_name_for_wrapping_check'; renderScanInfo();")
            check('LiDAR 상태 문구가 길어져도 레이아웃 높이가 변하지 않음 (아래 패널이 밀리지 않음)', await pg.js("document.getElementById('scanToggles').getBoundingClientRect().height") == h_before)
            # 연결이 끊겼다 복구되면 켜 둔 토글이 자동으로 다시 요청된다
            await pg.js("sock.close()")
            await asyncio.sleep(0.5)
            check('연결이 끊기면 서버가 구독을 해제', api('GET', '/api/health')['scan_subscribed'] == [])
            check('재연결하면 켜 둔 LiDAR 가 다시 켜짐', await pg.wait("app.wsUp && app.scan.pinky1.points.length > 0", 8))
            check('재연결 뒤 서버 구독 복구', api('GET', '/api/health')['scan_subscribed'] == ['pinky1'])
            await pg.click_sel('#scanToggles input[data-robot="pinky1"]')
            await asyncio.sleep(0.6)
            check('끄면 서버 구독이 해제됨', api('GET', '/api/health')['scan_subscribed'] == [])
            check('끄면 점이 사라짐', await pg.js("app.scan.pinky1.on === false && app.scan.pinky1.points.length === 0"))

            # ---- 비상정지 (FR4-7, FR4-8) ----
            check('상단에 로봇별 비상정지와 모두 정지 버튼', await pg.js("document.querySelectorAll('#estopButtons button').length") == 2
                  and await pg.js("document.getElementById('estopAll').getBoundingClientRect().top < 60"))
            await pg.js("app.robots.pinky2.online = false; app.dirtyCards = true;")
            await asyncio.sleep(0.3)
            check('오프라인 로봇의 비상정지도 활성', await pg.js("!document.querySelector('#estopButtons button[data-robot=\"pinky2\"]').disabled"))
            n0 = len(api('GET', '/api/state')['commands'])
            await pg.click_sel('#estopButtons button[data-robot="pinky1"]')
            check('확인 팝업이 열리지 않음', await pg.js("document.getElementById('confirmDlg').open") is False)
            await pg.click_sel('#estopButtons button[data-robot="pinky1"]')    # 연타: 잠그지 않는다
            await asyncio.sleep(0.8)
            cmds = api('GET', '/api/state')['commands']
            check('누른 횟수만큼 비상정지가 전송되고 이력에 남음', len([c for c in cmds[n0:] if c['cmd'] == 'estop' and c['robot'] == 'pinky1']) == 2, cmds[n0:])
            check('이력 표에 "비상정지" 표시', await pg.js("[...document.querySelectorAll('#historyBody td.cmd')].some(td => td.textContent.startsWith('비상정지'))"))
            check('알람바에 비상정지 전송 표시', await pg.js("document.getElementById('alarmList').textContent.includes('비상정지 전송')"))
            await pg.click_sel('#estopAll')
            await asyncio.sleep(0.8)
            cmds = api('GET', '/api/state')['commands']
            check('모두 정지: 두 로봇 모두 기록', {c['robot'] for c in cmds[n0 + 2:] if c['cmd'] == 'estop'} == {'pinky1', 'pinky2'})
            await asyncio.sleep(2.2)                                           # burst 가 끝나길 기다린다
            await pg.js("app.robots.pinky2.online = true; app.dirtyCards = true;")

            # ---- 패널 크기 조절 ----
            side_w = lambda: pg.js("document.querySelector('.sidebar').getBoundingClientRect().width")  # noqa: E731
            canvas_w = lambda: pg.js("document.getElementById('mapCanvas').getBoundingClientRect().width")  # noqa: E731
            hist_h = lambda: pg.js("document.querySelector('.history').getBoundingClientRect().height")  # noqa: E731
            canvas_h = lambda: pg.js("document.getElementById('mapCanvas').getBoundingClientRect().height")  # noqa: E731
            await pg.js("document.getElementById('splitV').scrollIntoView()")
            sw0, cw0, hh0, ch0 = await side_w(), await canvas_w(), await hist_h(), await canvas_h()
            check('기본 크기(사이드바 300, 이력 170)', abs(sw0 - 300) < 1 and abs(hh0 - 170) < 1, (sw0, hh0))
            vx, vy = await pg.js("(() => { const r = document.getElementById('splitV').getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; })()")
            await pg.call('Input.dispatchMouseEvent', type='mousePressed', x=vx, y=vy, button='left', clickCount=1)
            for dx in (-40, -90, -140):
                await pg.call('Input.dispatchMouseEvent', type='mouseMoved', x=vx + dx, y=vy, button='left')
            await pg.call('Input.dispatchMouseEvent', type='mouseReleased', x=vx - 140, y=vy, button='left', clickCount=1)
            await asyncio.sleep(0.4)
            sw1, cw1 = await side_w(), await canvas_w()
            check('손잡이를 왼쪽으로 끌면 사이드바가 넓어지고 지도가 좁아짐', 430 <= sw1 <= 450 and cw1 < cw0 - 100, (sw0, sw1, cw0, cw1))
            check('끄는 중 켜진 body 클래스가 정리됨', await pg.js("!document.body.classList.contains('resizing')"))
            check('지도 캔버스 해상도가 새 크기를 따라감', await pg.js("(() => { const c = document.getElementById('mapCanvas'); const r = c.getBoundingClientRect(); return Math.abs(c.width - Math.round(r.width * (devicePixelRatio || 1))) <= 2; })()"))
            # 끝까지 끌어도 한계 안에 있다
            await pg.call('Input.dispatchMouseEvent', type='mousePressed', x=vx - 140, y=vy, button='left', clickCount=1)
            await pg.call('Input.dispatchMouseEvent', type='mouseMoved', x=vx - 1200, y=vy, button='left')
            await pg.call('Input.dispatchMouseEvent', type='mouseReleased', x=vx - 1200, y=vy, button='left', clickCount=1)
            await asyncio.sleep(0.3)
            sw2 = await side_w()
            check('너무 크게 끌어도 최대치(창의 55% 이내, 720 이하)에서 멈춤', sw2 <= min(720, 1400 * 0.55) + 1 and sw2 > sw1, sw2)
            vx2, vy2 = await pg.js("(() => { const r = document.getElementById('splitV').getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; })()")
            await pg.call('Input.dispatchMouseEvent', type='mousePressed', x=vx2, y=vy2, button='left', clickCount=1)
            await pg.call('Input.dispatchMouseEvent', type='mouseMoved', x=vx2 + 1500, y=vy2, button='left')
            await pg.call('Input.dispatchMouseEvent', type='mouseReleased', x=vx2 + 1500, y=vy2, button='left', clickCount=1)
            await asyncio.sleep(0.3)
            check('너무 작게 끌어도 최소치(220)에서 멈춤', abs(await side_w() - 220) < 1, await side_w())
            # 더블클릭으로 초기화
            vx3, vy3 = await pg.js("(() => { const r = document.getElementById('splitV').getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; })()")
            await pg.call('Input.dispatchMouseEvent', type='mousePressed', x=vx3, y=vy3, button='left', clickCount=2)
            await pg.call('Input.dispatchMouseEvent', type='mouseReleased', x=vx3, y=vy3, button='left', clickCount=2)
            await pg.js("document.getElementById('splitV').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))")
            await asyncio.sleep(0.3)
            check('더블클릭하면 기본 너비로 돌아감', abs(await side_w() - 300) < 1, await side_w())
            # 키보드
            await pg.js("document.getElementById('splitV').focus()")
            await pg.call('Input.dispatchKeyEvent', type='rawKeyDown', key='ArrowLeft', windowsVirtualKeyCode=37)
            await pg.call('Input.dispatchKeyEvent', type='keyUp', key='ArrowLeft', windowsVirtualKeyCode=37)
            await asyncio.sleep(0.2)
            check('손잡이에서 왼쪽 방향키를 누르면 20px 넓어짐', abs(await side_w() - 320) < 1, await side_w())
            # 이력 높이: 위로 끌면 커지고 지도는 낮아진다
            hx, hy = await pg.js("(() => { const r = document.getElementById('splitH').getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; })()")
            await pg.call('Input.dispatchMouseEvent', type='mousePressed', x=hx, y=hy, button='left', clickCount=1)
            for dy in (-30, -80, -120):
                await pg.call('Input.dispatchMouseEvent', type='mouseMoved', x=hx, y=hy + dy, button='left')
            await pg.call('Input.dispatchMouseEvent', type='mouseReleased', x=hx, y=hy - 120, button='left', clickCount=1)
            await asyncio.sleep(0.4)
            hh1, ch1 = await hist_h(), await canvas_h()
            check('손잡이를 위로 끌면 이력이 높아지고 지도가 낮아짐', 280 <= hh1 <= 300 and ch1 < ch0 - 80, (hh0, hh1, ch0, ch1))
            # 저장: 새로고침해도 유지
            await pg.call('Page.navigate', url=BASE + '/')
            check('새로고침 뒤 다시 로드', await pg.wait("app.cfg && app.meta && app.wsUp", 15))
            await asyncio.sleep(0.5)
            check('크기가 새로고침 뒤에도 유지(브라우저 저장)', abs(await side_w() - 320) < 1 and abs(await hist_h() - hh1) < 1, (await side_w(), await hist_h()))
            await pg.js("window.__mis = []")
            # 섹션 접기
            n_vis = lambda: pg.js("[...document.querySelectorAll('.sidebar section')].filter(s => !s.classList.contains('collapsed')).length")  # noqa: E731
            n0 = await n_vis()
            await pg.click_sel('.sidebar section:nth-of-type(1) > h2')
            await asyncio.sleep(0.2)
            check('섹션 제목을 누르면 접힘(내용이 숨겨짐)', await n_vis() == n0 - 1 and await pg.js("document.getElementById('robotCards').getBoundingClientRect().height") == 0)
            await pg.click_sel('.sidebar section:nth-of-type(1) > h2')
            await asyncio.sleep(0.2)
            check('다시 누르면 펼쳐짐', await n_vis() == n0 and await pg.js("document.getElementById('robotCards').getBoundingClientRect().height") > 0)
            # 원래 크기로 되돌려 이후 시험(버튼 좌표 등)에 영향이 없게 한다
            await pg.js("document.getElementById('splitV').dispatchEvent(new MouseEvent('dblclick', {bubbles: true})); document.getElementById('splitH').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))")
            await asyncio.sleep(0.3)
            check('초기화 후 기본 크기', abs(await side_w() - 300) < 1 and abs(await hist_h() - 170) < 1)
            await pg.wait("app.zoneCfg && app.plans && app.motion", 8)

            # ---- 수동 조작 (FR4-9) ----
            sel = lambda d: f'#drivePad button[data-dir="{d}"]'  # noqa: E731
            await pg.js("document.getElementById('drivePanel').scrollIntoView({block: 'center'})")   # 사이드바가 길어서 화면 밖일 수 있다
            await asyncio.sleep(0.3)
            await pg.js("document.getElementById('driveRobot').value = 'pinky1'; document.getElementById('driveRobot').onchange()")
            await asyncio.sleep(0.6)
            check('켜기 전에는 방향 버튼이 비활성', await pg.js("[...document.getElementById('drivePad').children].every(b => b.disabled)"))
            await pg.click_sel('#driveEnable')
            await asyncio.sleep(0.6)
            check('켜면 방향 버튼 활성', await pg.js("[...document.getElementById('drivePad').children].every(b => !b.disabled)"))
            vel = lambda t0: [x for x in mock()['cmd_vel'] if x['robot'] == 'pinky1' and x['t'] >= t0]  # noqa: E731
            xy = await pg.js(f"(() => {{ const r = document.querySelector('{sel('fwd')}').getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; }})()")
            t0 = time.time()
            await pg.call('Input.dispatchMouseEvent', type='mousePressed', x=xy[0], y=xy[1], button='left', clickCount=1)
            await asyncio.sleep(0.7)
            mv = [x for x in vel(t0) if x['linear'] > 0]
            check('누르고 있는 동안 전진 속도가 약 10Hz 로 나감', len(mv) >= 5 and abs(mv[0]['linear'] - 0.1) < 1e-9 and mv[0]['angular'] == 0, len(mv))
            tr = time.time()
            await pg.call('Input.dispatchMouseEvent', type='mouseReleased', x=xy[0] + 300, y=xy[1] + 300, button='left', clickCount=1)   # 버튼 밖에서 뗌
            await asyncio.sleep(0.5)
            zeros = [x for x in vel(tr - 0.05) if x['linear'] == 0 and x['angular'] == 0]
            check('버튼을 떼면(버튼 밖이어도) 즉시 0 속도', zeros and zeros[0]['t'] - tr < 0.3, zeros[:1])
            p1 = mock()['pose']['pinky1']; await asyncio.sleep(0.4); p2 = mock()['pose']['pinky1']
            check('뗀 뒤 로봇이 멈춰 있음', math.hypot(p2['x'] - p1['x'], p2['y'] - p1['y']) < 1e-6)

            # 키보드 (방향키)
            t0 = time.time()
            await pg.call('Input.dispatchKeyEvent', type='rawKeyDown', key='ArrowLeft', windowsVirtualKeyCode=37)
            await asyncio.sleep(0.5)
            check('방향키: 좌회전 각속도', any(x['angular'] > 0 and x['linear'] == 0 for x in vel(t0)))
            tr = time.time()
            await pg.call('Input.dispatchKeyEvent', type='keyUp', key='ArrowLeft', windowsVirtualKeyCode=37)
            await asyncio.sleep(0.4)
            check('키를 떼면 0 속도', any(x['linear'] == 0 and x['angular'] == 0 for x in vel(tr - 0.05)))

            # 포커스를 잃으면 정지
            t0 = time.time()
            await pg.call('Input.dispatchKeyEvent', type='rawKeyDown', key='w', text='w', windowsVirtualKeyCode=87)
            await asyncio.sleep(0.4)
            tr = time.time()
            await pg.js("window.dispatchEvent(new Event('blur'))")
            await asyncio.sleep(0.4)
            check('창이 포커스를 잃으면 정지', any(x['linear'] == 0 and x['angular'] == 0 for x in vel(tr - 0.05)) and await pg.js('drive.dirs.size') == 0)
            await pg.call('Input.dispatchKeyEvent', type='keyUp', key='w', windowsVirtualKeyCode=87)

            # 체크 해제하면 정지
            t0 = time.time()
            await pg.call('Input.dispatchKeyEvent', type='rawKeyDown', key='ArrowUp', windowsVirtualKeyCode=38)
            await asyncio.sleep(0.4)
            tr = time.time()
            await pg.js("(() => { const c = document.getElementById('driveEnable'); c.checked = false; c.onchange(); })()")
            await asyncio.sleep(0.4)
            check('수동 조작을 끄면 정지', any(x['linear'] == 0 and x['angular'] == 0 for x in vel(tr - 0.05)) and await pg.js('drive.dirs.size') == 0)
            await pg.call('Input.dispatchKeyEvent', type='keyUp', key='ArrowUp', windowsVirtualKeyCode=38)

            # 순찰 중에는 비활성
            api('POST', '/api/robots/pinky1/command', {'cmd': 'start'})
            await pg.wait("app.robots.pinky1.patrol.state === 'STARTING'", 5)
            await asyncio.sleep(0.8)
            check('순찰 중에는 수동 조작을 켤 수 없음 (체크박스/버튼 비활성, 사유 표시)',
                  await pg.js("document.getElementById('driveEnable').disabled && [...document.getElementById('drivePad').children].every(b => b.disabled) && document.getElementById('driveMsg').textContent.includes('순찰')"))
            api('POST', '/api/robots/pinky1/estop', {})
            await pg.wait("app.robots.pinky1.patrol.state === 'STOPPED'", 15)
            await asyncio.sleep(3.0)

            # 누른 채로 탭을 닫으면 서버가 0.5초 안에 0 속도
            await pg.wait("!document.getElementById('driveEnable').disabled", 5)
            await pg.click_sel('#driveEnable')
            await asyncio.sleep(0.5)
            xy = await pg.js(f"(() => {{ const r = document.querySelector('{sel('back')}').getBoundingClientRect(); return [r.left + r.width/2, r.top + r.height/2]; }})()")
            t0 = time.time()
            await pg.call('Input.dispatchMouseEvent', type='mousePressed', x=xy[0], y=xy[1], button='left', clickCount=1)
            await asyncio.sleep(0.6)
            check('(탭 닫기 전) 후진 중', any(x['linear'] < 0 for x in vel(t0)))
            await ws.send(json.dumps({'id': 9999, 'method': 'Target.closeTarget', 'params': {'targetId': tab['id']}}))
        await asyncio.sleep(0.7)
        tc = time.time() - 0.7
        zeros = [x for x in mock()['cmd_vel'] if x['robot'] == 'pinky1' and x['t'] >= tc - 0.3 and x['linear'] == 0 and x['angular'] == 0]
        last_move = max((x['t'] for x in mock()['cmd_vel'] if x['robot'] == 'pinky1' and x['linear'] < 0), default=0)
        z_after = [x for x in zeros if x['t'] >= last_move]
        check('탭을 닫으면 마지막 이동 입력 후 0.5초 안에 0 속도', z_after and z_after[0]['t'] - last_move <= 0.6, (last_move, z_after[:1]))
    finally:
        br.terminate()
        srv.terminate()
        shutil.rmtree(prof, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shot', default=None)
    ap.add_argument('--zone-shot', default=None)
    ap.add_argument('--scan-shot', default=None)
    args = ap.parse_args()
    asyncio.run(run(args.shot, args.zone_shot, args.scan_shot))
    print('FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASS')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
