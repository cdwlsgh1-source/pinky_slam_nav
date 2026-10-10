"""로그 화면용 로그 저장소: GUI 가 켠 프로세스의 터미널 출력과 GUI 서버 자신의 로그를 소스별로 모은다.

- 소스마다 최근 PER_SOURCE 줄만 메모리에 둔다 (디스크에 쓰지 않는다. 서버를 재시작하면 사라진다).
  소스별로 따로 자르는 이유: 한 프로세스가 로그를 쏟아내도 다른 소스의 줄이 밀려나지 않게.
- 모든 줄에 증가하는 번호(n)가 붙는다. 화면은 마지막으로 받은 번호 이후만 요청한다 (since).
- 레벨은 줄 내용에서 추정한다: ROS 형식 [WARN] [ERROR], 파이썬 로깅 형식 WARNING ERROR, Traceback 등.
"""
import collections
import itertools
import logging
import re
import threading
import time

PER_SOURCE = 1000
MAX_LINE = 2000
GUI = 'gui'

DEBUG, INFO, WARN, ERROR = 0, 1, 2, 3
LEVEL_NAMES = {DEBUG: 'DEBUG', INFO: 'INFO', WARN: 'WARN', ERROR: 'ERROR'}

_ROS = re.compile(r'\[(DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]')
_PY = re.compile(r'^\S*\s*\S*\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b|^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b')
_BAD = re.compile(r'^Traceback \(most recent call last\)|^\w*(Error|Exception)\b|^\s*raise \w+|Segmentation fault|\bfatal\b', re.I)
_TOKEN = {'DEBUG': DEBUG, 'INFO': INFO, 'WARN': WARN, 'WARNING': WARN, 'ERROR': ERROR, 'FATAL': ERROR, 'CRITICAL': ERROR}


def guess_level(text):
    m = _ROS.search(text)
    if m:
        return _TOKEN[m.group(1)]
    m = _PY.search(text)
    if m:
        return _TOKEN[m.group(1) or m.group(2)]
    if _BAD.search(text):
        return ERROR
    return INFO


class LogStore:
    def __init__(self, per_source=PER_SOURCE, now=time.time):
        self._per = per_source
        self._now = now
        self._n = itertools.count(1)
        self._by_src = {}
        self._lock = threading.Lock()   # rclpy 스레드와 이벤트 루프가 함께 쓴다
        self.epoch = f'{int(now() * 1000):x}'   # 서버를 다시 띄웠는지 화면이 알아보는 값 (번호가 처음부터 다시 시작한다)

    def add(self, src, text, level=None):
        text = str(text).rstrip()
        if not text:
            return
        text = text[:MAX_LINE]
        lv = guess_level(text) if level is None else level
        with self._lock:
            q = self._by_src.get(src)
            if q is None:
                q = self._by_src[src] = collections.deque(maxlen=self._per)
            q.append({'n': next(self._n), 't': round(self._now(), 3), 'src': src, 'lvl': LEVEL_NAMES[lv], 'text': text})

    def read(self, since=0, limit=2000):
        """since 보다 큰 번호의 줄을 번호 순으로 최대 limit 개. 반환: (줄 목록, 마지막 번호)."""
        with self._lock:
            rows = [r for q in self._by_src.values() for r in q if r['n'] > since]
        rows.sort(key=lambda r: r['n'])
        rows = rows[:limit]
        return rows, (rows[-1]['n'] if rows else since)

    def clear(self, src=None):
        with self._lock:
            for k in ([src] if src else list(self._by_src)):
                self._by_src.pop(k, None)


class StoreHandler(logging.Handler):
    """파이썬 로깅(backend.*)을 GUI 소스로 모은다. 레벨은 로깅 레벨을 그대로 쓴다."""

    def __init__(self, store):
        super().__init__(logging.INFO)
        self._store = store

    def emit(self, record):
        try:
            lv = ERROR if record.levelno >= logging.ERROR else WARN if record.levelno >= logging.WARNING else INFO
            text = f'{record.name}: {record.getMessage()}'
            if record.exc_info:
                text += '\n' + logging.Formatter().formatException(record.exc_info)
            for line in text.splitlines():
                self._store.add(GUI, line, lv)
        except Exception:
            self.handleError(record)
