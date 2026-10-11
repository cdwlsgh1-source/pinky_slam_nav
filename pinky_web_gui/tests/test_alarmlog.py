"""알람 이력 저장 테스트: chunk 건씩 파일 나누기(기본 1000), 파일 이름의 생성 시각, 이어 쓰기, 날짜 바뀜, 쓰기 실패, 읽기. 실행: .venv/bin/python -m pytest tests -q"""
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


def fill(h, c, start, n, step=1.0):
    """시계를 step 초씩 올리며 n 건을 쓴다 (파일 이름이 시각이라 건마다 시각이 달라야 롤오버 시각을 확인할 수 있다)."""
    for i in range(start, start + n):
        assert h.record('raised', {'id': i, 'code': 'CMD_SENT'})
        c.t += step


def test_default_chunk_is_1000():
    assert AlarmHistory('x').chunk == 1000


def test_records_are_split_by_chunk_and_new_file_is_named_by_creation_time(tmp_path):
    c = Clock(epoch(2026, 10, 11, 9, 0, 0))
    h = AlarmHistory(tmp_path, 100, now=c)
    fill(h, c, 0, 250)                           # 1초에 한 건: 100건째가 지난 시각에 다음 파일
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ['alarms_2026-10-11_090000.jsonl', 'alarms_2026-10-11_090140.jsonl', 'alarms_2026-10-11_090320.jsonl']
    assert [len(lines(tmp_path / n)) for n in names] == [100, 100, 50]
    assert lines(tmp_path / names[0])[0]['id'] == 0 and lines(tmp_path / names[1])[0]['id'] == 100   # 순서대로 이어서
    for n in names:                              # 파일 이름의 시각 = 그 파일 첫 줄의 저장 시각
        first = lines(tmp_path / n)[0]['saved_at']
        assert n == f"alarms_{first[:10]}_{first[11:].replace(':', '')}.jsonl"


def test_1000_lines_default_rolls_over_at_the_1001st_event(tmp_path):
    c = Clock(epoch(2026, 10, 11, 9, 0, 0))
    h = AlarmHistory(tmp_path, now=c)
    fill(h, c, 0, 1001)
    fs = h.files()
    assert len(fs) == 2 and fs[0]['records'] == 1 and fs[1]['records'] == 1000 and fs[1]['started'] == '09:00:00' and fs[0]['started'] == '09:16:40'   # 1초 간격이라 1001번째 이벤트(1000초째)의 시각


def test_same_second_second_file_gets_suffix(tmp_path):
    c = Clock(epoch(2026, 10, 11))
    h = AlarmHistory(tmp_path, 2, now=c)
    for i in range(5):
        h.record('raised', {'id': i})            # 시계가 안 움직여도 파일이 겹쳐 덮이지 않는다
    assert sorted(p.name for p in tmp_path.iterdir()) == ['alarms_2026-10-11_120000-2.jsonl', 'alarms_2026-10-11_120000-3.jsonl', 'alarms_2026-10-11_120000.jsonl']
    assert [r['id'] for r in reversed(h.read())] == [0, 1, 2, 3, 4]


def test_record_carries_save_date_and_event(tmp_path):
    h = AlarmHistory(tmp_path, 100, now=Clock(epoch(2026, 10, 11, 18, 32, 47)))
    h.record('cleared', {'id': 7, 'code': 'ROBOT_OFFLINE', 'robot': 'pinky1', 'text': 'pinky1 is offline'})
    row = lines(tmp_path / 'alarms_2026-10-11_183247.jsonl')[0]
    assert row['saved_at'] == '2026-10-11 18:32:47' and row['event'] == 'cleared'
    assert row['id'] == 7 and row['robot'] == 'pinky1' and isinstance(row['t'], float)


def test_korean_text_is_stored_readably(tmp_path):
    h = AlarmHistory(tmp_path, 100, now=Clock(epoch(2026, 10, 11)))
    h.record('raised', {'id': 1, 'detail': '구독자 없음'})
    assert '구독자 없음' in next(tmp_path.iterdir()).read_text(encoding='utf-8')


def test_restart_continues_unfilled_file(tmp_path):
    c = Clock(epoch(2026, 10, 11, 9, 0, 0))
    h = AlarmHistory(tmp_path, 100, now=c)
    fill(h, c, 0, 30)
    h2 = AlarmHistory(tmp_path, 100, now=c)   # 서버를 다시 켠 것
    fill(h2, c, 30, 100)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 2 and names[0] == 'alarms_2026-10-11_090000.jsonl'
    assert [len(lines(tmp_path / n)) for n in names] == [100, 30]
    assert [r['id'] for r in lines(tmp_path / names[0])] == list(range(100))


def test_restart_after_full_file_starts_next(tmp_path):
    c = Clock(epoch(2026, 10, 11, 9, 0, 0))
    h = AlarmHistory(tmp_path, 100, now=c)
    fill(h, c, 0, 100)
    AlarmHistory(tmp_path, 100, now=c).record('raised', {'id': 100})
    assert len(list(tmp_path.iterdir())) == 2


def test_new_day_starts_new_file_even_if_not_full(tmp_path):
    c = Clock(epoch(2026, 10, 11, 23, 59, 50))
    h = AlarmHistory(tmp_path, 100, now=c)
    h.record('raised', {'id': 1})
    c.t = epoch(2026, 10, 12, 0, 0, 5)
    h.record('raised', {'id': 2})
    assert sorted(p.name for p in tmp_path.iterdir()) == ['alarms_2026-10-11_235950.jsonl', 'alarms_2026-10-12_000005.jsonl']
    assert lines(tmp_path / 'alarms_2026-10-12_000005.jsonl')[0]['saved_at'] == '2026-10-12 00:00:05'


