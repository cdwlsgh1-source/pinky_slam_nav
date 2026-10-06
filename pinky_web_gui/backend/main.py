"""관제 PC 웹 GUI 백엔드 (FastAPI + rclpy).

실행: python -m backend.main [--mock] [--host 0.0.0.0] [--port 8000]
(pinky_web_gui/ 에서 실행. 자세한 순서는 README.md)

Step 1 은 구독 전용이다. 로봇으로 나가는 발행은 없다.
"""
import argparse
import asyncio
import contextlib
import json
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .config import load_config
from .hub import Hub
from .state import FleetState

log = logging.getLogger('pinky_web_gui')

STATIC_DIR = Path(__file__).resolve().parent / 'static'


def create_app(mock=False, mock_silence=None):
    config = load_config()
    state = FleetState(config.robots, config.online_timeout_sec, config.pose_max_hz)
    hub = Hub(state)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        loop = asyncio.get_running_loop()
        tasks = []
        bridge = None

        if mock:
            from .mock import start_mock
            tasks += start_mock(config, hub, mock_silence)
            log.info('mock 모드: ROS 를 쓰지 않고 가짜 데이터를 만든다')
        else:
            from .ros_node import RosBridge   # rclpy 는 실제 모드에서만 import
            bridge = RosBridge(config, hub, loop)
            bridge.start()

        async def watch_online():
            while True:
                await asyncio.sleep(1.0)
                hub.check_online()

        tasks.append(asyncio.create_task(watch_online()))
        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if bridge is not None:
                bridge.stop()

    app = FastAPI(title='Pinky 관제 웹 GUI', lifespan=lifespan)

    @app.get('/api/health')
    async def health():
        return {'status': 'ok', 'mode': 'mock' if mock else 'ros', 'clients': hub.client_count}

    @app.get('/api/state')
    async def get_state():
        return state.snapshot()

    @app.get('/')
    async def index():
        return FileResponse(STATIC_DIR / 'index.html')

    @app.websocket('/ws')
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        client = hub.add_client()   # 큐의 첫 항목이 snapshot 이다

        async def pump():
            try:
                while True:
                    message = await client.queue.get()
                    if message is None:
                        break
                    await ws.send_text(message)
            except Exception:   # 전송 실패 = 연결이 이미 끊김
                pass
            finally:
                with contextlib.suppress(Exception):
                    await ws.close()

        sender = asyncio.create_task(pump())
        try:
            while True:
                await ws.receive_text()   # 클라이언트 메시지는 쓰지 않는다. 끊김을 감지하려고 읽는다.
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            hub.remove_client(client)
            sender.cancel()

    return app


def _parse_silence(text):
    """'pinky2:8' → ('pinky2', 8.0)"""
    robot, _, seconds = text.partition(':')
    return robot, float(seconds)


def main():
    parser = argparse.ArgumentParser(description='Pinky 관제 웹 GUI 백엔드')
    parser.add_argument('--mock', action='store_true', help='ROS 없이 가짜 데이터로 실행')
    parser.add_argument('--mock-silence', metavar='ROBOT:SEC', type=_parse_silence,
                        help='(mock 전용) 지정한 로봇이 SEC 초 뒤에 데이터를 멈춘다. online 만료 확인용')
    parser.add_argument('--host', default='127.0.0.1',
                        help='바인드 주소 (기본 127.0.0.1. 다른 PC에서 접속하려면 0.0.0.0)')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()

    if args.mock_silence and not args.mock:
        parser.error('--mock-silence 는 --mock 과 함께만 쓸 수 있다')

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    app = create_app(mock=args.mock, mock_silence=args.mock_silence)
    uvicorn.run(app, host=args.host, port=args.port, log_level='info')


if __name__ == '__main__':
    main()
