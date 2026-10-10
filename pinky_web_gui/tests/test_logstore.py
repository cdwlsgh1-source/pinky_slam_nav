"""로그 저장소: 번호, 소스별 상한, 레벨 추정, 로깅 핸들러."""
import logging

from backend.logstore import ERROR, GUI, INFO, WARN, LogStore, StoreHandler, guess_level


def test_guess_level_ros_python_and_traceback():
    assert guess_level('[INFO] [1.0] [nav2]: ok') == INFO
    assert guess_level('[WARN] [1.0] [nav2]: slow') == WARN
    assert guess_level('[ERROR] [1.0] [map_server]: bad file') == ERROR
    assert guess_level('[FATAL] [x]: dead') == ERROR
    assert guess_level('2026-10-10 11:00:00,000 WARNING backend: x') == WARN
    assert guess_level('Traceback (most recent call last):') == ERROR
    assert guess_level('RuntimeError: boom') == ERROR
    assert guess_level('그냥 출력') == INFO


def test_numbers_increase_and_since_returns_only_new():
    s = LogStore()
    s.add('a', 'one'); s.add('b', 'two'); s.add('a', 'three')
    rows, last = s.read(0)
    assert [r['text'] for r in rows] == ['one', 'two', 'three'] and last == rows[-1]['n']
    s.add('b', 'four')
    rows2, last2 = s.read(last)
    assert [r['text'] for r in rows2] == ['four'] and last2 > last
    assert s.read(last2) == ([], last2)


def test_per_source_cap_does_not_evict_other_sources():
    s = LogStore(per_source=3)
    s.add('quiet', 'keep')
    for i in range(10):
        s.add('noisy', f'n{i}')
    texts = [r['text'] for r in s.read(0)[0]]
    assert 'keep' in texts and texts.count('n9') == 1 and 'n0' not in texts and len(texts) == 4


def test_blank_lines_skipped_and_long_lines_cut():
    s = LogStore()
    s.add('a', '   '); s.add('a', 'x' * 5000)
    rows = s.read(0)[0]
    assert len(rows) == 1 and len(rows[0]['text']) == 2000


def test_clear_source():
    s = LogStore()
    s.add('a', '1'); s.add('b', '2')
    s.clear('a')
    assert [r['src'] for r in s.read(0)[0]] == ['b']


def test_handler_maps_logging_levels_and_exceptions():
    s = LogStore()
    lg = logging.getLogger('backend.test_logstore'); lg.setLevel(logging.INFO)
    h = StoreHandler(s); lg.addHandler(h)
    try:
        lg.info('hello'); lg.warning('careful')
        try:
            raise ValueError('x')
        except ValueError:
            lg.exception('failed')
    finally:
        lg.removeHandler(h)
    rows = s.read(0)[0]
    assert {r['src'] for r in rows} == {GUI}
    lv = {r['text'].split(': ', 1)[-1]: r['lvl'] for r in rows if r['text'].startswith('backend.test_logstore')}
    assert lv['hello'] == 'INFO' and lv['careful'] == 'WARN' and lv['failed'] == 'ERROR'
    assert any('ValueError' in r['text'] and r['lvl'] == 'ERROR' for r in rows)
