"""WebSocket 클라이언트 관리와 브로드캐스트.

클라이언트마다 큐 + 전송 태스크를 둔다. 느린 브라우저 하나가 전체 브로드캐스트를 막지 않게 하고,
접속 직후 snapshot 이 항상 이후 이벤트보다 먼저 나가는 순서도 큐가 보장한다.
"""
import asyncio
import json
import logging

log = logging.getLogger('pinky_web_gui.hub')


class Client:
    def __init__(self, queue_size):
        self.queue = asyncio.Queue(maxsize=queue_size)
        self.dropped = False


class Hub:
    def __init__(self, state, queue_size=256):
        self.state = state
        self._queue_size = queue_size
        self._clients = set()

    @property
    def client_count(self):
        return len(self._clients)

    # ---- 클라이언트 ----
    def add_client(self):
        client = Client(self._queue_size)
        # snapshot 을 만들고 등록하는 사이에 await 가 없어서 이벤트가 끼어들 수 없다.
        client.queue.put_nowait(self._dumps(self.state.snapshot()))
        self._clients.add(client)
        return client

    def remove_client(self, client):
        self._clients.discard(client)

    # ---- ROS/mock → 상태 갱신 → 브로드캐스트 (이벤트 루프 스레드에서만 호출) ----
    def ingest_patrol(self, robot, raw):
        self._emit(self.state.ingest_patrol(robot, raw))

    def ingest_pose(self, robot, x, y, yaw):
        self._emit(self.state.ingest_pose(robot, x, y, yaw))

    def ingest_battery(self, robot, percentage, voltage):
        self._emit(self.state.ingest_battery(robot, percentage, voltage))

    def ingest_zone(self, status):
        self._emit(self.state.ingest_zone(status))

    def check_online(self):
        self._emit(self.state.check_online())

    # ---- 내부 ----
    @staticmethod
    def _dumps(event):
        # allow_nan=False: NaN 이 섞이면 브라우저 JSON.parse 가 실패하므로 여기서 걸러낸다.
        return json.dumps(event, ensure_ascii=False, allow_nan=False)

    def _emit(self, events):
        for event in events:
            message = self._dumps(event)
            for client in list(self._clients):
                try:
                    client.queue.put_nowait(message)
                except asyncio.QueueFull:
                    # 못 따라오는 클라이언트는 끊는다. 재접속하면 새 snapshot 을 받는다.
                    log.warning('느린 WebSocket 클라이언트를 끊는다')
                    self._drop(client)

    def _drop(self, client):
        client.dropped = True
        self._clients.discard(client)
        while not client.queue.empty():
            client.queue.get_nowait()
        client.queue.put_nowait(None)   # 전송 태스크에 종료 신호
