"""알람 이력 저장. 알람바에는 최근 N건만 보이지만, 일어난 사건은 모두 파일로 남긴다.

저장 규칙
  - 알람 이벤트(raised: 생김, repeated: 반복, cleared: 해소)마다 JSON 한 줄(JSONL)을 파일 끝에 붙인다. 이벤트가 오면 바로 쓰므로 서버가 갑자기 꺼져도 지금까지의 기록이 남는다.
  - 한 파일에는 `chunk`(기본 100)건까지만 담고, 차면 다음 번호의 새 파일에 쓴다.
  - 파일 이름에 저장한 날짜(서버 PC 의 현지 날짜)가 들어간다: `alarms_2026-10-11_001.jsonl`. 날짜가 바뀌면 100건이 안 찼어도 새 날짜의 `_001` 부터 다시 시작한다.
  - 레코드에도 `saved_at`(현지 시각 'YYYY-MM-DD HH:MM:SS')과 `t`(epoch)를 적어 파일만 따로 열어 봐도 날짜를 알 수 있다.
  - 서버를 다시 켜면 오늘 날짜의 마지막 파일이 아직 안 찼을 때 이어서 쓴다.
  - 쓰기에 실패(디스크 가득 참, 권한 등)해도 알람 자체는 계속 동작한다 (경고 로그만 남긴다). 파일은 자동으로 지우지 않는다.
rclpy 와 무관하며 시계는 주입한다 (CommandTracker, AlarmManager 와 같은 `now=` 관례).
"""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger('backend.alarmlog')

NAME_RE = re.compile(r'^alarms_(\d{4}-\d{2}-\d{2})_(\d{3,})\.jsonl$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
READ_MAX = 1000   # 한 번에 돌려주는 최대 레코드 수


def stamp(t):
    """epoch -> 서버 PC 현지 시각 문자열."""
    return datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M:%S')


class AlarmHistory:
    def __init__(self, directory, chunk=100, now=time.time):
        self.dir = Path(directory)
        self.chunk = max(1, int(chunk))
        self._now = now
        self._cur = None     # (날짜, 번호, 지금까지 쓴 줄 수)
        self._failed = False  # 쓰기 실패를 로그에 한 번만 남기려고 (복구되면 다시 알린다)

    # ---- 쓰기 ----
    def record(self, event, alarm):
        """알람 이벤트 하나를 저장한다. 실패해도 예외를 내지 않고 False 를 돌려준다."""
        t = self._now()
        row = {'saved_at': stamp(t), 't': t, 'event': event, **alarm}
        try:
            path = self._target(stamp(t)[:10])
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
            d, n, lines = self._cur
            self._cur = (d, n, lines + 1)
            if self._failed:
                log.info('알람 이력 저장이 복구되었다: %s', self.dir)
                self._failed = False
            return True
        except Exception as e:
            self._cur = None   # 다음에는 디스크의 실제 상태를 다시 읽는다
            if not self._failed:
                log.warning('알람 이력 저장 실패(알람은 계속 동작한다): %s: %s', self.dir, e)
                self._failed = True
            return False

    def _target(self, date):
        """지금 쓸 파일 경로. 날짜가 바뀌었거나 100건이 찼으면 새 번호의 파일을 만든다."""
        self.dir.mkdir(parents=True, exist_ok=True)
        if self._cur is None or self._cur[0] != date:
            self._cur = self._resume(date)
        d, n, lines = self._cur
        if lines >= self.chunk:
            self._cur = (d, n + 1, 0)
        d, n, _ = self._cur
        return self.dir / f'alarms_{d}_{n:03d}.jsonl'

    def _resume(self, date):
        """그날의 마지막 파일을 찾아 이어 쓸 위치를 구한다 (없으면 001 부터)."""
        nums = [m for m in (self._parse(p.name) for p in self.dir.iterdir()) if m and m[0] == date]
        if not nums:
            return (date, 1, 0)
        n = max(x[1] for x in nums)
        return (date, n, self._count_lines(self.dir / f'alarms_{date}_{n:03d}.jsonl'))

    # ---- 읽기 ----
    @staticmethod
    def _parse(name):
        m = NAME_RE.match(name)
        return (m.group(1), int(m.group(2))) if m else None

    @staticmethod
    def _count_lines(path):
        with open(path, 'rb') as f:
            return sum(1 for line in f if line.strip())

    def files(self):
        """저장된 파일 목록 (최신이 앞): [{name, date, seq, records, size}]."""
        if not self.dir.is_dir():
            return []
        rows = []
        for p in self.dir.iterdir():
            parsed = self._parse(p.name)
            if parsed:
                rows.append({'name': p.name, 'date': parsed[0], 'seq': parsed[1], 'records': self._count_lines(p), 'size': p.stat().st_size})
        rows.sort(key=lambda r: (r['date'], r['seq']), reverse=True)
        return rows

    def read(self, date=None, limit=100):
        """저장된 레코드를 최신이 앞인 순서로 돌려준다. date('YYYY-MM-DD')를 주면 그날 것만. 읽다가 깨진 줄은 건너뛴다."""
        if date is not None and not DATE_RE.match(date):
            raise ValueError('date 는 YYYY-MM-DD 형식이어야 한다')
        limit = max(1, min(int(limit), READ_MAX))
        out = []
        for f in self.files():
            if date is not None and f['date'] != date:
                continue
            with open(self.dir / f['name'], encoding='utf-8') as fh:
                lines = fh.readlines()
            for line in reversed(lines):
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
                if len(out) >= limit:
                    return out
        return out
