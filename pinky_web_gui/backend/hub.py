"""WebSocket 연결 관리와 브로드캐스트.

클라이언트마다 보내기 큐와 쓰기 태스크를 둔다. 느린 클라이언트 하나가 다른 클라이언트나
상태 처리를 막지 않게 하고, 큐가 가득 차면(따라가지 못하면) 그 연결만 끊는다.
"""
import asyncio
import logging

log = logging.getLogger('backend.hub')

CLIENT_QUEUE_SIZE = 256


class Hub:
    def __init__(self):
        self._clients = set()

    def register(self, snapshot):
        """동기 함수다. 스냅샷을 큐 맨 앞에 넣고 등록하므로 이후 이벤트가 스냅샷보다 앞서 나가지 않는다."""
        q = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)
        q.put_nowait(snapshot)
        self._clients.add(q)
        return q

    def unregister(self, q):
        self._clients.discard(q)

    def broadcast(self, messages):
        for q in list(self._clients):
            for m in messages:
                try:
                    q.put_nowait(m)
                except asyncio.QueueFull:
                    log.warning('느린 WebSocket 클라이언트를 끊는다')
                    self._clients.discard(q)
                    self._drain_and_close(q)
                    break

    @staticmethod
    def _drain_and_close(q):
        while not q.empty():
            q.get_nowait()
        q.put_nowait(None)  # None = 종료 신호

    @property
    def count(self):
        return len(self._clients)
