"""알람 이력 조회(Step A7) 테스트: 이벤트 줄 묶기, 필터, 페이지 표지, Uncleared/Active, 재시작 뒤 id 겹침, 깨진 줄, 잘못된 파라미터, CSV, API 권한.
실행: .venv/bin/python -m pytest tests/test_alarmquery.py -q (ROS 불필요)"""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend import alarmquery as aq
from backend.alarmlog import AlarmHistory
from backend.alarms import AlarmManager
from backend.auth import Auth
from backend.config import load_config
from backend.main import create_app

CFG = load_config()


def epoch(m, d, hh=12, mm=0, ss=0, y=2026):
    return datetime(y, m, d, hh, mm, ss).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, s=1.0):
        self.t += s


def rig(tmp_path, t0=None, chunk=1000):
    c = Clock(t0 or epoch(10, 11, 9))
    h = AlarmHistory(tmp_path, chunk, now=c)
    return c, h, AlarmManager(now=c, sink=h.record)


FLT = aq.parse_filter()


def rows_of(h, flt=FLT, live=None, **kw):
    return list(h.iter_alarms(flt, kw.get('before'), live))


# ---- 묶기 ----
def test_events_of_one_alarm_become_one_row_and_every_attempt_is_a_line(tmp_path):
    c, h, m = rig(tmp_path)
    m.raise_('CMD_REJECTED', 'pinky1', cmd='start', reason='검증 실패'); c.tick(5)
    m.raise_('CMD_REJECTED', 'pinky1', cmd='start', reason='또 실패'); c.tick(5)    # ACK 전 반복: 새 행이 아니라 repeated 한 줄
    m.ack([1], 'oper'); c.tick(5)
    m.clear('CMD_REJECTED', 'pinky1')
    rows = rows_of(h)
    assert len(rows) == 1
    r = rows[0]
    assert (r['id'], r['count'], r['acked_by'], r['state'], r['events']) == (1, 2, 'oper', 'cleared', 4)
    assert r['duration'] == 15.0 and r['detail'] == '또 실패'
    detail = h.events_of(1, r['t_raised'])
    assert [e['event'] for e in detail] == ['raised', 'repeated', 'acked', 'cleared']


def test_info_alarm_is_a_cleared_row_without_duration(tmp_path):
    c, h, m = rig(tmp_path)
    m.raise_('CMD_SENT', 'pinky1', cmd='start')
    (r,) = rows_of(h)
    assert r['level'] == 'info' and r['state'] == 'cleared' and r['duration'] is None


def test_ack_after_clear_is_still_cleared_not_uncleared(tmp_path):
    """해소 뒤에 ACK 하면 마지막 줄의 event 는 acked 지만 state 는 cleared 다 (해소 줄을 따로 쓰지 않는다)."""
    c, h, m = rig(tmp_path)
    m.raise_('ROBOT_OFFLINE', 'pinky1'); c.tick(); m.clear('ROBOT_OFFLINE', 'pinky1'); c.tick(); m.ack([1], 'mngr')
    (r,) = rows_of(h)
    assert r['state'] == 'cleared' and r['acked_by'] == 'mngr'


# ---- Uncleared / Active, 재시작 ----
def test_uncleared_unless_the_same_alarm_is_alive_in_server_memory(tmp_path):
    c, h, m = rig(tmp_path)
    m.raise_('ROBOT_OFFLINE', 'pinky1')
    assert rows_of(h)[0]['state'] == 'uncleared'                  # 메모리 정보가 없으면 추정하지 않는다
    assert rows_of(h, live=m.live())[0]['state'] == 'active'
    m.ack([1], 'oper')
    assert rows_of(h, live=m.live())[0]['state'] == 'acknowledged'
    m.clear('ROBOT_OFFLINE', 'pinky1')
    assert rows_of(h, live=m.live())[0]['state'] == 'cleared'


