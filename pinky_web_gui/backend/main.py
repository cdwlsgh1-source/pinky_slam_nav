"""FastAPI 백엔드. 실행: pinky_web_gui/ 에서 `python -m backend.main [--mock] [--host H] [--port P]`."""
import argparse
import asyncio
import contextlib
import itertools
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .auth import COOKIE, OPERATOR, SESSION_SEC, VIEWER, Auth
from .commands import CommandRejected, CommandTracker, CommandUnavailable, FAILED, validate
from .config import Config, load_config
from .hub import Hub
from .logstore import GUI, LogStore, StoreHandler
from .map_loader import load_grid, load_map
from .motion import Motion
from .procs import ProcError, ProcessManager, SystemRunner
from .planner import GridPlanner, NoPath, path_length
import math

from .scan import to_world, to_world_tf, yaw_of
from .state import StateStore

log = logging.getLogger('backend')

STATIC = Path(__file__).resolve().parent / 'static'
FRONTEND = Path(__file__).resolve().parent.parent / 'frontend'
TICK_SEC = 0.1  # online 타임아웃 판정과 보류된 pose 전송 주기
MOTION_TICK_SEC = 0.05  # 비상정지 burst, 수동 조작 반복/타임아웃 판정 주기
SHUTDOWN_FLUSH_SEC = 0.3  # 종료할 때 마지막 0 속도가 전달될 시간
MAX_WS_TEXT = 4096      # 브라우저가 보내는 제어 메시지의 최대 길이
LOOPBACK = ('127.0.0.1', 'localhost', '::1')


def same_origin(headers) -> bool:
    """브라우저가 보낸 Origin 이 이 서버(Host)와 같은지. 다른 사이트의 페이지가 localhost 의 로봇 제어 API 를 부르는 것을 막는다.

    curl, 테스트 클라이언트처럼 Origin 이 없는 요청은 통과시킨다 (브라우저는 POST 와 WebSocket 에 항상 Origin 을 붙인다).
    """
    origin = headers.get('origin')
    if origin is None:
        return True
    return urlparse(origin).netloc == headers.get('host')


PROC_STATUS = {'unknown': 404, 'unset': 409, 'busy': 409, 'external': 409, 'confirm': 409, 'fail': 502}
PUBLIC_API = ('/api/health', '/api/login', '/api/logout', '/api/me')  # 로그인 없이 부를 수 있는 API


