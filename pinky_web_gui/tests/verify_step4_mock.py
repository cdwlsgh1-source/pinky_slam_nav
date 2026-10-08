"""Step 4 API/WS 를 --mock 서버에 직접 붙어 확인한다 (urllib + websockets). 서버는 이 스크립트가 띄우고 정리한다.

실행: .venv/bin/python tests/verify_step4_mock.py
확인: 구역 상태(v1/v2), LiDAR 구독 수 증감과 해제, 스캔 점이 지도의 벽 위에 떨어지는지, 비상정지(stop + 0 속도 burst),
      수동 조작 데드맨(입력 끊김/연결 끊김/순찰 중/비상정지 중), 다른 출처의 요청 거절.
"""
import asyncio
import json
import math
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.config import load_config  # noqa: E402
from backend.map_loader import load_grid  # noqa: E402

PORT = 8015
BASE = f'http://127.0.0.1:{PORT}'
WS = f'ws://127.0.0.1:{PORT}/ws'
FAILS = []


def check(name, cond, extra=''):
    print(('PASS ' if cond else 'FAIL ') + name + (f'  {extra}' if extra and not cond else ''))
    if not cond:
        FAILS.append(name)


def http(method, path, body=None, headers=None):
    h = {'Content-Type': 'application/json'}
    h.update(headers or {})
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


get = lambda path: http('GET', path)[1]            # noqa: E731
health = lambda: get('/api/health')                # noqa: E731
mock_log = lambda: get('/api/mock/cmd_vel')        # noqa: E731


async def drain(ws, sec):
    """sec 초 동안 받은 메시지를 모두 돌려준다"""
    out, end = [], time.monotonic() + sec
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return out
        try:
            out.append(json.loads(await asyncio.wait_for(ws.recv(), left)))
        except asyncio.TimeoutError:
            return out


async def until(ws, pred, sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), max(0.01, end - time.monotonic())))
        except asyncio.TimeoutError:
            return None
        if pred(m):
            return m
    return None


def near_wall_ratio(points, meta, pixels, radius_px=3):
    w, h, res, (ox, oy) = meta['width'], meta['height'], meta['resolution'], meta['origin'][:2]
    occ = lambda c, r: 0 <= c < w and 0 <= r < h and (255 - pixels[r * w + c]) / 255 > meta['occupied_thresh']  # noqa: E731
    good = 0
    for x, y in points:
        c, r = math.floor((x - ox) / res), math.floor(h - (y - oy) / res)
        if any(occ(c + dc, r + dr) for dc in range(-radius_px, radius_px + 1) for dr in range(-radius_px, radius_px + 1)):
            good += 1
    return good / len(points) if points else 0.0


async def mounted_scan(extra_args, config_text=None):
    """센서가 180도 돌아 달린 mock 서버를 따로 띄워 켠 LiDAR 의 첫 스캔 메시지와 벽 일치율을 돌려준다."""
    import tempfile
    port = PORT + 10
    args = [sys.executable, '-m', 'backend.main', '--mock', '--port', str(port), '--mock-scan-mount-deg', '180'] + extra_args
    tmp = None
    if config_text is not None:
        tmp = Path(tempfile.mkdtemp())
        src = ROOT / 'config'
        for f in ('points.yaml', 'zone.yaml'):
            (tmp / f).write_text((src / f).read_text(encoding='utf-8'), encoding='utf-8')
        raw = (src / 'robots.yaml').read_text(encoding='utf-8').replace('../../map_view_pc', str(ROOT.parent / 'map_view_pc'))
        (tmp / 'robots.yaml').write_text(raw.replace('yaw_offset_deg: {}', config_text), encoding='utf-8')
        args += ['--config', str(tmp / 'robots.yaml')]
    srv = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health')
                break
            except Exception:
                time.sleep(0.1)
        cfg = load_config()
        meta, pixels = load_grid(cfg.map_yaml)
        async with websockets.connect(f'ws://127.0.0.1:{port}/ws') as ws:
            await ws.recv()
            await ws.send(json.dumps({'type': 'scan', 'robot': 'pinky1', 'on': True}))
            sc = await until(ws, lambda x: x['type'] == 'scan', 4)
        return sc, (near_wall_ratio(sc['points'], meta, pixels) if sc else 0.0)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()


