"""알람 이력 저장 테스트: 100건씩 파일 나누기, 파일 이름의 날짜, 이어 쓰기, 날짜 바뀜, 쓰기 실패, 읽기. 실행: .venv/bin/python -m pytest tests -q"""
import json
from datetime import datetime

import pytest

from backend.alarmlog import AlarmHistory
from backend.alarms import MONITOR_SIZE, AlarmManager


def epoch(y, m, d, hh=12, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def lines(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines()]


def test_records_are_split_into_chunks_of_100(tmp_path):
    h = AlarmHistory(tmp_path, 100, now=Clock(epoch(2026, 10, 11)))
    for i in range(250):
        assert h.record('raised', {'id': i, 'code': 'CMD_SENT'})
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ['alarms_2026-10-11_001.jsonl', 'alarms_2026-10-11_002.jsonl', 'alarms_2026-10-11_003.jsonl']
    assert [len(lines(tmp_path / n)) for n in names] == [100, 100, 50]
    assert lines(tmp_path / names[0])[0]['id'] == 0 and lines(tmp_path / names[1])[0]['id'] == 100   # 순서대로 이어서


def test_record_carries_save_date_and_event(tmp_path):
    h = AlarmHistory(tmp_path, 100, now=Clock(epoch(2026, 10, 11, 18, 32, 47)))
    h.record('cleared', {'id': 7, 'code': 'ROBOT_OFFLINE', 'robot': 'pinky1', 'text': 'pinky1 is offline'})
    row = lines(tmp_path / 'alarms_2026-10-11_001.jsonl')[0]
    assert row['saved_at'] == '2026-10-11 18:32:47' and row['event'] == 'cleared'
    assert row['id'] == 7 and row['robot'] == 'pinky1' and isinstance(row['t'], float)


def test_korean_text_is_stored_readably(tmp_path):
    h = AlarmHistory(tmp_path, 100, now=Clock(epoch(2026, 10, 11)))
    h.record('raised', {'id': 1, 'detail': '구독자 없음'})
    assert '구독자 없음' in (tmp_path / 'alarms_2026-10-11_001.jsonl').read_text(encoding='utf-8')


def test_restart_continues_unfilled_file(tmp_path):
    c = Clock(epoch(2026, 10, 11))
    h = AlarmHistory(tmp_path, 100, now=c)
    for i in range(30):
        h.record('raised', {'id': i})
    h2 = AlarmHistory(tmp_path, 100, now=c)   # 서버를 다시 켠 것
    for i in range(30, 130):
        h2.record('raised', {'id': i})
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ['alarms_2026-10-11_001.jsonl', 'alarms_2026-10-11_002.jsonl']
    assert [len(lines(tmp_path / n)) for n in names] == [100, 30]
    assert [r['id'] for r in lines(tmp_path / names[0])] == list(range(100))


def test_restart_after_full_file_starts_next(tmp_path):
    c = Clock(epoch(2026, 10, 11))
    h = AlarmHistory(tmp_path, 100, now=c)
    for i in range(100):
        h.record('raised', {'id': i})
    AlarmHistory(tmp_path, 100, now=c).record('raised', {'id': 100})
    assert sorted(p.name for p in tmp_path.iterdir()) == ['alarms_2026-10-11_001.jsonl', 'alarms_2026-10-11_002.jsonl']


def test_new_day_starts_new_numbering_even_if_not_full(tmp_path):
    c = Clock(epoch(2026, 10, 11, 23, 59, 50))
    h = AlarmHistory(tmp_path, 100, now=c)
    h.record('raised', {'id': 1})
    c.t = epoch(2026, 10, 12, 0, 0, 5)
    h.record('raised', {'id': 2})
    assert sorted(p.name for p in tmp_path.iterdir()) == ['alarms_2026-10-11_001.jsonl', 'alarms_2026-10-12_001.jsonl']
    assert lines(tmp_path / 'alarms_2026-10-12_001.jsonl')[0]['saved_at'] == '2026-10-12 00:00:05'


def test_write_failure_does_not_raise_and_recovers(tmp_path):
    blocker = tmp_path / 'blocker'
    blocker.write_text('x')                     # 폴더 자리에 파일이 있어 저장할 수 없다
    h = AlarmHistory(blocker / 'sub', 100, now=Clock(epoch(2026, 10, 11)))
    assert h.record('raised', {'id': 1}) is False and h.record('raised', {'id': 2}) is False
    blocker.unlink()                            # 복구되면 다시 쓴다
    assert h.record('raised', {'id': 3}) is True
    assert [r['id'] for r in lines(blocker / 'sub' / 'alarms_2026-10-11_001.jsonl')] == [3]


def test_files_listing_and_read_newest_first(tmp_path):
    c = Clock(epoch(2026, 10, 11))
    h = AlarmHistory(tmp_path, 100, now=c)
    for i in range(120):
        h.record('raised', {'id': i})
    c.t = epoch(2026, 10, 12)
    for i in range(120, 125):
        h.record('raised', {'id': i})
    (tmp_path / 'notes.txt').write_text('무시')
    fs = h.files()
    assert [(f['name'], f['records']) for f in fs] == [('alarms_2026-10-12_001.jsonl', 5), ('alarms_2026-10-11_002.jsonl', 20), ('alarms_2026-10-11_001.jsonl', 100)]
    assert [r['id'] for r in h.read(limit=7)] == [124, 123, 122, 121, 120, 119, 118]   # 최신이 앞, 파일을 넘나든다
    assert [r['id'] for r in h.read('2026-10-11', limit=3)] == [119, 118, 117]
    assert h.read('2026-01-01') == []
    with pytest.raises(ValueError):
        h.read('11/10/2026')
    assert len(h.read(limit=99999)) == 125   # 상한(1000) 안에서 전부


def test_read_skips_broken_lines(tmp_path):
    h = AlarmHistory(tmp_path, 100, now=Clock(epoch(2026, 10, 11)))
    h.record('raised', {'id': 1})
    with open(tmp_path / 'alarms_2026-10-11_001.jsonl', 'a', encoding='utf-8') as f:
        f.write('{"id": 2, broken\n')   # 서버가 쓰는 도중 꺼졌다고 가정
    h.record('raised', {'id': 3})
    assert [r['id'] for r in h.read()] == [3, 1]


def test_missing_dir_lists_nothing(tmp_path):
    h = AlarmHistory(tmp_path / 'none', 100)
    assert h.files() == [] and h.read() == []


# ---- AlarmManager 와 연결 ----
def manager(tmp_path, chunk=100):
    c = Clock(epoch(2026, 10, 11))
    h = AlarmHistory(tmp_path, chunk, now=c)
    return AlarmManager(now=c, sink=h.record), h, c


def test_manager_saves_raised_repeated_cleared(tmp_path):
    a, h, _ = manager(tmp_path)
    a.raise_('ROBOT_OFFLINE', 'pinky1')
    a.raise_('ROBOT_OFFLINE', 'pinky1')
    a.clear('ROBOT_OFFLINE', 'pinky1')
    rows = list(reversed(h.read()))
    assert [(r['event'], r['count'], r['state']) for r in rows] == [('raised', 1, 'active'), ('repeated', 2, 'active'), ('cleared', 2, 'cleared')]
    assert all(r['code'] == 'ROBOT_OFFLINE' and r['robot'] == 'pinky1' and r['id'] == rows[0]['id'] for r in rows)


def test_manager_saves_info_rows_too(tmp_path):
    a, h, _ = manager(tmp_path)
    a.command_sent('pinky1', 'goto', ['P2', 'P3'])
    rows = h.read()
    assert len(rows) == 1 and rows[0]['event'] == 'raised' and rows[0]['level'] == 'info' and rows[0]['text'] == 'Command `goto P2 P3` sent to pinky1'


def test_monitor_is_capped_at_100_but_history_keeps_everything(tmp_path):
    a, h, _ = manager(tmp_path)
    for i in range(250):
        a.raise_('CMD_SENT', 'pinky1', cmd=f'start{i}')
    assert MONITOR_SIZE == 100 and len(a.snapshot()) == 100
    assert a.snapshot()[0]['text'].endswith('sent to pinky1') and 'start249' in a.snapshot()[0]['text']
    assert sum(f['records'] for f in h.files()) == 250 and [f['records'] for f in h.files()] == [50, 100, 100]


def test_failing_sink_never_breaks_alarms():
    def boom(event, alarm):
        raise RuntimeError('disk')
    a = AlarmManager(sink=boom)
    assert a.raise_('ROBOT_OFFLINE', 'pinky1') and a.clear('ROBOT_OFFLINE', 'pinky1')
    assert a.snapshot()[0]['state'] == 'cleared'