def test_write_failure_does_not_raise_and_recovers(tmp_path):
    blocker = tmp_path / 'blocker'
    blocker.write_text('x')                     # 폴더 자리에 파일이 있어 저장할 수 없다
    h = AlarmHistory(blocker / 'sub', 100, now=Clock(epoch(2026, 10, 11)))
    assert h.record('raised', {'id': 1}) is False and h.record('raised', {'id': 2}) is False
    blocker.unlink()                            # 복구되면 다시 쓴다
    assert h.record('raised', {'id': 3}) is True
    assert [r['id'] for r in lines(next((blocker / 'sub').iterdir()))] == [3]


def test_files_listing_and_read_newest_first(tmp_path):
    c = Clock(epoch(2026, 10, 11, 9, 0, 0))
    h = AlarmHistory(tmp_path, 100, now=c)
    fill(h, c, 0, 120)
    c.t = epoch(2026, 10, 12, 0, 0, 0)
    fill(h, c, 120, 5)
    (tmp_path / 'notes.txt').write_text('무시')
    fs = h.files()
    assert [(f['name'], f['started'], f['records']) for f in fs] == [('alarms_2026-10-12_000000.jsonl', '00:00:00', 5),
                                                                    ('alarms_2026-10-11_090140.jsonl', '09:01:40', 20),
                                                                    ('alarms_2026-10-11_090000.jsonl', '09:00:00', 100)]
    assert all(f['seq'] is None for f in fs)
    assert [r['id'] for r in h.read(limit=7)] == [124, 123, 122, 121, 120, 119, 118]   # 최신이 앞, 파일을 넘나든다
    assert [r['id'] for r in h.read('2026-10-11', limit=3)] == [119, 118, 117]
    assert h.read('2026-01-01') == []
    with pytest.raises(ValueError):
        h.read('11/10/2026')
    assert len(h.read(limit=99999)) == 125   # 상한(1000) 안에서 전부


def test_old_numbered_files_are_still_listed_read_and_continued(tmp_path):
    """이전 버전이 만든 alarms_<날짜>_NNN.jsonl 도 읽고, 오늘 마지막 번호 파일이 안 찼으면 이어서 쓴다. 같은 날 번호 파일이 시각 파일보다 앞이다."""
    for n, ids in (('001', range(0, 3)), ('002', range(3, 5))):
        (tmp_path / f'alarms_2026-10-11_{n}.jsonl').write_text(''.join(json.dumps({'id': i}) + '\n' for i in ids), encoding='utf-8')
    c = Clock(epoch(2026, 10, 11, 9, 0, 0))
    h = AlarmHistory(tmp_path, 5, now=c)
    assert [(f['name'], f['seq'], f['started']) for f in h.files()] == [('alarms_2026-10-11_002.jsonl', 2, None), ('alarms_2026-10-11_001.jsonl', 1, None)]
    h.record('raised', {'id': 5})                # 002 는 2건이라 이어서 쓴다
    assert len(lines(tmp_path / 'alarms_2026-10-11_002.jsonl')) == 3
    fill(h, c, 6, 3)                              # 5건이 차면 시각 이름의 새 파일
    names = [f['name'] for f in h.files()]
    assert names[0].startswith('alarms_2026-10-11_09') and names[1:] == ['alarms_2026-10-11_002.jsonl', 'alarms_2026-10-11_001.jsonl']
    assert [r['id'] for r in h.read(limit=4)] == [8, 7, 6, 5]


def test_read_skips_broken_lines(tmp_path):
    h = AlarmHistory(tmp_path, 100, now=Clock(epoch(2026, 10, 11)))
    h.record('raised', {'id': 1})
    with open(next(tmp_path.iterdir()), 'a', encoding='utf-8') as f:
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


def test_manager_saves_raised_acked_cleared_in_order(tmp_path):
    a, h, c = manager(tmp_path)
    a.raise_('ROBOT_OFFLINE', 'pinky1')
    c.t += 1
    a.ack(user='oper')
    c.t += 1
    a.clear('ROBOT_OFFLINE', 'pinky1')
    rows = list(reversed(h.read()))          # 오래된 것부터: 일어난 순서 그대로 쌓인다
    assert [(r['event'], r['state'], r['acked_by']) for r in rows] == [('raised', 'active', None), ('acked', 'active', 'oper'), ('cleared', 'cleared', 'oper')]
    assert all(r['code'] == 'ROBOT_OFFLINE' and r['robot'] == 'pinky1' and r['id'] == rows[0]['id'] for r in rows)
    assert rows[1]['acked_at'] == rows[1]['t'] and [r['t'] for r in rows] == sorted(r['t'] for r in rows)


def test_record_has_16_keys_and_repeats_are_appended_in_order(tmp_path):
    a, h, _ = manager(tmp_path)
    a.raise_('ROBOT_OFFLINE', 'pinky1')
    a.raise_('ROBOT_OFFLINE', 'pinky1')
    rows = list(reversed(h.read()))
    assert list(rows[0]) == ['saved_at', 't', 'event', 'id', 'code', 'level', 'robot', 'text', 'detail', 'state',
                             't_raised', 't_last', 't_cleared', 'count', 'acked_by', 'acked_at']
    assert [(r['event'], r['count']) for r in rows] == [('raised', 1), ('repeated', 2)]


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
