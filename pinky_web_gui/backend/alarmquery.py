"""알람 이력 조회의 순수 함수들 (Step A7). 파일 읽기는 alarmlog.AlarmHistory 가 하고, 여기는 줄을 알람 단위로 묶고 거르고 CSV 로 만든다.

이력 파일은 이벤트(raised/repeated/acked/cleared)마다 한 줄이고, 줄마다 그 시점의 알람 전체가 들어 있다.
  그래서 한 알람의 마지막 이벤트 줄이 곧 최종 상태다. 알람은 `(id, t_raised)` 쌍으로 묶는다 (서버를 다시 켜면 id 가 1 부터 다시 시작한다).
  서버가 꺼져 해소되지 못한 알람은 해소 줄이 없다 (이력 파일 형식은 바꾸지 않는다, PRD 결정 15). 그 알람이 지금도 살아 있는지는
  서버 메모리의 active 알람 `(id, t_raised)` 와 일치할 때만 `active`, 아니면 `uncleared` 다. 추정하지 않는다.
rclpy 와 무관하며 시계를 읽지 않는다 (오늘 날짜가 필요하면 호출부가 넘긴다).
"""
import csv
import io
import math
import re
from datetime import datetime, timedelta

from .alarmcatalog import INFO, LEVELS

EVENTS = ('raised', 'repeated', 'acked', 'cleared')
STATES = ('active', 'acknowledged', 'cleared', 'uncleared')   # 지금 active / active 인데 ACK 됨 / 해소됨 / 해소 기록 없음
PAGE_MAX = 200          # 한 번에 돌려주는 최대 행 수 (FRA7-5)
EXPORT_MAX = 10000      # CSV 로 내려받는 최대 행 수 (FRA7-10)
RANGE_MAX_DAYS = 366    # 기간(from~to) 상한
Q_MAX = 100             # 검색어 최대 글자 수

DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
ROBOT_RE = re.compile(r'^[\w.\-]{1,40}$')
CODE_RE = re.compile(r'^[A-Z0-9_]{1,40}$')
CURSOR_RE = re.compile(r'^(\d+(?:\.\d+)?):(\d+)$')

STATE_TEXT = {'active': '활성', 'acknowledged': '확인됨', 'cleared': '해소', 'uncleared': '해소 기록 없음'}   # CSV 의 상태 칸 (화면 8-5절 문구와 같다)
CSV_HEAD = ('발생', '로봇', '레벨', '코드', '메시지', '상태', '횟수', '확인 계정', '확인 시각', '해소', '지속 시간(초)')


class QueryError(ValueError):
    """잘못된 조회 조건. 메시지는 한국어이며 API 가 그대로 400 으로 돌려준다."""


# ---- 조건 해석 ----
def _day(text, name):
    if not DAY_RE.match(text):
        raise QueryError(f'{name} 은 YYYY-MM-DD 형식이어야 합니다')
    try:
        return datetime.strptime(text, '%Y-%m-%d').date()
    except ValueError:
        raise QueryError(f'{name} 은 달력에 있는 날짜여야 합니다') from None


def _split(text):
    return [p for p in (text or '').split(',') if p]


def parse_filter(frm=None, to=None, robot=None, level=None, state=None, code=None, q=None):
    """쿼리 문자열 조건 -> 검사를 마친 조건 dict. 모든 항목은 선택이고 없으면 그 조건은 걸지 않는다.
    기간은 서버 PC 현지 날짜(시작일 0시 이상, 끝일 다음 날 0시 미만)로 알람이 **발생한** 시각에 건다."""
    f = {'day_from': None, 'day_to': None, 't_from': None, 't_to': None, 'robot': None, 'levels': None, 'states': None, 'code': None, 'q': None}
    d_from = _day(frm, 'from') if frm else None
    d_to = _day(to, 'to') if to else None
    if d_from and d_to:
        if d_from > d_to:
            raise QueryError('from 은 to 보다 늦을 수 없습니다')
        if (d_to - d_from).days + 1 > RANGE_MAX_DAYS:
            raise QueryError(f'기간은 {RANGE_MAX_DAYS}일까지입니다')
    if d_from:
        f['day_from'] = d_from.isoformat()
        f['t_from'] = datetime.combine(d_from, datetime.min.time()).timestamp()
    if d_to:
        f['day_to'] = d_to.isoformat()
        f['t_to'] = datetime.combine(d_to + timedelta(days=1), datetime.min.time()).timestamp()
    if robot:
        if not ROBOT_RE.match(robot):
            raise QueryError('robot 이름이 올바르지 않습니다')
        f['robot'] = robot
    if level:
        levels = _split(level)
        bad = [x for x in levels if x not in LEVELS]
        if bad or not levels:
            raise QueryError('level 은 ' + ', '.join(LEVELS) + ' 중에서 쉼표로 고릅니다')
        f['levels'] = set(levels)
    if state:
        states = _split(state)
        bad = [x for x in states if x not in STATES]
        if bad or not states:
            raise QueryError('state 는 ' + ', '.join(STATES) + ' 중에서 쉼표로 고릅니다')
        f['states'] = set(states)
    if code:
        if not CODE_RE.match(code):
            raise QueryError('code 가 올바르지 않습니다')
        f['code'] = code
    if q:
        if len(q) > Q_MAX or any(ord(c) < 32 for c in q):
            raise QueryError(f'검색어는 줄바꿈 없이 {Q_MAX}글자까지입니다')
        f['q'] = q.lower()
    return f