async def main():
    cfg = load_config()
    meta, pixels = load_grid(cfg.map_yaml)
    srv = subprocess.Popen([sys.executable, '-m', 'backend.main', '--mock', '--mock-wait-scale', '0.1', '--port', str(PORT)],
                           cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(BASE + '/api/health')
                break
            except Exception:
                time.sleep(0.1)

        # ---- 설정 API ----
        z = get('/api/zone/config')
        check('구역 사각형과 문 설정', z['rect']['x_min'] < z['rect']['x_max'] and z['doors']['pinky2']['in'] == 'RED2IN' and z['confirmed'] is False)
        m = get('/api/motion/config')
        check('모션 설정(상한)', m['manual']['max_linear'] <= 0.2 and m['scan']['decimate'] == 3 and m['estop_burst_sec'] == 2.0)
        snap_zone = get('/api/state')
        check('snapshot 에 zone_info', 'zone_info' in snap_zone)

        # ---- 구역 상태는 WS 로 해석값이 간다 ----
        async with websockets.connect(WS) as ws0:
            first = json.loads(await ws0.recv())
            check('snapshot 의 zone_info 형식', first['type'] == 'snapshot' and first['zone_info']['state'] in ('free', 'occupied', 'unknown'))

        # ---- LiDAR: 구독 수 증감 ----
        check('처음에는 아무도 구독하지 않음', all(v == 0 for v in health()['scan_clients'].values()) and health()['scan_subscribed'] == [])
        async with websockets.connect(WS) as a, websockets.connect(WS) as b, websockets.connect(WS) as c:
            for w in (a, b, c):
                await w.recv()
            await a.send(json.dumps({'type': 'scan', 'robot': 'pinky1', 'on': True}))
            sc = await until(a, lambda x: x['type'] == 'scan', 3)
            check('켜면 스캔이 온다', sc is not None and len(sc['points']) > 10, sc and len(sc['points']))
            check('켠 로봇만 서버가 구독', health()['scan_subscribed'] == ['pinky1'] and health()['scan_clients']['pinky1'] == 1)
            if sc:
                ratio = near_wall_ratio(sc['points'], meta, pixels)
                check('스캔 점이 지도의 벽 위에 떨어짐 (>=95%)', ratio >= 0.95, ratio)
                check('스캔에 pose 가 실림', set(sc['pose']) == {'x', 'y', 'yaw'})
            got = [x for x in await drain(a, 2.0) if x['type'] == 'scan']
            check('빈도가 5Hz 이하로 제한', 5 <= len(got) <= 12, len(got))
            check('켜지 않은 화면에는 스캔이 안 간다', not [x for x in await drain(c, 0.6) if x['type'] == 'scan'])
            await b.send(json.dumps({'type': 'scan', 'robot': 'pinky1', 'on': True}))
            await asyncio.sleep(0.3)
            check('두 화면이 켜면 구독자 2', health()['scan_clients']['pinky1'] == 2)
            await a.send(json.dumps({'type': 'scan', 'robot': 'pinky1', 'on': False}))
            await asyncio.sleep(0.3)
            check('한 화면이 꺼도 다른 화면이 켜져 있으면 구독 유지', health()['scan_subscribed'] == ['pinky1'] and health()['scan_clients']['pinky1'] == 1)
            await drain(a, 0.4)
            check('끈 화면에는 더 안 온다', not [x for x in await drain(a, 0.8) if x['type'] == 'scan'])
            check('남은 화면에는 계속 온다', any(x['type'] == 'scan' for x in await drain(b, 1.0)))
            b.transport.abort()                                    # 마지막 구독자가 갑자기 사라짐
            for _ in range(20):
                if health()['scan_subscribed'] == []:
                    break
                await asyncio.sleep(0.1)
            check('마지막 구독자가 끊기면 /scan 구독 해제', health()['scan_subscribed'] == [] and health()['scan_clients']['pinky1'] == 0)
            await a.send(json.dumps({'type': 'scan', 'robot': 'ghost', 'on': True}))
            await a.send('not json')
            await a.send(json.dumps([1, 2]))
            await asyncio.sleep(0.2)
            check('이상한 WS 메시지는 무시', health()['ok'] and health()['scan_subscribed'] == [])

        # ---- 센서가 로봇 정면에서 180도 돌아 달린 경우 (LiDAR 방향) ----
        sc, ratio = await mounted_scan([])
        check('센서가 180도 돌아 있어도 tf 로 그리면 점이 벽 위에 떨어짐 (>=95%)', sc is not None and sc['source'] == 'tf' and ratio >= 0.95, (sc and sc['source'], ratio))
        check('tf 로 그릴 때 메시지에 센서 프레임이 실림', sc is not None and sc['frame'] == 'mock_laser' and sc['offset_deg'] is None)
        sc, ratio = await mounted_scan(['--mock-scan-no-tf'])
        check('(대조) tf 도 보정도 없으면 방향이 어긋나 벽과 거의 안 맞음 (<50%)', sc is not None and sc['source'] == 'amcl' and ratio < 0.5, (sc and sc['source'], ratio))
        sc, ratio = await mounted_scan(['--mock-scan-no-tf'], 'yaw_offset_deg: {pinky1: 180}')
        check('tf 가 없어도 설정의 보정 각도(180)를 주면 벽 위에 떨어짐 (>=95%)', sc is not None and sc['source'] == 'amcl' and sc['offset_deg'] == 180.0 and ratio >= 0.95, (sc and (sc['source'], sc['offset_deg']), ratio))

        # ---- 출처 검사 ----
        s, _ = http('POST', '/api/robots/pinky1/estop', {}, {'Origin': 'http://evil.example'})
        check('다른 출처의 POST -> 403', s == 403, s)
        s, _ = http('POST', '/api/robots/pinky1/command', b'{"cmd":"stop"}', {'Content-Type': 'text/plain'})
        check('text/plain POST -> 415 (단순 요청 거절)', s == 415, s)
        s, _ = http('POST', '/api/estop', b'{}', {'Content-Type': 'text/plain'})
        check('estop 도 text/plain 거절', s == 415, s)
        try:
            async with websockets.connect(WS, additional_headers={'Origin': 'http://evil.example'}) as w:
                await w.recv()
            check('다른 출처의 WS 거절', False)
        except Exception as e:
            check('다른 출처의 WS 거절', '403' in str(e) or 'rejected' in str(e).lower() or '1008' in str(e), e)
        async with websockets.connect(WS, additional_headers={'Origin': f'http://127.0.0.1:{PORT}'}) as w:
            check('같은 출처 WS 는 허용', json.loads(await w.recv())['type'] == 'snapshot')
        s, _ = http('POST', '/api/robots/nobody/estop', {})
        check('알 수 없는 로봇 estop -> 404', s == 404)

        # ---- 수동 조작 ----
        t_before = time.time()
        async with websockets.connect(WS) as d:
            await d.recv()
            for _ in range(8):                                     # 10Hz 로 0.8초, 상한(0.1)보다 큰 값을 요청
                await d.send(json.dumps({'type': 'drive', 'robot': 'pinky1', 'linear': 5.0, 'angular': 0.0}))
                await asyncio.sleep(0.1)
            log = [x for x in mock_log()['cmd_vel'] if x['robot'] == 'pinky1' and x['t'] >= t_before]
            moving = [x for x in log if x['linear'] != 0]
            check('조작 입력이 cmd_vel 로 나간다', len(moving) >= 5, len(log))
            check('상한으로 잘림 (요청 5.0 -> 설정 최대)', all(abs(x['linear']) <= m['manual']['max_linear'] + 1e-9 for x in moving) and moving[0]['linear'] == m['manual']['max_linear'])
            pose_a = mock_log()['pose']['pinky1']
            await asyncio.sleep(0.3)
            pose_b = mock_log()['pose']['pinky1']
            check('조작 중 mock 로봇이 움직임', math.hypot(pose_b['x'] - pose_a['x'], pose_b['y'] - pose_a['y']) > 0.005)
            # 입력이 끊기면(연결은 유지) 0.5초 안에 0 속도
            t_last = time.time()
            await asyncio.sleep(0.9)
            log = [x for x in mock_log()['cmd_vel'] if x['robot'] == 'pinky1' and x['t'] >= t_last - 0.15]
            zeros = [x for x in log if x['linear'] == 0 and x['angular'] == 0]
            check('입력이 끊기면 0.5초 안에 0 속도', zeros and zeros[0]['t'] - t_last <= 0.75, zeros[:1])
            p1 = mock_log()['pose']['pinky1']; await asyncio.sleep(0.4); p2 = mock_log()['pose']['pinky1']
            check('입력 끊김 뒤 mock 로봇이 멈춰 있음', math.hypot(p2['x'] - p1['x'], p2['y'] - p1['y']) < 1e-6)

        # 연결을 갑자기 끊는 경우 (탭 닫기/네트워크 끊김)
        async with websockets.connect(WS) as d:
            await d.recv()
            for _ in range(4):
                await d.send(json.dumps({'type': 'drive', 'robot': 'pinky1', 'linear': 0.05, 'angular': 0.3}))
                await asyncio.sleep(0.1)
            t_cut = time.time()
            d.transport.abort()
        await asyncio.sleep(0.4)
        zeros = [x for x in mock_log()['cmd_vel'] if x['robot'] == 'pinky1' and x['t'] >= t_cut - 0.01 and x['linear'] == 0 and x['angular'] == 0]
        check('연결이 끊기면 0.5초 안에 0 속도', zeros and zeros[0]['t'] - t_cut <= 0.5, zeros[:1])
        p1 = mock_log()['pose']['pinky1']; await asyncio.sleep(0.4); p2 = mock_log()['pose']['pinky1']
        check('연결 끊김 뒤 mock 로봇이 멈춰 있음', math.hypot(p2['x'] - p1['x'], p2['y'] - p1['y']) < 1e-6 and abs(p2['z'] - p1['z']) < 1e-6)

        # 순찰 중에는 거절
        status, _ = http('POST', '/api/robots/pinky2/command', {'cmd': 'start'})
        await asyncio.sleep(0.5)
        async with websockets.connect(WS) as d:
            await d.recv()
            await d.send(json.dumps({'type': 'drive', 'robot': 'pinky2', 'linear': 0.05, 'angular': 0}))
            den = await until(d, lambda x: x['type'] == 'drive_denied', 2)
            check('순찰 중에는 수동 조작 거절', status == 200 and den and '순찰' in den['reason'], den)
            await d.send(json.dumps({'type': 'drive', 'robot': 'pinky2', 'linear': 0.05, 'angular': 0}))
            check('같은 거절을 되풀이해 보내지 않음', not [x for x in await drain(d, 0.5) if x['type'] == 'drive_denied'])
        http('POST', '/api/robots/pinky2/estop', {})              # 정리: pinky2 순찰 중지
        await asyncio.sleep(0.5)

        # ---- 비상정지 ----
        t0 = time.time()
        s, body = http('POST', '/api/robots/pinky1/estop', {})
        check('비상정지 200 (stop, cmd_vel 모두 전송)', s == 200 and body['patrol_stop'] and body['cmd_vel'] and body['errors'] == [], (s, body))
        await asyncio.sleep(2.6)
        log = mock_log()
        zeros = [x for x in log['cmd_vel'] if x['robot'] == 'pinky1' and x['t'] >= t0 - 0.01]
        check('0 속도 burst 약 20회 (10Hz x 2초)', 19 <= len(zeros) <= 23 and all(x['linear'] == 0 and x['angular'] == 0 for x in zeros), len(zeros))
        check('burst 길이가 약 2초', 1.8 <= zeros[-1]['t'] - zeros[0]['t'] <= 2.3, zeros[-1]['t'] - zeros[0]['t'])
        check('patrol_cmd 로 stop 이 나감', any(x['robot'] == 'pinky1' and x['data'] == 'stop' and x['t'] >= t0 - 0.01 for x in log['patrol_cmd']))
        # 비상정지 중 수동 조작은 거절, burst 가 끝나면 다시 가능
        s, body = http('POST', '/api/estop', {})
        check('모두 정지: 로봇마다 결과', s == 200 and {r['robot'] for r in body['results']} == {'pinky1', 'pinky2'}, body)
        async with websockets.connect(WS) as d:
            await d.recv()
            await d.send(json.dumps({'type': 'drive', 'robot': 'pinky1', 'linear': 0.05, 'angular': 0}))
            den = await until(d, lambda x: x['type'] == 'drive_denied', 2)
            check('비상정지 중에는 수동 조작 거절', den and '비상정지' in den['reason'], den)
        hist = get('/api/state')['commands']
        es = [e for e in hist if e['cmd'] == 'estop']
        check('이력에 비상정지 기록', len(es) >= 3 and es[0]['points'] == [] and 'cmd_vel' in es[0]['sent'], es[:1])
        await asyncio.sleep(5.3)                                  # 마지막 비상정지로부터 5초 응답 대기 시간이 지남
        es = [e for e in get('/api/state')['commands'] if e['cmd'] == 'estop' and e['robot'] == 'pinky1']
        check('멈춰 있던 로봇의 비상정지는 응답 없음이 아니라 설명이 붙은 전송됨',
              es and es[-1]['result'] == 'sent' and '이미 멈춰' in es[-1]['detail'] and all(e['result'] != 'no_response' for e in es), es[-2:])
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except subprocess.TimeoutExpired:
            srv.kill()
    print('ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
