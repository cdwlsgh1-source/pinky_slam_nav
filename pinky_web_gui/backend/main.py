"""FastAPI 백엔드. 실행: pinky_web_gui/ 에서 `python -m backend.main [--mock] [--host H] [--port P]`."""
import argparse
import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .commands import CommandRejected, CommandTracker, CommandUnavailable, FAILED, validate
from .config import Config, load_config
from .hub import Hub
from .map_loader import load_map
from .state import StateStore

log = logging.getLogger('backend')

STATIC = Path(__file__).resolve().parent / 'static'
FRONTEND = Path(__file__).resolve().parent.parent / 'frontend'
TICK_SEC = 0.1  # online 타임아웃 판정과 보류된 pose 전송 주기


def create_app(cfg: Config, mock: bool, mock_opts: dict = None) -> FastAPI:
    store = StateStore(cfg.robots, cfg.zone_status_topic, cfg.online_timeout_sec, cfg.pose_max_hz)
    tracker = CommandTracker(cfg.robots, cfg.command_history_size, cfg.no_response_sec)
    hub = Hub()
    bg = {}  # lifespan 에서 만든 리소스 (commander: publish_cmd(rid, data) 를 가진 RosBridge 또는 MockFleet)

    def full_snapshot():
        """WS 접속 시와 /api/state 가 쓰는 전체 상태. 상태 저장소 + 명령 이력과 로봇별 마지막 명령."""
        snap = store.snapshot()
        for rid, r in snap['robots'].items():
            r['last_command'] = tracker.last_command(rid)
            r['last_task'] = tracker.last_task(rid)
        snap['commands'] = tracker.history()
        return snap

    # 지도는 시작할 때 한 번 읽는다. 실패해도 서버는 뜨고 /api/map 만 503 을 돌려준다.
    map_meta = map_png = None
    if cfg.map_yaml:
        try:
            map_meta, map_png = load_map(cfg.map_yaml)
            log.info('지도 로드: %s (%dx%d, %.3f m/px)', cfg.map_yaml,
                     map_meta['width'], map_meta['height'], map_meta['resolution'])
        except Exception:
            log.exception('지도 로드 실패: %s', cfg.map_yaml)

    async def consume(queue):
        """큐에서 이벤트를 꺼내 상태에 반영하고 변경분을 브로드캐스트한다 (상태를 바꾸는 유일한 곳)."""
        while True:
            event = await queue.get()
            try:
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
            except Exception:
                log.exception('tick 실패(무시하고 계속)')

    @contextlib.asynccontextmanager
    async def lifespan(app):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        tasks = [asyncio.create_task(consume(queue)), asyncio.create_task(ticker())]
        ros = None
        if mock:
            from .mock import MockFleet
            fleet = MockFleet(cfg, queue, **(mock_opts or {}))
            bg['commander'] = fleet
            tasks.append(asyncio.create_task(fleet.run()))
            log.info('mock 모드: ROS 를 사용하지 않는다')
        else:
            from .ros_bridge import RosBridge  # rclpy 는 여기서만 import
            ros = RosBridge(cfg, loop, queue)
            ros.start()
            bg['commander'] = ros
        bg['mock'] = mock
        try:
            yield
        finally:
            if ros:
                ros.stop()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title='Pinky web GUI backend', lifespan=lifespan)

    @app.get('/api/health')
    async def health():
        return {'ok': True, 'mock': mock, 'robots': list(cfg.robots), 'ws_clients': hub.count}

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

    @app.post('/api/robots/{robot_id}/command')
    async def send_command(robot_id: str, request: Request):
        """start / stop / goto 를 검증한 뒤 /{id}/patrol_cmd 로 발행한다. accepted 는 '발행했다' 는 뜻일 뿐이다.

        본문은 직접 파싱해서 모든 입력 오류를 400 으로 통일한다 (허용 목록 밖은 400, 알 수 없는 로봇은 404).
        """
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

    @app.get('/console')
    async def console():
        return FileResponse(STATIC / 'console.html')

    @app.websocket('/ws')
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        q = hub.register(full_snapshot())
        try:
            while True:
                msg = await q.get()
                if msg is None:
                    break
                await ws.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.unregister(q)

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
    ap.add_argument('--config', default=None, help='robots.yaml 경로 (기본 config/robots.yaml)')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    cfg = load_config(args.config)

    import uvicorn
    uvicorn.run(create_app(cfg, args.mock, {'wait_scale': args.mock_wait_scale}), host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