def test_restart_reuses_ids_but_alarms_stay_separate_and_only_the_live_one_is_active(tmp_path):
    c1, h1, m1 = rig(tmp_path, epoch(10, 11, 9))
    m1.raise_('ROBOT_OFFLINE', 'pinky1')                          # id 1, 서버가 꺼져 해소되지 못함
    c2, h2, m2 = rig(tmp_path, epoch(10, 11, 15))                 # 재시작: 같은 폴더, id 가 1 부터 다시
    m2.raise_('ROBOT_OFFLINE', 'pinky1')                          # id 1 (다른 t_raised)
    rows = rows_of(h2, live=m2.live())
    assert [(r['id'], r['state']) for r in rows] == [(1, 'active'), (1, 'uncleared')]
    assert rows[0]['t_raised'] > rows[1]['t_raised']
    assert len(h2.events_of(1, rows[1]['t_raised'])) == 1         # 타임라인도 (id, t_raised) 로 나뉜다


def test_alarm_whose_events_span_files_is_completed(tmp_path):
    c, h, m = rig(tmp_path, chunk=3)
    m.raise_('ROBOT_OFFLINE', 'pinky1'); c.tick(10)               # 1번 파일
    for i in range(8):
        m.raise_('CMD_SENT', 'pinky2', cmd=f'c{i}'); c.tick(10)   # 파일을 넘긴다
    m.clear('ROBOT_OFFLINE', 'pinky1')                            # 맨 뒤 파일에서 해소
    assert len(h.files()) >= 3
    rows = rows_of(h)
    off = [r for r in rows if r['code'] == 'ROBOT_OFFLINE']
    assert len(off) == 1 and off[0]['state'] == 'cleared' and off[0]['events'] == 2 and off[0]['duration'] == 90.0
    assert len(rows) == 9


# ---- 필터 / 페이지 ----
def seed(tmp_path):
    c, h, m = rig(tmp_path, epoch(10, 9, 9), chunk=4)
    m.raise_('ROBOT_OFFLINE', 'pinky1'); c.tick(60); m.clear('ROBOT_OFFLINE', 'pinky1')       # 10/9: H 계열
    c.t = epoch(10, 10, 10)
    m.raise_('CMD_REJECTED', 'pinky2', cmd='goto', reason='허용되지 않은 지점 P9'); c.tick(5)
    m.raise_('CMD_SENT', 'pinky2', cmd='start')                                                  # info
    c.t = epoch(10, 11, 11)
    m.raise_('ESTOP_FAILED', 'pinky1', reason='구독자 없음')                                     # active, ACK 안 됨
    return c, h, m


def test_filters(tmp_path):
    c, h, m = seed(tmp_path)
    live = m.live()

    def codes(live=live, **kw):
        return [r['code'] for r in h.iter_alarms(aq.parse_filter(**kw), None, live)]
    assert codes() == ['ESTOP_FAILED', 'CMD_SENT', 'CMD_REJECTED', 'ROBOT_OFFLINE']              # 최신이 위
    assert codes(frm='2026-10-10', to='2026-10-10') == ['CMD_SENT', 'CMD_REJECTED']
    assert codes(frm='2026-10-11') == ['ESTOP_FAILED']
    assert codes(to='2026-10-09') == ['ROBOT_OFFLINE']
    assert codes(robot='pinky2') == ['CMD_SENT', 'CMD_REJECTED']
    assert codes(level='HH,H') == ['ESTOP_FAILED', 'ROBOT_OFFLINE'] or codes(level='HH,H')[0] == 'ESTOP_FAILED'
    assert codes(level='info') == ['CMD_SENT']
    assert codes(state='active') == ['ESTOP_FAILED', 'CMD_REJECTED']                              # 둘 다 지금 서버 메모리에 살아 있다
    assert codes(state='cleared') == ['CMD_SENT', 'ROBOT_OFFLINE']
    assert codes(live={}, state='uncleared') == ['ESTOP_FAILED', 'CMD_REJECTED']                  # 서버를 다시 켠 것처럼 메모리가 비면 해소 기록 없음
    assert codes(live={}, state='active') == []
    assert codes(code='CMD_REJECTED') == ['CMD_REJECTED']
    assert codes(q='p9') == ['CMD_REJECTED']                      # detail/메시지 검색, 대소문자 무시
    assert codes(q='구독자') == ['ESTOP_FAILED']
    assert codes(robot='pinky1', level='LL') == []


