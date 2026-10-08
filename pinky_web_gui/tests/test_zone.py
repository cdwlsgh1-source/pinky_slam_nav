"""구역 상태 해석(v1/v2 형식), 점유 시간, zone.yaml 검증, StateStore 연동."""
import pytest

from backend.config import load_config
from backend.state import StateStore
from backend.zone import ZoneTracker, parse_status


@pytest.mark.parametrize('raw,expected', [
    ('free', ('free', None, None)),
    ('  free\n', ('free', None, None)),
    ('occupied_by:pinky1', ('occupied', 'pinky1', None)),              # v1
    ('occupied_by:pinky2:a1b2c3', ('occupied', 'pinky2', 'a1b2c3')),    # v2
    ('occupied_by:pinky1:', ('occupied', 'pinky1', None)),
    ('occupied_by:', ('unknown', None, None)),
    ('occupied_by::tok', ('unknown', None, None)),
    ('FREE', ('unknown', None, None)),                                   # 대소문자 다른 값은 추측하지 않는다
    ('busy', ('unknown', None, None)),
    ('', ('unknown', None, None)),
    (None, ('unknown', None, None)),
    (123, ('unknown', None, None)),
])
def test_parse_status(raw, expected):
    p = parse_status(raw)
    assert (p['state'], p['holder'], p['token']) == expected


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_held_time_counts_from_first_seen_and_resets():
    c = Clock()
    z = ZoneTracker(c)
    z.update('free')
    assert z.info()['held_sec'] is None
    z.update('occupied_by:pinky1')
    c.t += 42
    assert z.info()['held_sec'] == pytest.approx(42)
    z.update('occupied_by:pinky1')               # 같은 점유 -> 시간 유지
    c.t += 8
    assert z.info()['held_sec'] == pytest.approx(50)
    z.update('occupied_by:pinky1:tok')           # 같은 점유자의 token 만 바뀜 -> 유지
    assert z.info()['held_sec'] == pytest.approx(50)
    z.update('occupied_by:pinky2')               # 점유자가 바뀜 -> 다시 센다
    assert z.info()['held_sec'] == pytest.approx(0)
    z.update('free')
    assert z.info()['held_sec'] is None and z.info()['state'] == 'free'


def test_store_zone_message_and_snapshot():
    c = Clock()
    s = StateStore(['a'], '/zone_manager/status', 5.0, 10, now=c)
    assert s.snapshot()['zone_info'] is None           # 수신 전에는 비어 있다고 가정하지 않는다
    [m] = s.apply(('zone', 'occupied_by:a'))
    assert (m['status'], m['state'], m['holder']) == ('occupied_by:a', 'occupied', 'a')
    c.t += 7
    assert s.snapshot()['zone_info']['held_sec'] == pytest.approx(7)
    assert s.snapshot()['zone'] == 'occupied_by:a'
    assert s.apply(('zone', 'occupied_by:a')) == []     # 변화 없음


# ---- zone.yaml ----
def _cfg(tmp_path, zone_text):
    import shutil
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / 'config'
    for f in ('robots.yaml', 'points.yaml'):
        shutil.copy(src / f, tmp_path / f)
    (tmp_path / 'zone.yaml').write_text(zone_text, encoding='utf-8')
    raw = (tmp_path / 'robots.yaml').read_text(encoding='utf-8').replace('../../map_view_pc', str(src.parent.parent / 'map_view_pc'))
    (tmp_path / 'robots.yaml').write_text(raw, encoding='utf-8')
    return tmp_path / 'robots.yaml'


GOOD = """
points: [P3, P4, P5, P6]
margin: 0.15
doors:
  pinky1: {in: RED1IN, out: RED1OUT}
  pinky2: {in: RED2IN, out: RED2OUT}
"""


def test_default_zone_rect_from_points():
    z = load_config().zone
    assert z.confirmed is False                       # 사용자가 확인하기 전까지 초안
    r = z.rect
    assert (r['x_min'], r['x_max'], r['y_min'], r['y_max']) == pytest.approx((0.85, 1.70, -0.55, 0.25))
    assert z.doors['pinky2'] == {'in': 'RED2IN', 'out': 'RED2OUT'}


def test_zone_yaml_loads_and_margin_changes_rect(tmp_path):
    z = load_config(_cfg(tmp_path, GOOD.replace('0.15', '0.0') + 'confirmed: true\n')).zone
    assert z.confirmed is True
    assert (z.rect['x_min'], z.rect['x_max'], z.rect['y_min'], z.rect['y_max']) == pytest.approx((1.0, 1.55, -0.4, 0.1))


@pytest.mark.parametrize('mutate,msg', [
    (lambda t: t.replace('P6]', 'P2]'), 'in_zone'),                       # 구역 밖 지점
    (lambda t: t.replace('P6]', 'P9]'), 'points.yaml'),                   # 없는 지점
    (lambda t: t.replace('RED1IN', 'P2'), '구역 문'),                       # 문이 아님
    (lambda t: t.replace('  pinky2: {in: RED2IN, out: RED2OUT}\n', ''), 'doors'),   # 로봇 누락
    (lambda t: t.replace('0.15', '-1'), 'margin'),
    (lambda t: t + 'confirmed: yes please\n', 'confirmed'),
])
def test_zone_yaml_validation(tmp_path, mutate, msg):
    with pytest.raises(ValueError, match=msg):
        load_config(_cfg(tmp_path, mutate(GOOD)))


def test_scan_yaw_offset_config(tmp_path):
    import shutil
    from pathlib import Path
    cfgp = _cfg(tmp_path, GOOD)
    raw = cfgp.read_text(encoding='utf-8')
    cfgp.write_text(raw.replace('yaw_offset_deg: {}', 'yaw_offset_deg: {pinky1: 180}'), encoding='utf-8')
    assert load_config(cfgp).motion.scan_yaw_offset_deg == {'pinky1': 180.0}
    for bad in ('{ghost: 90}', '{pinky1: 400}', '{pinky1: x}', '[1]'):
        cfgp.write_text(raw.replace('yaw_offset_deg: {}', f'yaw_offset_deg: {bad}'), encoding='utf-8')
        with pytest.raises(ValueError, match='yaw_offset_deg'):
            load_config(cfgp)