def create_app(cfg: Config, mock: bool, mock_opts: dict = None, auth: Auth = None, proc_runner=None) -> FastAPI:
    auth = auth if auth is not None else Auth.from_env(os.environ)
    store = StateStore(cfg.robots, cfg.zone_status_topic, cfg.online_timeout_sec, cfg.pose_max_hz)
    tracker = CommandTracker(cfg.robots, cfg.command_history_size, cfg.no_response_sec)
    hub = Hub()
    bg = {}  # lifespan 에서 만든 리소스 (commander: publish_cmd(rid, data) 를 가진 RosBridge 또는 MockFleet)
    client_ids = itertools.count(1)

    def _publish_twist(rid, linear, angular):
        commander = bg.get('commander')
        if commander is None:
            raise CommandUnavailable('서버가 아직 준비되지 않았습니다')
        return commander.publish_twist(rid, linear, angular)

    bg['procs_dirty'] = False

    def procs_changed():
        bg['procs_dirty'] = True  # ticker 가 모아서 한 번에 보낸다 (로그가 많이 나와도 WS 가 넘치지 않게)

    if proc_runner is None and mock:
        from .mock_runner import MockRunner
        proc_runner = MockRunner(**(mock_opts or {}).get('proc_opts', {}))
    logs = LogStore()
    procs = ProcessManager(cfg.processes, proc_runner or SystemRunner(), lambda robot: store.is_online(robot), procs_changed, log_sink=logs.add)

    def procs_message():
        return {'type': 'procs', 'enabled': auth.procs_allowed, **procs.status()}

    motion = Motion(cfg.robots, cfg.motion, _publish_twist, store.patrol_state, store.is_online)

    def sync_scans():
        """LiDAR 를 켠 브라우저가 있는 로봇만 /scan 을 구독한다. 아무도 안 켜면 구독을 해제한다 (FR4-5)."""
        commander = bg.get('commander')
        if commander is None:
            return
        want, have = hub.scan_robots(), set(commander.scan_subscribed())
        for rid in sorted(want - have):
            try:
                commander.scan_enable(rid, True)
            except Exception as e:
                log.error('%s LiDAR 구독 실패: %s', rid, e)
        for rid in sorted(have - want):
            try:
                commander.scan_enable(rid, False)
            except Exception as e:
                log.error('%s LiDAR 구독 해제 실패: %s', rid, e)

    def full_snapshot():
        """WS 접속 시와 /api/state 가 쓰는 전체 상태. 상태 저장소 + 명령 이력과 로봇별 마지막 명령."""
        snap = store.snapshot()
        for rid, r in snap['robots'].items():
            r['last_command'] = tracker.last_command(rid)
            r['last_task'] = tracker.last_task(rid)
        snap['commands'] = tracker.history()
        snap['procs'] = procs_message()
        return snap

    # 지도는 시작할 때 한 번 읽는다. 실패해도 서버는 뜨고 /api/map 만 503 을 돌려준다.
    map_meta = map_png = None
    planner = None
    plans = {}  # 'A>B' -> {path, length} (첫 요청에서 계산해 두 번째부터는 캐시)
    if cfg.map_yaml:
        try:
            map_meta, map_png = load_map(cfg.map_yaml)
            planner = GridPlanner(*load_grid(cfg.map_yaml), inflation_m=cfg.planner_inflation_m)
            log.info('지도 로드: %s (%dx%d, %.3f m/px)', cfg.map_yaml,
                     map_meta['width'], map_meta['height'], map_meta['resolution'])
        except Exception:
            log.exception('지도 로드 실패: %s', cfg.map_yaml)

    def scan_message(rid, event):
        """('scan', rid, tf|None, frame, angle_min, angle_inc, ranges, range_min, range_max) -> 켠 화면에 보낼 메시지.

        tf(센서 프레임 -> map) 가 있으면 그것으로 그리고(source 'tf'), 없으면 amcl_pose + 설정의 보정 각도로 그린다(source 'amcl').
        """
        tf, frame, scan = event[2], event[3], event[4:]
        if tf is not None:
            pts = to_world_tf(tf, *scan)
            R, t = tf
            return {'type': 'scan', 'robot': rid, 'source': 'tf', 'frame': frame, 'offset_deg': None,
                    'pose': {'x': round(t[0], 3), 'y': round(t[1], 3), 'yaw': round(yaw_of(R), 4)}, 'points': pts}
        pose = store.pose(rid)
        if not pose:
            return None
        off = cfg.motion.scan_yaw_offset_deg.get(rid, 0.0)
        return {'type': 'scan', 'robot': rid, 'source': 'amcl', 'frame': frame, 'offset_deg': off, 'pose': pose,
                'points': to_world(pose, *scan, yaw_offset=math.radians(off))}

    async def consume(queue):
        """큐에서 이벤트를 꺼내 상태에 반영하고 변경분을 브로드캐스트한다 (상태를 바꾸는 유일한 곳)."""
        while True:
            event = await queue.get()
            try:
                if event[0] == 'scan':  # 스캔은 켠 브라우저에만 보낸다 (상태가 아니라 흘려보내는 데이터)
                    store.apply(event)
                    rid = event[1]
                    if hub.scan_count(rid):
                        msg = scan_message(rid, event)
                        if msg:
                            hub.send_scan(rid, msg)
                    continue
                msgs = store.apply(event)
                hub.broadcast(msgs)
                # 로봇이 명령에 맞는 상태(start/goto -> STARTING, stop -> STOPPED)를 내면 응답한 것이다
                for m in msgs:
                    if m['type'] == 'robot_update' and m['field'] == 'patrol':
                        hub.broadcast(tracker.on_patrol(m['robot'], (m['data'] or {}).get('state')))
            except Exception:
                log.exception('이벤트 처리 실패(무시하고 계속): %r', event)

    async def ticker():
        while True:
            await asyncio.sleep(TICK_SEC)
            try:
                hub.broadcast(store.tick())
                hub.broadcast(tracker.tick())
                if bg['procs_dirty']:
                    bg['procs_dirty'] = False
                    hub.broadcast([procs_message()])
            except Exception:
                log.exception('tick 실패(무시하고 계속)')

    async def motion_ticker():
        while True:
            await asyncio.sleep(MOTION_TICK_SEC)
            try:
                motion.tick()
            except Exception:
                log.exception('motion tick 실패(무시하고 계속)')

    @contextlib.asynccontextmanager
    async def lifespan(app):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        tasks = [asyncio.create_task(consume(queue)), asyncio.create_task(ticker()),
                 asyncio.create_task(motion_ticker())]
        ros = None
        if mock:
            from .mock import MockFleet
            fleet = MockFleet(cfg, queue, planner=planner, **{k: v for k, v in (mock_opts or {}).items() if k != 'proc_opts'})
            bg['commander'] = fleet
            tasks.append(asyncio.create_task(fleet.run()))
            log.info('mock 모드: ROS 를 사용하지 않는다')
        else:
            from .ros_bridge import RosBridge  # rclpy 는 여기서만 import
            ros = RosBridge(cfg, loop, queue)
            ros.start()
            bg['commander'] = ros
        bg['mock'] = mock
        log_handler = StoreHandler(logs)   # GUI 서버 로그를 로그 화면으로 모은다 (서버가 도는 동안만)
        logging.getLogger('backend').addHandler(log_handler)
        try:
            yield
        finally:
            motion.shutdown()  # 비상정지 중이거나 수동 조작 중이던 로봇에 마지막 0 속도. 브릿지를 끄기 전에 먼저 보낸다
            if ros:
                # publish() 는 DDS 가 따로 내보내므로, 곧바로 rclpy 를 종료하면 마지막 0 속도가 나가기 전에 사라진다 (격리 도메인에서 확인)
                await asyncio.sleep(SHUTDOWN_FLUSH_SEC)
            await procs.shutdown()  # 이 GUI 가 켠 프로세스(브릿지, zone_manager, 로봇의 bringup/map)를 모두 정지한다
            if ros:
                ros.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logging.getLogger('backend').removeHandler(log_handler)

    app = FastAPI(title='Pinky web GUI backend', lifespan=lifespan)

    def role_of(request):
        return auth.role_of(request.cookies.get(COOKIE))

    def require(request, need):
        """권한이 부족하면 거절 응답을, 충분하면 None 을 돌려준다. 인증이 꺼져 있으면 항상 통과 (기존 동작)."""
        role = role_of(request)
        if role is None:
            return JSONResponse({'accepted': False, 'error': '로그인이 필요합니다'}, status_code=401)
        if not Auth.allows(role, need):
            return JSONResponse({'accepted': False, 'error': '보기 전용 계정은 사용할 수 없습니다 (operator 로 로그인하세요)'}, status_code=403)
        return None

    @app.middleware('http')
    async def api_login_gate(request: Request, call_next):
        """인증이 켜져 있으면 /api/ 는 로그인해야 쓸 수 있다 (정적 화면 파일은 로그인 화면을 보여줘야 하므로 열어 둔다)."""
        path = request.url.path
        if auth.enabled and path.startswith('/api/') and path not in PUBLIC_API and role_of(request) is None:
            return JSONResponse({'error': '로그인이 필요합니다'}, status_code=401)
        return await call_next(request)

    @app.get('/api/me')
    async def me(request: Request):
        return {'auth': auth.enabled, 'role': role_of(request), 'procs_allowed': auth.procs_allowed,
                'procs_configured': bool(cfg.processes)}

    @app.post('/api/login')
    async def login(request: Request):
        denied = guard_post(request)
        if denied:
            return denied
        try:
            body = await request.json()
        except Exception:
            body = None
        who = request.client.host if request.client else '-'
        if not auth.enabled:
            return JSONResponse({'ok': False, 'error': '인증이 설정돼 있지 않습니다'}, status_code=400)
        token, role = auth.login(body.get('password') if isinstance(body, dict) else None, who)
        if token is None:
            if role == 'locked':
                log.warning('로그인 시도 제한: %s', who)
                return JSONResponse({'ok': False, 'error': '실패가 너무 많습니다. 잠시 후 다시 시도하세요'}, status_code=429)
            log.warning('로그인 실패: %s', who)
            return JSONResponse({'ok': False, 'error': '비밀번호가 맞지 않습니다'}, status_code=401)
        log.info('로그인: %s (%s)', who, role)
        resp = JSONResponse({'ok': True, 'role': role})
        resp.set_cookie(COOKIE, token, max_age=SESSION_SEC, httponly=True, samesite='strict', path='/')
        return resp

    @app.post('/api/logout')
    async def logout(request: Request):
        denied = guard_post(request)
        if denied:
            return denied
        auth.logout(request.cookies.get(COOKIE))
        resp = JSONResponse({'ok': True})
        resp.delete_cookie(COOKIE, path='/')
        return resp

    @app.get('/api/health')
    async def health():
        commander = bg.get('commander')
        return {'ok': True, 'mock': mock, 'robots': list(cfg.robots), 'ws_clients': hub.count,
                'scan_clients': {rid: hub.scan_count(rid) for rid in cfg.robots},          # 켠 브라우저 수
                'scan_subscribed': commander.scan_subscribed() if commander else [],       # 실제 ROS /scan 구독 중인 로봇
                'motion': motion.status()}

    @app.get('/api/state')
    async def state():
        return full_snapshot()

    @app.get('/api/commands/config')
    async def commands_config():
        """명령 패널이 쓰는 설정: 로봇별 허용 지점/홈/대기, 지점 좌표, 표시 임계 시간."""
        return {
            'robots': {rid: {'goto_allowed': list(s.goto_allowed), 'home': s.home,
                             'goto_wait_sec': s.goto_wait_sec, 'wait_every_point': s.wait_every_point,
                             'start_route': list(s.start_route)}
                       for rid, s in cfg.robot_settings.items()},
            'points': cfg.points,
            'max_goto_points': cfg.max_goto_points,
            'no_response_sec': cfg.no_response_sec,
            'starting_notice_sec': cfg.starting_notice_sec,
            'history_size': cfg.command_history_size,
        }

    @app.get('/api/zone/config')
    async def zone_config():
        """지도에 그리는 구역 사각형과 로봇별 문. confirmed 가 false 면 화면에 '초안(미확인)' 을 표시한다."""
        z = cfg.zone
        if z is None:
            return JSONResponse({'error': 'zone not configured'}, status_code=503)
        return {'rect': z.rect, 'points': list(z.points), 'margin': z.margin, 'doors': z.doors,
                'confirmed': z.confirmed, 'status_topic': cfg.zone_status_topic}

    @app.get('/api/motion/config')
    async def motion_config():
        """비상정지, 수동 조작, LiDAR 오버레이 설정 (화면의 표시와 전송 주기에 쓴다)."""
        m = cfg.motion
        return {'estop_burst_hz': m.estop_burst_hz, 'estop_burst_sec': m.estop_burst_sec,
                'manual': {'enabled': m.manual_enabled, 'max_linear': m.manual_max_linear,
                           'max_angular': m.manual_max_angular, 'input_timeout_sec': m.manual_timeout_sec,
                           'rate_hz': m.manual_rate_hz},
                'scan': {'max_hz': m.scan_max_hz, 'decimate': m.scan_decimate}}

    def guard_post(request):
        """다른 사이트가 보낸 요청(CSRF)을 막는다: Origin 이 같고 Content-Type 이 application/json 이어야 한다.
        text/plain 같은 '단순 요청' 은 브라우저가 사전 확인(preflight) 없이 보낼 수 있어서, JSON 만 받는다."""
        if not same_origin(request.headers):
            return JSONResponse({'accepted': False, 'error': '허용되지 않은 출처입니다'}, status_code=403)
        if not request.headers.get('content-type', '').lower().startswith('application/json'):
            return JSONResponse({'accepted': False, 'error': 'Content-Type 은 application/json 이어야 합니다'}, status_code=415)
        return None

    def do_estop(rid):
        """비상정지 한 대: ① patrol_cmd 로 stop ② cmd_vel 0 속도 burst. 한쪽이 실패해도 다른 쪽은 반드시 시도한다.

        오프라인이거나 구독자가 없어도 막지 않는다 (정지 명령을 거절하는 일이 없어야 한다). 전체 결과는 이력에 남긴다.
        """
        errors = []
        commander = bg.get('commander')
        patrol_ok = False
        try:
            if commander is None:
                raise CommandUnavailable('서버가 아직 준비되지 않았습니다')
            commander.publish_cmd(rid, 'stop')
            patrol_ok = True
        except CommandUnavailable as e:
            errors.append(f'patrol_cmd stop 전송 실패: {e}')
        vel_ok = motion.estop(rid)
        if not vel_ok:
            errors.append('cmd_vel 0 속도를 받는 구독자가 없습니다 (domain_bridge 가 꺼져 있을 수 있습니다)')
        sent = f'stop + cmd_vel 0 ({cfg.motion.estop_burst_sec:g}초)'
        _, msgs = tracker.estop(rid, sent, patrol_ok, vel_ok, ' · '.join(errors))
        hub.broadcast(msgs)
        (log.warning if errors else log.info)('%s 비상정지: patrol_stop=%s cmd_vel=%s %s', rid, patrol_ok, vel_ok, errors)
        return {'robot': rid, 'patrol_stop': patrol_ok, 'cmd_vel': vel_ok, 'errors': errors}

    @app.post('/api/robots/{robot_id}/estop')
    async def estop_one(robot_id: str, request: Request):
        denied = guard_post(request)
        if denied:
            return denied
        if robot_id not in cfg.robot_settings:
            return JSONResponse({'accepted': False, 'error': f'알 수 없는 로봇입니다: {robot_id}'}, status_code=404)
        r = do_estop(robot_id)
        ok = r['patrol_stop'] or r['cmd_vel']
        return JSONResponse({'accepted': ok, **r}, status_code=200 if ok else 503)

    @app.post('/api/estop')
    async def estop_all(request: Request):
        """모두 정지: 설정된 모든 로봇에 비상정지. 한 대가 실패해도 나머지는 계속한다."""
        denied = guard_post(request)
        if denied:
            return denied
        results = [do_estop(rid) for rid in cfg.robots]
        ok = any(r['patrol_stop'] or r['cmd_vel'] for r in results)
        return JSONResponse({'accepted': ok, 'results': results}, status_code=200 if ok else 503)

    @app.post('/api/robots/{robot_id}/command')
    async def send_command(robot_id: str, request: Request):
        """start / stop / goto 를 검증한 뒤 /{id}/patrol_cmd 로 발행한다. accepted 는 '발행했다' 는 뜻일 뿐이다.

        본문은 직접 파싱해서 모든 입력 오류를 400 으로 통일한다 (허용 목록 밖은 400, 알 수 없는 로봇은 404).
        """
        denied = guard_post(request) or require(request, OPERATOR)
        if denied:
            return denied
        if robot_id not in cfg.robot_settings:
            return JSONResponse({'accepted': False, 'error': f'알 수 없는 로봇입니다: {robot_id}'}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            body = None
        cmd_raw = body.get('cmd') if isinstance(body, dict) else None
        pts_raw = body.get('points') if isinstance(body, dict) else None
        try:
            cmd, points, sent = validate(cfg.robot_settings[robot_id], cfg.max_goto_points, body)
        except CommandRejected as e:
            _, msgs = tracker.rejected(robot_id, cmd_raw, pts_raw, str(e))
            hub.broadcast(msgs)
            log.warning('%s 명령 거절: %s (cmd=%r points=%r)', robot_id, e, cmd_raw, pts_raw)
            return JSONResponse({'accepted': False, 'error': str(e)}, status_code=400)
        commander = bg.get('commander')
        try:
            if commander is None:
                raise CommandUnavailable('서버가 아직 준비되지 않았습니다')
            commander.publish_cmd(robot_id, sent)
        except CommandUnavailable as e:
            entry, msgs = tracker.rejected(robot_id, cmd, points, str(e), result=FAILED)
            hub.broadcast(msgs)
            log.error('%s 명령 발행 실패: %s', robot_id, e)
            return JSONResponse({'accepted': False, 'error': str(e)}, status_code=503)
        # 발행과 기록 사이에 await 가 없으므로, 로봇의 응답 이벤트는 이 기록 뒤에 처리된다
        entry, msgs = tracker.sent(robot_id, cmd, points, sent)
        hub.broadcast(msgs)
        log.info('%s 명령 발행: %s', robot_id, sent)
        return {'accepted': True, 'sent': sent, 'id': entry['id']}

    def procs_guard(request):
        """프로세스 제어(원격 실행) 공통 검사: 출처·형식, operator 권한, 그리고 operator 비밀번호가 설정돼 있어야 한다."""
        if request.method == 'POST':
            denied = guard_post(request)
            if denied:
                return denied
        denied = require(request, OPERATOR)
        if denied:
            return denied
        if not auth.procs_allowed:
            return JSONResponse({'accepted': False, 'error': '프로세스 제어는 PINKY_OPERATOR_PASSWORD 환경 변수를 설정해야 켜집니다 (인증 없이 원격 실행을 열지 않습니다)'}, status_code=403)
        return None

    def proc_error(e):
        return JSONResponse({'accepted': False, 'error': str(e), 'needs_confirm': e.code == 'confirm'}, status_code=PROC_STATUS.get(e.code, 502))

    async def confirm_flag(request):
        try:
            body = await request.json()
        except Exception:
            return False
        return isinstance(body, dict) and body.get('confirm') is True

    @app.get('/api/procs')
    async def api_procs():
        return procs_message()

    @app.post('/api/procs/start_all')
    async def procs_start_all(request: Request):
        denied = procs_guard(request)
        if denied:
            return denied
        try:
            procs.start_all()
        except ProcError as e:
            return proc_error(e)
        log.info('프로세스 전체 시작 요청')
        return {'accepted': True}

    @app.post('/api/procs/stop_all')
    async def procs_stop_all(request: Request):
        denied = procs_guard(request)
        if denied:
            return denied
        try:
            procs.stop_all(await confirm_flag(request))
        except ProcError as e:
            return proc_error(e)
        log.info('프로세스 전체 정지 요청')
        return {'accepted': True}

    @app.post('/api/procs/{proc_id}/start')
    async def procs_start(proc_id: str, request: Request):
        denied = procs_guard(request)
        if denied:
            return denied
        try:
            await procs.start(proc_id)
        except ProcError as e:
            log.warning('%s 시작 거절: %s', proc_id, e)
            return proc_error(e)
        return {'accepted': True}

    @app.post('/api/procs/{proc_id}/stop')
    async def procs_stop(proc_id: str, request: Request):
        denied = procs_guard(request)
        if denied:
            return denied
        try:
            await procs.stop(proc_id, await confirm_flag(request))
        except ProcError as e:
            log.warning('%s 정지 거절/실패: %s', proc_id, e)
            return proc_error(e)
        return {'accepted': True}

    @app.get('/api/logs')
    async def api_logs(request: Request, since: int = 0, limit: int = 2000):
        """로그 화면: 프로세스 출력과 GUI 서버 로그. 프로세스 출력에는 비밀이 섞일 수 있어 operator 만 본다 (인증이 꺼져 있으면 누구나 - 이때 프로세스는 없다)."""
        denied = require(request, OPERATOR)
        if denied:
            return denied
        rows, last = logs.read(max(0, since), max(1, min(limit, 5000)))
        sources = [{'id': GUI, 'label': 'GUI 서버'}] + [
            {'id': s.id, 'label': s.label, 'configured': bool(s.command)} for s in cfg.processes]
        return {'epoch': logs.epoch, 'last': last, 'entries': rows, 'sources': sources}

    @app.get('/api/procs/{proc_id}/log')
    async def procs_log(proc_id: str, request: Request):
        denied = procs_guard(request)
        if denied:
            return denied
        try:
            return {'id': proc_id, 'lines': procs.log_lines(proc_id)}
        except ProcError as e:
            return proc_error(e)

    @app.get('/api/plans')
    async def api_plans():
        """지점 사이의 경로 추정 (벽을 피하는 최단 경로). 키는 'A>B'. 지도의 선 그리기용이며 Nav2 의 실제 경로가 아니다."""
        if planner is None:
            return JSONResponse({'error': 'map not available'}, status_code=503)
        if not plans:
            for a, pa in cfg.points.items():
                for b, pb in cfg.points.items():
                    if a == b or (pa['x'], pa['y']) == (pb['x'], pb['y']):
                        continue
                    try:
                        path = planner.plan((pa['x'], pa['y']), (pb['x'], pb['y']))
                    except NoPath as e:
                        log.warning('경로 추정 실패 %s>%s: %s (화면은 직선으로 그린다)', a, b, e)
                        continue
                    plans[f'{a}>{b}'] = {'path': [[round(x, 3), round(y, 3)] for x, y in path], 'length': round(path_length(path), 3)}
        return {'inflation_m': cfg.planner_inflation_m, 'plans': plans}

    @app.get('/api/map')
    async def api_map():
        if map_meta is None:
            return JSONResponse({'error': 'map not available'}, status_code=503)
        return map_meta

    @app.get('/api/map/image')
    async def api_map_image():
        if map_png is None:
            return JSONResponse({'error': 'map not available'}, status_code=503)
        return Response(map_png, media_type='image/png')

    if mock:
        @app.get('/api/mock/cmd_vel')
        async def mock_cmd_vel():
            """(mock 전용) 로봇이 받은 cmd_vel 과 patrol_cmd 기록. 실제 로봇에서는 ros2 topic echo 로 본다."""
            f = bg['commander']
            return {'cmd_vel': [{'t': t, 'robot': r, 'linear': lin, 'angular': ang} for t, r, lin, ang in f.cmd_vel_log],
                    'patrol_cmd': [{'t': t, 'robot': r, 'data': d} for t, r, d in f.cmd_log],
                    'pose': {rid: dict(zip('xyz', p)) for rid, p in f._pose.items()}}

    @app.get('/console')
    async def console():
        return FileResponse(STATIC / 'console.html')

    @app.websocket('/ws')
    async def ws_endpoint(ws: WebSocket):
        if not same_origin(ws.headers):  # 다른 사이트의 페이지가 로봇 제어 소켓을 여는 것을 막는다
            await ws.close(code=1008)
            return
        if auth.role_of(ws.cookies.get(COOKIE)) is None:  # 인증이 켜져 있는데 로그인하지 않았다
            await ws.close(code=1008)
            return
        await ws.accept()
        q = hub.register(full_snapshot())
        client = next(client_ids)
        last_denied = {}  # 로봇 id -> 마지막으로 알린 거절 사유 (10Hz 입력마다 같은 거절을 되풀이해 보내지 않는다)

        def handle(text):
            """브라우저가 보낸 제어 메시지. 잘못된 것은 로그만 남기고 무시한다."""
            if len(text) > MAX_WS_TEXT:
                return
            try:
                msg = json.loads(text)
            except ValueError:
                return
            if not isinstance(msg, dict):
                return
            kind, rid = msg.get('type'), msg.get('robot')
            if kind == 'scan' and rid in cfg.robots and isinstance(msg.get('on'), bool):
                hub.set_scan(q, rid, msg['on'])
                sync_scans()
            elif kind == 'drive' and rid in cfg.robots:
                if not Auth.allows(auth.role_of(ws.cookies.get(COOKIE)), OPERATOR):  # 보기 전용은 조작할 수 없다 (매 메시지 확인: 세션 만료 대비)
                    motion.release(client)
                    if last_denied.get(rid) != 'viewer':
                        last_denied[rid] = 'viewer'
                        q.put_nowait({'type': 'drive_denied', 'robot': rid, 'reason': '보기 전용 계정은 수동 조작할 수 없습니다'})
                    return
                ok, reason = motion.drive(client, rid, msg.get('linear'), msg.get('angular'))
                if ok:
                    last_denied.pop(rid, None)
                elif last_denied.get(rid) != reason:
                    last_denied[rid] = reason
                    q.put_nowait({'type': 'drive_denied', 'robot': rid, 'reason': reason})
            elif kind == 'drive_stop':
                motion.release(client)
                last_denied.clear()
            else:
                log.debug('알 수 없는 WS 메시지 무시: %r', kind)

        async def writer():
            while True:
                msg = await q.get()
                if msg is None:
                    return
                await ws.send_json(msg)

        async def reader():
            async for text in ws.iter_text():
                handle(text)

        tasks = [asyncio.create_task(writer()), asyncio.create_task(reader())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            hub.unregister(q)
            motion.release(client)  # 연결이 어떻게 끊겼든 이 화면이 조작 중이던 로봇은 즉시 멈춘다 (FR4-9)
            sync_scans()            # 이 화면이 마지막 LiDAR 구독자였다면 /scan 구독 해제

    # 화면 파일은 매번 서버에 변경 여부를 확인하게 한다 (no-cache: 안 바뀌면 304). 업데이트 뒤 브라우저가 옛 CSS/JS 와 새 HTML 을 섞어 쓰는 것을 막는다
    @app.middleware('http')
    async def no_stale_frontend(request, call_next):
        resp = await call_next(request)
        if not request.url.path.startswith('/api/'):
            resp.headers['Cache-Control'] = 'no-cache'
        return resp

    # API 와 /ws 를 모두 등록한 뒤 마지막에 정적 파일을 '/' 에 붙인다 (FR2-9, 빌드 단계 없음)
    if FRONTEND.is_dir():
        app.mount('/', StaticFiles(directory=FRONTEND, html=True), name='frontend')
    else:
        log.warning('frontend 폴더가 없어 정적 파일을 서빙하지 않는다: %s', FRONTEND)

    return app


def main():
    ap = argparse.ArgumentParser(description='Pinky 관제 웹 GUI 백엔드')
    ap.add_argument('--mock', action='store_true', help='ROS 없이 가짜 데이터로 실행')
    ap.add_argument('--host', default='127.0.0.1', help='바인드 주소 (기본 127.0.0.1, 다른 PC 접속은 0.0.0.0)')
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--mock-wait-scale', type=float, default=1.0,
                    help='--mock 에서 goto 지점 대기 시간 배율 (기본 1.0 = 설정값 그대로, 0.2 면 2초)')
    ap.add_argument('--mock-scan-mount-deg', type=float, default=0.0,
                    help='--mock 에서 가짜 LiDAR 가 로봇 정면에서 돌아 달린 각도(도). 0 이 아니면 map->센서 변환(tf)으로 그린다 (센서 방향 보정 시험용)')
    ap.add_argument('--mock-scan-no-tf', action='store_true',
                    help='--mock-scan-mount-deg 와 함께: 센서가 돌려 달렸어도 tf 를 주지 않는다 (amcl + yaw_offset_deg 대체 경로 시험용)')
    ap.add_argument('--mock-proc-fail', default='', help='--mock 에서 시작 직후 실패하는 프로세스 id (쉼표로 구분)')
    ap.add_argument('--mock-proc-external', default='', help='--mock 에서 이미 다른 곳에서 실행 중인 것으로 보이는 프로세스 id (쉼표로 구분)')
    ap.add_argument('--config', default=None, help='robots.yaml 경로 (기본 config/robots.yaml)')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    cfg = load_config(args.config)
    auth = Auth.from_env(os.environ)
    if not auth.enabled:
        logging.getLogger('backend').warning(
            '인증이 꺼져 있습니다 (PINKY_OPERATOR_PASSWORD, PINKY_VIEWER_PASSWORD 미설정). 프로세스 제어(원격 실행)는 꺼진 채로 시작합니다')
        if args.host not in LOOPBACK:
            logging.getLogger('backend').warning(
                '--host %s: 인증 없이 같은 네트워크의 누구나 start/goto%s를 보낼 수 있습니다',
                args.host, '와 수동 조작(cmd_vel) ' if cfg.motion.manual_enabled else ' ')
    elif not auth.procs_allowed:
        logging.getLogger('backend').warning('PINKY_OPERATOR_PASSWORD 가 없어 프로세스 제어는 꺼진 채로 시작합니다')
    if args.host not in LOOPBACK and auth.enabled:
        logging.getLogger('backend').warning('--host %s: 로그인 비밀번호가 암호화되지 않은 HTTP 로 전달됩니다. 신뢰하는 내부망에서만 쓰세요', args.host)

    import signal
    import uvicorn
    # 터미널 창을 닫으면 SIGHUP 이 온다. 기본 동작은 정리 없이 즉시 종료라서 켜 둔 프로세스가 남는다 -> Ctrl-C(SIGTERM)와 같은 정상 종료로 바꾼다
    signal.signal(signal.SIGHUP, lambda *_: os.kill(os.getpid(), signal.SIGTERM))
    uvicorn.run(create_app(cfg, args.mock, {'wait_scale': args.mock_wait_scale, 'scan_mount_yaw': math.radians(args.mock_scan_mount_deg),
                                                 'scan_mount_tf': not args.mock_scan_no_tf,
                                                 'proc_opts': {'fail_start': [x for x in args.mock_proc_fail.split(',') if x],
                                                               'external': [x for x in args.mock_proc_external.split(',') if x]}},
                                            auth=auth), host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
