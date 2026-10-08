"""config/robots.yaml 로더. 로봇 이름, 토픽 이름, 타임아웃은 전부 이 파일을 거쳐서만 읽는다."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'config' / 'robots.yaml'


@dataclass(frozen=True)
class Config:
    robots: tuple
    zone_status_topic: str
    online_timeout_sec: float
    pose_max_hz: float
    map_yaml: str = None  # 지도 yaml 의 절대 경로 (robots.yaml 위치 기준 상대 경로로 적는다)
    points: dict = field(default_factory=dict)          # 이름 -> {x, y, in_zone, door} (표시와 mock 이동용)
    robot_settings: dict = field(default_factory=dict)  # 로봇 id -> RobotSettings
    max_goto_points: int = 10
    no_response_sec: float = 5.0
    starting_notice_sec: float = 5.0
    command_history_size: int = 50
    planner_inflation_m: float = 0.08  # 경로 추정에서 벽에서 이만큼 떨어진 칸만 지난다 (지점의 벽 여유 최소값 0.10 m 보다 작아야 한다)
    zone: 'ZoneConfig' = None          # 위험 구역 영역 (zone_yaml 이 없으면 None)
    motion: 'MotionConfig' = None      # 비상정지/수동 조작/LiDAR 설정


# 수동 조작 하드 상한. 설정 파일이 실수로 큰 값을 적어도 이 이상은 나가지 않는다.
LINEAR_HARD_MAX = 0.2    # m/s
ANGULAR_HARD_MAX = 1.0   # rad/s


@dataclass(frozen=True)
class ZoneConfig:
    points: tuple          # 구역 안 지점 이름
    margin: float
    rect: dict             # {x_min, x_max, y_min, y_max} (m)
    doors: dict            # 로봇 id -> {'in': 이름, 'out': 이름}
    confirmed: bool        # 사용자가 지도와 대조해 경계를 확인했는지


@dataclass(frozen=True)
class MotionConfig:
    estop_burst_hz: float = 10.0
    estop_burst_sec: float = 2.0
    manual_enabled: bool = True
    manual_max_linear: float = 0.1
    manual_max_angular: float = 0.5
    manual_timeout_sec: float = 0.5
    manual_rate_hz: float = 10.0
    scan_max_hz: float = 5.0
    scan_decimate: int = 3


@dataclass(frozen=True)
class RobotSettings:
    goto_allowed: tuple     # 백엔드가 허용하는 goto 지점 (RED* 없음)
    home: str
    goto_wait_sec: float
    wait_every_point: bool
    start_route: tuple      # start 순찰 경로 이름 (노드의 WAYPOINTS)


def is_door(name) -> bool:
    """구역 문(RED*)은 설정과 상관없이 직접 goto 대상이 될 수 없다."""
    return isinstance(name, str) and name.strip().upper().startswith('RED')


def _load_points(path):
    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}
    pts = raw.get('points')
    if not isinstance(pts, dict) or not pts:
        raise ValueError(f'{path}: points 는 비어 있지 않은 매핑이어야 한다')
    out = {}
    for name, v in pts.items():
        if not isinstance(name, str) or not isinstance(v, dict):
            raise ValueError(f'{path}: 포인트 {name!r} 형식이 잘못됐다')
        try:
            x, y = float(v['x']), float(v['y'])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f'{path}: 포인트 {name} 에 숫자 x, y 가 필요하다') from None
        out[name] = {'x': x, 'y': y, 'in_zone': bool(v.get('in_zone', False)),
                     'door': bool(v.get('door', False)) or is_door(name)}
    return out


def _load_robot_settings(path, robots, raw_settings, points):
    if not isinstance(raw_settings, dict) or set(raw_settings) != set(robots):
        raise ValueError(f'{path}: robot_settings 의 키는 robots {list(robots)} 와 같아야 한다')
    out = {}
    for rid in robots:
        s = raw_settings[rid]
        if not isinstance(s, dict):
            raise ValueError(f'{path}: robot_settings.{rid} 는 매핑이어야 한다')
        allowed = s.get('goto_allowed')
        if not isinstance(allowed, list) or not allowed or not all(isinstance(n, str) for n in allowed):
            raise ValueError(f'{path}: {rid}.goto_allowed 는 비어 있지 않은 문자열 목록이어야 한다')
        for n in allowed:
            if is_door(n):
                raise ValueError(f'{path}: {rid}.goto_allowed 에 구역 문 {n} 을 넣을 수 없다 (노드가 문 경유를 처리한다)')
            if n != n.upper() or n not in points:
                raise ValueError(f'{path}: {rid}.goto_allowed 의 {n} 은 points 에 있는 대문자 이름이어야 한다')
        home = s.get('home')
        if home not in allowed:
            raise ValueError(f'{path}: {rid}.home {home!r} 은 goto_allowed 에 있어야 한다')
        route = s.get('start_route', [])
        if not isinstance(route, list) or any(n not in points for n in route):
            raise ValueError(f'{path}: {rid}.start_route 의 모든 이름이 points 에 있어야 한다')
        wait = float(s.get('goto_wait_sec', 10))
        if wait < 0:
            raise ValueError(f'{path}: {rid}.goto_wait_sec 는 0 이상이어야 한다')
        out[rid] = RobotSettings(tuple(allowed), home, wait, bool(s.get('wait_every_point', True)), tuple(route))
    return out


def _load_zone(path, robots, points):
    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}
    names = raw.get('points')
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ValueError(f'{path}: points 는 비어 있지 않은 문자열 목록이어야 한다')
    for n in names:
        if n not in points:
            raise ValueError(f'{path}: 구역 지점 {n} 이 points.yaml 에 없다')
        if not points[n]['in_zone']:
            raise ValueError(f'{path}: 구역 지점 {n} 은 points.yaml 에서 in_zone 이어야 한다')
    try:
        margin = float(raw.get('margin', 0.15))
    except (TypeError, ValueError):
        raise ValueError(f'{path}: margin 은 숫자여야 한다') from None
    if not 0 <= margin <= 2.0:
        raise ValueError(f'{path}: margin 은 0~2 m 여야 한다')
    doors_raw = raw.get('doors')
    if not isinstance(doors_raw, dict) or set(doors_raw) != set(robots):
        raise ValueError(f'{path}: doors 의 키는 robots {list(robots)} 와 같아야 한다')
    doors = {}
    for rid in robots:
        d = doors_raw[rid]
        if not isinstance(d, dict) or set(d) != {'in', 'out'}:
            raise ValueError(f'{path}: doors.{rid} 는 in, out 두 키를 가져야 한다')
        for k in ('in', 'out'):
            if d[k] not in points or not points[d[k]]['door']:
                raise ValueError(f'{path}: doors.{rid}.{k} {d[k]!r} 는 points.yaml 의 구역 문(RED*) 이어야 한다')
        doors[rid] = {'in': d['in'], 'out': d['out']}
    xs = [points[n]['x'] for n in names]
    ys = [points[n]['y'] for n in names]
    rect = {'x_min': min(xs) - margin, 'x_max': max(xs) + margin,
            'y_min': min(ys) - margin, 'y_max': max(ys) + margin}
    confirmed = raw.get('confirmed', False)
    if not isinstance(confirmed, bool):
        raise ValueError(f'{path}: confirmed 는 true/false 여야 한다')
    return ZoneConfig(tuple(names), margin, rect, doors, confirmed)


def _load_motion(path, raw_motion, raw_manual, raw_scan):
    def section(raw, name):
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(f'{path}: {name} 는 매핑이어야 한다')
        return raw

    m, man, sc = section(raw_motion, 'motion'), section(raw_manual, 'manual'), section(raw_scan, 'scan')
    d = MotionConfig()

    def num(src, key, default, name, lo=0.0, hi=None, cast=float):
        try:
            v = cast(src.get(key, default))
        except (TypeError, ValueError):
            raise ValueError(f'{path}: {name}.{key} 는 숫자여야 한다') from None
        if not v > lo or (hi is not None and v > hi):
            raise ValueError(f'{path}: {name}.{key} 는 {lo} 초과' + (f' {hi} 이하' if hi is not None else '') + ' 여야 한다')
        return v

    enabled = man.get('enabled', d.manual_enabled)
    if not isinstance(enabled, bool):
        raise ValueError(f'{path}: manual.enabled 는 true/false 여야 한다')
    return MotionConfig(
        estop_burst_hz=num(m, 'estop_burst_hz', d.estop_burst_hz, 'motion', hi=50),
        estop_burst_sec=num(m, 'estop_burst_sec', d.estop_burst_sec, 'motion', hi=30),
        manual_enabled=enabled,
        manual_max_linear=num(man, 'max_linear', d.manual_max_linear, 'manual', hi=LINEAR_HARD_MAX),
        manual_max_angular=num(man, 'max_angular', d.manual_max_angular, 'manual', hi=ANGULAR_HARD_MAX),
        manual_timeout_sec=num(man, 'input_timeout_sec', d.manual_timeout_sec, 'manual', hi=2.0),
        manual_rate_hz=num(man, 'rate_hz', d.manual_rate_hz, 'manual', hi=50),
        scan_max_hz=num(sc, 'max_hz', d.scan_max_hz, 'scan', hi=20),
        scan_decimate=num(sc, 'decimate', d.scan_decimate, 'scan', lo=0, hi=20, cast=int),
    )


def load_config(path=None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}

    robots = raw.get('robots')
    if not isinstance(robots, list) or not robots or not all(isinstance(r, str) and r for r in robots):
        raise ValueError(f'{path}: robots 는 비어 있지 않은 문자열 목록이어야 한다')
    if len(set(robots)) != len(robots):
        raise ValueError(f'{path}: robots 에 중복이 있다')
    zone_topic = raw.get('zone_status_topic')
    if not isinstance(zone_topic, str) or not zone_topic.startswith('/'):
        raise ValueError(f'{path}: zone_status_topic 은 "/"로 시작하는 토픽 이름이어야 한다')

    timeout = float(raw.get('online_timeout_sec', 5.0))
    hz = float(raw.get('pose_max_hz', 10))
    if timeout <= 0 or hz <= 0:
        raise ValueError(f'{path}: online_timeout_sec, pose_max_hz 는 0보다 커야 한다')

    map_yaml = raw.get('map_yaml')
    if map_yaml is not None:
        if not isinstance(map_yaml, str) or not map_yaml:
            raise ValueError(f'{path}: map_yaml 은 문자열 경로여야 한다')
        map_yaml = str((path.resolve().parent / map_yaml).resolve())

    points = {}
    robot_settings = {}
    if 'robot_settings' in raw or 'points_yaml' in raw:
        points_yaml = raw.get('points_yaml')
        if not isinstance(points_yaml, str) or not points_yaml:
            raise ValueError(f'{path}: points_yaml 이 필요하다')
        points = _load_points((path.resolve().parent / points_yaml).resolve())
        robot_settings = _load_robot_settings(path, robots, raw.get('robot_settings'), points)

    inflation = float(raw.get('planner_inflation_m', 0.08))
    if not 0 <= inflation <= 0.5:
        raise ValueError(f'{path}: planner_inflation_m 은 0~0.5 m 여야 한다')
    zone = None
    if raw.get('zone_yaml') is not None:
        zone_yaml = raw['zone_yaml']
        if not isinstance(zone_yaml, str) or not zone_yaml:
            raise ValueError(f'{path}: zone_yaml 은 문자열 경로여야 한다')
        if not points:
            raise ValueError(f'{path}: zone_yaml 을 쓰려면 points_yaml 이 필요하다')
        zone = _load_zone((path.resolve().parent / zone_yaml).resolve(), robots, points)
    motion = _load_motion(path, raw.get('motion'), raw.get('manual'), raw.get('scan'))

    def positive(key, default, cast):
        v = cast(raw.get(key, default))
        if v <= 0:
            raise ValueError(f'{path}: {key} 는 0보다 커야 한다')
        return v

    return Config(tuple(robots), zone_topic, timeout, hz, map_yaml, points, robot_settings,
                  positive('max_goto_points', 10, int), positive('no_response_sec', 5.0, float),
                  positive('starting_notice_sec', 5.0, float), positive('command_history_size', 50, int),
                  inflation, zone, motion)