def parse_limit(text, default=PAGE_MAX, top=PAGE_MAX):
    """limit 검사: 비어 있으면 default, 1..top 의 정수가 아니면 QueryError."""
    if text is None or text == '':
        return default
    try:
        n = int(text)
    except (TypeError, ValueError):
        raise QueryError('limit 은 정수여야 합니다') from None
    if n < 1 or n > top:
        raise QueryError(f'limit 은 1~{top} 이어야 합니다')
    return n


def parse_cursor(text):
    """다음 페이지 표지 '발생시각:id' -> (t_raised, id). 없으면 None."""
    if text is None or text == '':
        return None
    m = CURSOR_RE.match(text)
    if not m or not math.isfinite(float(m.group(1))):
        raise QueryError('before 표지가 올바르지 않습니다')
    return (float(m.group(1)), int(m.group(2)))


def cursor_of(row):
    return f"{row['t_raised']!r}:{row['id']}"


# ---- 줄 -> 알람 ----
def valid_event(row):
    """이력 줄로 쓸 수 있는가: 객체이고 알람을 알아볼 키(id, t_raised)와 알려진 event 를 갖는다. 아니면 건너뛴다 (깨진 줄)."""
    if not isinstance(row, dict) or row.get('event') not in EVENTS:
        return False
    i, t = row.get('id'), row.get('t_raised')
    return isinstance(i, int) and not isinstance(i, bool) and isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t)


def key_of(row):
    return (row['id'], row['t_raised'])


def state_of(last, live):
    """알람 하나의 상태. last: 마지막 이벤트 줄, live: 지금 서버 메모리에서 active 인 같은 (id, t_raised) 알람({'acked_by'}) 또는 None.
    해소 여부는 줄의 state/t_cleared 로 본다 (해소 뒤에 ACK 하면 마지막 줄이 event=acked 지만 state=cleared 다)."""
    if live is not None:
        return 'acknowledged' if live.get('acked_by') is not None else 'active'
    if last.get('state') == 'cleared' or last.get('t_cleared') is not None:
        return 'cleared'
    return 'uncleared'


def build(events, live=None):
    """한 알람의 이벤트 줄(일어난 순서)을 화면 한 행으로. live: {(id, t_raised): {'acked_by'}} (서버 메모리의 active 알람)."""
    first, last = events[0], events[-1]
    live_one = (live or {}).get(key_of(last))
    state = state_of(last, live_one)
    t_cleared = last.get('t_cleared')
    duration = None
    if state == 'cleared' and t_cleared is not None and last.get('level') != INFO:
        duration = round(max(0.0, t_cleared - last['t_raised']), 1)
    return {
        'id': last['id'], 't_raised': last['t_raised'], 'code': last.get('code', ''), 'level': last.get('level', ''), 'robot': last.get('robot'),
        'text': last.get('text', ''), 'detail': last.get('detail', ''), 'state': state, 'count': last.get('count', 1),
        'acked_by': last.get('acked_by'), 'acked_at': last.get('acked_at'), 't_last': last.get('t_last', last['t_raised']),
        't_cleared': t_cleared, 'duration': duration, 'events': len(events), 'file': first.get('file'),
    }


def match(row, f):
    """build() 가 만든 행이 조건에 맞는가 (기간은 발생 시각 기준)."""
    t = row['t_raised']
    if f['t_from'] is not None and t < f['t_from']:
        return False
    if f['t_to'] is not None and t >= f['t_to']:
        return False
    if f['robot'] is not None and row['robot'] != f['robot']:
        return False
    if f['levels'] is not None and row['level'] not in f['levels']:
        return False
    if f['states'] is not None and row['state'] not in f['states']:
        return False
    if f['code'] is not None and row['code'] != f['code']:
        return False
    if f['q'] is not None and f['q'] not in (str(row['text']) + '\n' + str(row['detail'])).lower():
        return False
    return True


def stamp(t):
    """epoch -> 'YYYY-MM-DD HH:MM:SS' (서버 PC 현지 시각). 없으면 빈 문자열."""
    return '' if t is None else datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M:%S')


def to_csv(rows):
    """행 목록 -> CSV 문자열 (UTF-8 BOM 포함). BOM 이 있어야 스프레드시트가 한글을 깨뜨리지 않고 연다.
    '=', '+', '-', '@' 로 시작하는 칸은 수식으로 읽히지 않게 앞에 작은따옴표를 붙인다."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator='\r\n')
    w.writerow(CSV_HEAD)

    def safe(v):
        v = '' if v is None else str(v)
        return "'" + v if v[:1] in ('=', '+', '-', '@', '\t') else v
    for r in rows:
        w.writerow([stamp(r['t_raised']), safe(r['robot']), r['level'], r['code'], safe(r['text']), STATE_TEXT[r['state']], r['count'],
                    safe(r['acked_by']), stamp(r['acked_at']), stamp(r['t_cleared']) if r['state'] == 'cleared' else '',
                    '' if r['duration'] is None else r['duration']])
    return '﻿' + out.getvalue()
