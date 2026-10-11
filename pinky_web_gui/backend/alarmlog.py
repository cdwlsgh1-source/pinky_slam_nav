"""알람 이력 저장. 알람바에는 최근 N건만 보이지만, 일어난 사건은 모두 파일로 남긴다.

저장 규칙
  - 알람 이벤트(raised: 생김, repeated: ACK 전의 같은 알람이 또 생김(시도마다 한 줄), acked: 확인(ACK), cleared: 해소)마다 JSON 한 줄(JSONL)을 파일 끝에 붙인다. 일어난 순서대로 쌓기만 하고 기존 줄은 고치지 않는다. 이벤트가 오면 바로 쓰므로 서버가 갑자기 꺼져도 지금까지의 기록이 남는다.
  - 한 파일에는 `chunk`(기본 1000)건까지만 담고, 차면 그 다음 이벤트의 시각으로 새 파일을 만든다.
  - 파일 이름에 그 파일을 처음 만든 날짜와 시각(서버 PC 의 현지 시각)이 들어간다: `alarms_2026-10-11_193012.jsonl` (같은 초에 둘이면 `-2`). 날짜가 바뀌면 안 찼어도 새 파일을 만든다.
  - 예전 번호 이름(`alarms_2026-10-11_001.jsonl`)의 파일도 목록·조회·이어쓰기에서 같이 다룬다 (같은 날 안에서 번호 파일이 시각 파일보다 앞).
  - 레코드에도 `saved_at`(현지 시각 'YYYY-MM-DD HH:MM:SS')과 `t`(epoch)를 적어 파일만 따로 열어 봐도 날짜를 알 수 있다.
  - 서버를 다시 켜면 오늘 날짜의 가장 최근 파일이 아직 안 찼을 때 이어서 쓴다.
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

TIME_RE = re.compile(r'^alarms_(\d{4}-\d{2}-\d{2})_(\d{6})(?:-(\d+))?\.jsonl$')   # 시각 이름: 날짜_HHMMSS[-중복번호]
SEQ_RE = re.compile(r'^alarms_(\d{4}-\d{2}-\d{2})_(\d{3})\.jsonl$')                 # 예전 번호 이름 (읽기와 이어쓰기만 지원)
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
READ_MAX = 1000   # 한 번에 돌려주는 최대 레코드 수


def stamp(t):
    """epoch -> 서버 PC 현지 시각 문자열."""
    return datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M:%S')


class AlarmHistory:
    def __init__(self, directory, chunk=1000, now=time.time):
        self.dir = Path(directory)
        self.chunk = max(1, int(chunk))
        self._now = now
        self._cur = None     # (날짜, 파일 이름, 지금까지 쓴 줄 수)
        self._failed = False  # 쓰기 실패를 로그에 한 번만 남기려고 (복구되면 다시 알린다)

    # ---- 쓰기 ----
    def record(self, event, alarm):
        """알람 이벤트 하나를 저장한다. 실패해도 예외를 내지 않고 False 를 돌려준다."""
        t = self._now()
        row = {'saved_at': stamp(t), 't': t, 'event': event, **alarm}
        try:
            path = self._target(row['saved_at'])
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

    def _target(self, stamp_text):
        """지금 쓸 파일 경로. 날짜가 바뀌었거나 chunk 건이 찼으면 지금 시각으로 새 파일을 만든다."""
        self.dir.mkdir(parents=True, exist_ok=True)
        date = stamp_text[:10]
        if self._cur is None or self._cur[0] != date:
            self._cur = self._resume(date)
        if self._cur is not None and self._cur[2] >= self.chunk:
            self._cur = None
        if self._cur is None:
            self._cur = (date, self._new_name(date, stamp_text[11:].replace(':', '')), 0)
        return self.dir / self._cur[1]

    def _new_name(self, date, hhmmss):
        name = f'alarms_{date}_{hhmmss}.jsonl'
        n = 1
        while (self.dir / name).exists():   # 같은 초에 파일이 또 필요하면 겹치지 않게 번호를 붙인다
            n += 1
            name = f'alarms_{date}_{hhmmss}-{n}.jsonl'
        return name

    def _resume(self, date):
        """그날의 가장 최근 파일을 찾아 이어 쓸 위치를 구한다 (없으면 None)."""
        found = [(self._parse(p.name), p.name) for p in self.dir.iterdir()]
        found = [(m, name) for m, name in found if m and m['date'] == date]
        if not found:
            return None
        _, name = max(found, key=lambda x: x[0]['key'])
        return (date, name, self._count_lines(self.dir / name))

    # ---- 읽기 ----
    @staticmethod
    def _parse(name):
        """파일 이름 -> {date, key, seq, started}. 같은 날 안에서 key 가 클수록 최근이다 (번호 이름 < 시각 이름)."""
        m = SEQ_RE.match(name)
        if m:
            return {'date': m.group(1), 'key': (0, int(m.group(2)), 0), 'seq': int(m.group(2)), 'started': None}
        m = TIME_RE.match(name)
        if m:
            t = m.group(2)
            return {'date': m.group(1), 'key': (1, int(t), int(m.group(3) or 1)), 'seq': None, 'started': f'{t[:2]}:{t[2:4]}:{t[4:]}'}
        return None

    @staticmethod
    def _count_lines(path):
        with open(path, 'rb') as f:
            return sum(1 for line in f if line.strip())

    def files(self):
        """저장된 파일 목록 (최신이 앞): [{name, date, started(시각 이름이면 HH:MM:SS), seq(예전 번호 이름이면 번호), records, size}]."""
        if not self.dir.is_dir():
            return []
        rows = []
        for p in self.dir.iterdir():
            parsed = self._parse(p.name)
            if parsed:
                rows.append({'name': p.name, 'date': parsed['date'], 'started': parsed['started'], 'seq': parsed['seq'], 'records': self._count_lines(p),
                             'size': p.stat().st_size, '_key': (parsed['date'], parsed['key'])})
        rows.sort(key=lambda r: r['_key'], reverse=True)
        for r in rows:
            del r['_key']
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