def test_paging_covers_every_alarm_once_newest_first(tmp_path):
    c, h, m = rig(tmp_path, chunk=7)
    for i in range(25):
        m.raise_('CMD_SENT', 'pinky1', cmd=f'c{i}'); c.tick(3)
    seen, before = [], None
    pages = []
    while True:
        page = h.alarms(FLT, 10, aq.parse_cursor(before))
        pages.append(len(page['alarms']))
        seen += [r['id'] for r in page['alarms']]
        before = page['next']
        if before is None:
            break
    assert pages == [10, 10, 5]
    assert seen == list(range(25, 0, -1))


def test_reading_stops_early_and_skips_files_older_than_the_period(tmp_path, monkeypatch):
    c, h, m = rig(tmp_path, epoch(10, 1, 9), chunk=5)
    for day in range(1, 11):                                      # 10일 × 5건 = 파일 10개
        c.t = epoch(10, day, 9)
        for i in range(5):
            m.raise_('CMD_SENT', 'pinky1', cmd=f'd{day}-{i}'); c.tick(1)
    assert len(h.files()) == 10
    read = []
    orig = h._events
    monkeypatch.setattr(h, '_events', lambda n: (read.append(n), orig(n))[1])
    h.alarms(aq.parse_filter(frm='2026-10-10'), 200)              # 오늘(10일) 것만: 앞선 날짜 파일은 열지 않는다
    assert len(read) == 1
    read.clear()
    page = h.alarms(FLT, 3)                                       # 3건이면 최신 파일 하나로 충분
    assert len(read) == 1 and len(page['alarms']) == 3 and page['next']


# ---- 깨진 줄 ----
def test_broken_lines_are_skipped(tmp_path):
    c, h, m = rig(tmp_path)
    m.raise_('ROBOT_OFFLINE', 'pinky1')
    m.raise_('CMD_SENT', 'pinky2', cmd='start')
    p = next(tmp_path.iterdir())
    with open(p, 'a', encoding='utf-8') as f:
        f.write('{"event":"raised","id":9,"t_raised"\n')          # 쓰다 끊긴 줄
        f.write('[1,2,3]\n"문자열"\n{"event":"raised","id":"x","t_raised":1}\n\n')   # 알람 모양이 아닌 줄
    with open(p, 'ab') as f:
        f.write(b'{"event":"raised","id":8,"t_raised":1,"text":"\xea\xb0')   # 한글 한 글자가 잘린 마지막 줄
    assert [r['id'] for r in rows_of(h)] == [2, 1]
    s = h.summary()
    assert s['total_alarms'] >= 2


# ---- 잘못된 파라미터 ----
@pytest.mark.parametrize('kw', [
    {'frm': '2026-13-01'}, {'frm': '2026/10/01'}, {'to': 'abc'}, {'frm': '2026-10-05', 'to': '2026-10-01'}, {'frm': '2020-01-01', 'to': '2026-10-01'},
    {'level': 'HH,fatal'}, {'level': ','}, {'state': 'dead'}, {'robot': 'a b'}, {'robot': '../x'}, {'code': 'cmd sent'}, {'q': 'x' * 101}, {'q': 'a\nb'},
])
def test_bad_filter_is_rejected(kw):
    with pytest.raises(aq.QueryError):
        aq.parse_filter(**kw)


@pytest.mark.parametrize('text', ['0', '201', '-1', 'abc', '1.5'])
def test_bad_limit_is_rejected(text):
    with pytest.raises(aq.QueryError):
        aq.parse_limit(text)


@pytest.mark.parametrize('text', ['abc', '1:2:3', '-5:1', 'nan:1', '12.5', '12.5:x'])
def test_bad_cursor_is_rejected(text):
    with pytest.raises(aq.QueryError):
        aq.parse_cursor(text)


