"""구역 점유 상태(`/zone_manager/status`) 해석과 점유 시간 추적. rclpy 에 의존하지 않고, 시계는 주입받는다.

zone_manager_node 는 `free` 또는 `occupied_by:<id>` 를 발행한다.
과거 v2 매니저(`v2-final` 태그)는 `occupied_by:<id>:<token>` 형식이었으므로 둘 다 해석한다.
"""
import logging
import time

log = logging.getLogger('backend.zone')

FREE = 'free'
OCCUPIED = 'occupied'
UNKNOWN = 'unknown'


def parse_status(raw):
    """상태 문자열 -> {'state': free|occupied|unknown, 'holder': id|None, 'token': str|None}.

    알 수 없는 형식은 free 로 추측하지 않고 unknown 으로 둔다 (비어 있다고 잘못 보여 주는 쪽이 더 위험하다).
    """
    out = {'state': UNKNOWN, 'holder': None, 'token': None}
    if not isinstance(raw, str):
        return out
    text = raw.strip()
    if text == 'free':
        out['state'] = FREE
    elif text.startswith('occupied_by:'):
        holder, _, token = text[len('occupied_by:'):].partition(':')
        holder = holder.strip()
        if holder:
            out.update(state=OCCUPIED, holder=holder, token=token.strip() or None)
    return out


class ZoneTracker:
    """점유 시간은 백엔드가 그 점유 상태를 '처음 본 시각' 부터 센다 (FR4-2). 매니저는 시작 시각을 알려 주지 않는다."""

    def __init__(self, now=time.time):
        self._now = now
        self._parsed = parse_status(None)
        self._since = None

    def update(self, raw):
        new = parse_status(raw)
        if new['state'] == UNKNOWN:
            log.warning('해석할 수 없는 구역 상태: %r', raw)
        if new['state'] == OCCUPIED:
            # 같은 점유자의 token 만 바뀐 경우는 같은 점유로 본다
            if self._parsed['state'] != OCCUPIED or self._parsed['holder'] != new['holder']:
                self._since = self._now()
        else:
            self._since = None
        self._parsed = new

    def info(self):
        p = self._parsed
        held = None if self._since is None else max(0.0, self._now() - self._since)
        return {'state': p['state'], 'holder': p['holder'], 'token': p['token'], 'held_sec': held}
