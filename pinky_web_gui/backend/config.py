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

    def positive(key, default, cast):
        v = cast(raw.get(key, default))
        if v <= 0:
            raise ValueError(f'{path}: {key} 는 0보다 커야 한다')
        return v

    return Config(tuple(robots), zone_topic, timeout, hz, map_yaml, points, robot_settings,
                  positive('max_goto_points', 10, int), positive('no_response_sec', 5.0, float),
                  positive('starting_notice_sec', 5.0, float), positive('command_history_size', 50, int))