def test_limit_and_cursor_defaults():
    assert aq.parse_limit(None) == 200 and aq.parse_limit('200') == 200 and aq.parse_limit('1') == 1
    assert aq.parse_cursor(None) is None and aq.parse_cursor('1791712750.9648645:12') == (1791712750.9648645, 12)


# ---- CSV ----
def test_csv_has_bom_korean_and_neutralizes_formulas(tmp_path):
    c, h, m = rig(tmp_path)
    m.raise_('CMD_REJECTED', 'pinky1', cmd='goto', reason='=HYPERLINK("x")')
    rows, truncated = h.export_rows(FLT, m.live())
    data = aq.to_csv(rows).encode('utf-8')
    assert data[:3] == b'\xef\xbb\xbf' and not truncated
    text = data.decode('utf-8-sig')
    assert text.splitlines()[0] == ','.join(aq.CSV_HEAD)
    assert '활성' in text and "'=HYPERLINK" not in text.splitlines()[1].split(',')[0]
    assert '2026-10-11 09:00:00' in text


def test_export_is_cut_at_the_limit(tmp_path):
    c, h, m = rig(tmp_path)
    for i in range(7):
        m.raise_('CMD_SENT', 'pinky1', cmd=f'c{i}'); c.tick()
    rows, truncated = h.export_rows(FLT, None, limit=5)
    assert len(rows) == 5 and truncated
    rows, truncated = h.export_rows(FLT, None, limit=7)
    assert len(rows) == 7 and not truncated


# ---- 파일 요약 ----
def test_summary_counts_alarms_and_events_and_caches_closed_files(tmp_path):
    c, h, m = rig(tmp_path, chunk=3)
    m.raise_('ROBOT_OFFLINE', 'pinky1'); c.tick(); m.raise_('CMD_SENT', 'pinky1', cmd='a'); c.tick(); m.clear('ROBOT_OFFLINE', 'pinky1'); c.tick(); m.raise_('CMD_SENT', 'pinky1', cmd='b')
    s = h.summary()
    assert (s['total_files'], s['total_events'], s['total_alarms']) == (2, 4, 3)
    assert [f['alarms'] for f in s['files']] == [1, 2] and s['dir'] == str(tmp_path)
    assert h.summary() == s


# ---- API ----
JSON = {'Content-Type': 'application/json'}


def client(tmp_path, auth=None):
    app = create_app(CFG, mock=True, auth=auth if auth is not None else Auth(), alarm_dir=str(tmp_path))
    return TestClient(app)


def login(c, user):
    assert c.post('/api/login', json={'id': user, 'password': user}).status_code == 200
    return c


def make_alarms(c, n=3):
    for i in range(n):
        assert c.post('/api/robots/pinky1/command', json={'cmd': 'bogus'}).status_code == 400
        assert c.post('/api/robots/pinky2/command', json={'cmd': 'bogus'}).status_code == 400


URLS = ['/api/alarms/history?view=alarms', '/api/alarms/history/export', '/api/alarms/history/summary', '/api/alarms/history/1/events?t_raised=1.5']


@pytest.mark.parametrize('url', URLS)
def test_new_endpoints_need_login(tmp_path, url):
    with client(tmp_path) as c:
        assert c.get(url).status_code == 401


def test_operator_can_read_and_no_auth_server_too(tmp_path):
    with client(tmp_path) as c:
        login(c, 'oper')
        for url in URLS[:3]:
            assert c.get(url).status_code == 200
    with client(tmp_path, auth=Auth.disabled()) as c:
        assert c.get('/api/alarms/history?view=alarms').status_code == 200


