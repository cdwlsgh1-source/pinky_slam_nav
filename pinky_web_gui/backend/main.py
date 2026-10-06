"""FastAPI 백엔드. 실행: pinky_web_gui/ 에서 `python -m backend.main [--mock] [--host H] [--port P]`."""
import argparse
import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .config import Config, load_config
from .hub import Hub
from .state import StateStore

log = logging.getLogger('backend')

STATIC = Path(__file__).resolve().parent / 'static'
TICK_SEC = 0.1  # online 타임아웃 판정과 보류된 pose 전송 주기


def create_app(cfg: Config, mock: bool) -> FastAPI:
    store = StateStore(cfg.robots, cfg.zone_status_topic, cfg.online_timeout_sec, cfg.pose_max_hz)
    hub = Hub()
    bg = {}  # lifespan 에서 만든 리소스

    async def consume(queue):
        """큐에서 이벤트를 꺼내 상태에 반영하고 변경분을 브로드캐스트한다 (상태를 바꾸는 유일한 곳)."""
        while True:
            event = await queue.get()
            try:
                hub.broadcast(store.apply(event))
            except Exception:
                log.exception('이벤트 처리 실패(무시하고 계속): %r', event)

    async def ticker():
        while True:
            await asyncio.sleep(TICK_SEC)
            try:
                hub.broadcast(store.tick())
            except Exception:
                log.exception('tick 실패(무시하고 계속)')

    @contextlib.asynccontextmanager
    async def lifespan(app):
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        tasks = [asyncio.create_task(consume(queue)), asyncio.create_task(ticker())]
        ros = None
        if mock:
            from .mock import run_mock
            tasks.append(asyncio.create_task(run_mock(cfg.robots, queue)))
            log.info('mock 모드: ROS 를 사용하지 않는다')
        else:
            from .ros_bridge import RosBridge  # rclpy 는 여기서만 import
            ros = RosBridge(cfg, loop, queue)
            ros.start()
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
        return store.snapshot()

    @app.get('/')
    async def console():
        return FileResponse(STATIC / 'console.html')

    @app.websocket('/ws')
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        q = hub.register(store.snapshot())
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

    return app


def main():
    ap = argparse.ArgumentParser(description='Pinky 관제 웹 GUI 백엔드')
    ap.add_argument('--mock', action='store_true', help='ROS 없이 가짜 데이터로 실행')
    ap.add_argument('--host', default='127.0.0.1', help='바인드 주소 (기본 127.0.0.1, 다른 PC 접속은 0.0.0.0)')
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--config', default=None, help='robots.yaml 경로 (기본 config/robots.yaml)')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    cfg = load_config(args.config)

    import uvicorn
    uvicorn.run(create_app(cfg, args.mock), host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
