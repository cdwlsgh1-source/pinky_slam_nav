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
        self._clients = {}  # 큐 -> 이 클라이언트가 LiDAR 를 켠 로봇 id 집합

    def register(self, snapshot):
        """동기 함수다. 스냅샷을 큐 맨 앞에 넣고 등록하므로 이후 이벤트가 스냅샷보다 앞서 나가지 않는다."""
        q = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)
        q.put_nowait(snapshot)
        self._clients[q] = set()
        return q

    def unregister(self, q):
        self._clients.pop(q, None)  # 이 클라이언트의 scan 구독도 함께 사라진다 (scan_count 가 줄어든다)

    # ---- LiDAR 구독 (FR4-5): 로봇별로 켠 클라이언트 수를 센다. 0 이 되면 호출한 쪽이 ROS 구독을 해제한다 ----
    def set_scan(self, q, rid, on):
        subs = self._clients.get(q)
        if subs is None:
            return
        (subs.add if on else subs.discard)(rid)

    def scan_count(self, rid):
        return sum(1 for subs in self._clients.values() if rid in subs)

    def scan_robots(self):
        """한 명이라도 켜 둔 로봇 id 집합"""
        out = set()
        for subs in self._clients.values():
            out |= subs
        return out

    def send_scan(self, rid, message):
        """스캔을 켠 클라이언트에게만 보낸다. 스캔은 버려도 되는 데이터라, 큐가 거의 찼으면 건너뛴다 (연결을 끊지 않는다)."""
        for q, subs in list(self._clients.items()):
            if rid in subs and q.qsize() < CLIENT_QUEUE_SIZE * 3 // 4:
                q.put_nowait(message)

    def broadcast(self, messages):
        for q in list(self._clients):
            for m in messages:
                try:
                    q.put_nowait(m)
                except asyncio.QueueFull:
                    log.warning('느린 WebSocket 클라이언트를 끊는다')
                    self._clients.pop(q, None)
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