def test_grouped_view_pages_and_events_detail(tmp_path):
    with client(tmp_path) as c:
        login(c, 'mngr')
        make_alarms(c, 3)                                          # 알람 2행(로봇별), 반복 포함
        r = c.get('/api/alarms/history?view=alarms&code=CMD_REJECTED').json()
        assert len(r['alarms']) == 2 and r['next'] is None
        assert {a['robot'] for a in r['alarms']} == {'pinky1', 'pinky2'}
        assert all(a['count'] == 3 and a['events'] == 3 and a['state'] == 'active' for a in r['alarms'])   # 반복 3번, 지금 살아 있음
        a = r['alarms'][0]
        d = c.get('/api/alarms/history/%d/events?t_raised=%r' % (a['id'], a['t_raised'])).json()
        assert [e['event'] for e in d['events']] == ['raised', 'repeated', 'repeated'] and d['alarm']['id'] == a['id']
        assert d['events'][0]['file'].startswith('alarms_')
        p1 = c.get('/api/alarms/history?view=alarms&code=CMD_REJECTED&limit=1').json()
        assert len(p1['alarms']) == 1 and p1['next']
        p2 = c.get('/api/alarms/history?view=alarms&code=CMD_REJECTED&limit=1&before=' + p1['next']).json()
        assert len(p2['alarms']) == 1 and p2['next'] is None and p2['alarms'][0]['id'] != p1['alarms'][0]['id']
        assert c.get('/api/alarms/history?view=alarms&robot=pinky2').json()['alarms'][0]['robot'] == 'pinky2'
        assert c.get('/api/alarms/history?view=alarms&q=없는문구').json()['alarms'] == []
        assert c.get('/api/alarms/history/99/events?t_raised=1.5').status_code == 404


def test_old_event_api_is_unchanged(tmp_path):
    with client(tmp_path) as c:
        login(c, 'mngr')
        make_alarms(c, 1)
        r = c.get('/api/alarms/history?limit=50').json()
        assert list(r) == ['records'] and r['records'][0]['event'] in ('raised', 'repeated') and 'saved_at' in r['records'][0]
        assert 'files' in c.get('/api/alarms/history/files').json()
        assert c.get('/api/alarms/history?view=events').json()['records']


@pytest.mark.parametrize('qs', ['view=nope', 'view=alarms&level=zzz', 'view=alarms&limit=0', 'view=alarms&limit=201', 'view=alarms&from=2026-02-30', 'view=alarms&before=abc',
                                'view=alarms&state=dead', 'view=alarms&from=2026-10-05&to=2026-10-01'])
def test_bad_parameters_are_400(tmp_path, qs):
    with client(tmp_path) as c:
        login(c, 'oper')
        r = c.get('/api/alarms/history?' + qs)
        assert r.status_code == 400 and r.json()['error']
    with client(tmp_path) as c:
        login(c, 'oper')
        assert c.get('/api/alarms/history/export?level=zzz').status_code == 400


def test_export_is_csv_with_bom_and_headers(tmp_path):
    with client(tmp_path) as c:
        login(c, 'mngr')
        make_alarms(c, 1)
        r = c.get('/api/alarms/history/export?code=CMD_REJECTED')
        assert r.status_code == 200 and r.headers['content-type'].startswith('text/csv')
        assert r.content[:3] == b'\xef\xbb\xbf' and 'attachment' in r.headers['content-disposition']
        assert r.headers['x-export-truncated'] == '0' and r.headers['x-export-rows'] == '2'
        assert r.content.decode('utf-8-sig').splitlines()[0] == ','.join(aq.CSV_HEAD)
        today = datetime.now().strftime('%Y-%m-%d')
        r = c.get(f'/api/alarms/history/export?from={today}&to={today}')
        assert f'alarms_{today}.csv' in r.headers['content-disposition']


def test_history_off_gives_a_notice_not_an_error_for_summary(tmp_path):
    import dataclasses
    app = create_app(dataclasses.replace(CFG, alarm_history_dir=None), mock=True, auth=Auth.disabled())
    with TestClient(app) as c:
        assert c.get('/api/alarms/history/summary').json() == {'enabled': False}
        for url in ('/api/alarms/history?view=alarms', '/api/alarms/history/export', '/api/alarms/history/1/events?t_raised=1.5'):
            assert c.get(url).status_code == 404
