"""config/robots.yaml 로더. 로봇 이름, 토픽 이름, 타임아웃은 전부 이 파일을 거쳐서만 읽는다."""
import re
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
    processes: tuple = ()              # ProcSpec 목록 (processes_yaml 이 없으면 비어 있다). 목록 순서가 '전체 시작' 순서다


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
    scan_yaw_offset_deg: dict = field(default_factory=dict)  # 로봇 id -> 센서 보정 각도(도). tf 를 못 구했을 때만 쓴다


@dataclass(frozen=True)
class ProcSpec:
    """GUI 가 켜고 끌 수 있는 프로세스 하나. 이 목록에 있는 것만 실행할 수 있다 (사용자 입력 문자열은 실행하지 않는다)."""
    id: str
    label: str
    kind: str                # 'local'(관제 PC 에서 실행) | 'ssh'(로봇에서 SSH 로 실행)
    command: str             # 빈 문자열이면 '명령 미설정' (화면에서 시작 버튼 비활성)
    domain: int = None       # ROS_DOMAIN_ID (없으면 설정하지 않음)
    source: tuple = ()       # 실행 전에 source 할 파일들 (ssh 면 로봇의 경로). cwd 가 있으면 ./install/setup.bash 처럼 cwd 기준 상대 경로도 가능
    cwd: str = ''            # 실행 전에 cd 할 절대 경로 (ssh 면 로봇의 경로). 상대 경로 인자(map:=x.yaml)와 ./install/setup.bash 의 기준이 된다
    detect: str = ''         # pgrep -f 패턴: 이미 같은 프로세스가 돌고 있으면 시작하지 않는다
    stop_pattern: str = ''   # (ssh) SSH 연결을 닫아도 안 죽을 때 로봇에서 pkill 할 패턴. 비어 있으면 detect 를 쓴다
    robot: str = None        # 이 프로세스가 속한 로봇 (health 표시용, robots 에 있어야 한다)
    health: bool = False     # True 면 robot 이 online(토픽 수신 중)인지도 화면에 같이 보인다
    stop_on_exit: bool = True    # 서버가 종료될 때 같이 정지할지 (기본 True. 남겨 두면 다음 실행에서 '이미 실행 중' 으로 잠긴다). False 로 두면 로봇 쪽이 계속 돈다
    confirm_stop: bool = False   # 정지 전에 확인이 필요한지 (로봇 bringup/map)
    settle_sec: float = 2.0      # 시작 후 이 시간 동안 살아 있으면 running
    stop_timeout_sec: float = 5.0   # SIGINT 후 이 시간 안에 안 끝나면 다음 단계(SIGTERM, 다시 이 시간, SIGKILL)
    ssh_host: str = ''
    ssh_user: str = ''
    ssh_port: int = 22
    ssh_key: str = ''
    ssh_password_env: str = ''   # 있으면 이 환경 변수의 값을 sshpass -e 로 쓴다 (값 자체는 설정 파일에 두지 않는다)
    group: str = ''              # 시스템 패널에서 묶어 보여 줄 기능 이름 (예: '도메인 브릿지', 'bringup'). 같은 이름끼리 한 묶음
    env: tuple = ()          # ((이름, 값), ...) 실행 전에 export 할 환경 변수. SSH 는 ~/.bashrc 를 읽지 않아 RMW/CYCLONEDDS_URI 가 빠지므로 여기에 적는다
    restart_if_stalled: bool = False   # True 면 [전체 시작] 에서 bringup 뒤에도 로봇 토픽이 안 올 때 이 프로세스(브릿지)를 한 번 재시작한다
    ready_sec: float = 25.0  # health 가 있는 프로세스: 켜진 뒤 로봇 토픽이 올 때까지 [전체 시작] 이 기다리는 최대 시간


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


def _load_motion(path, raw_motion, raw_manual, raw_scan, robots=()):
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
    offsets = sc.get('yaw_offset_deg', {})
    if not isinstance(offsets, dict) or not set(offsets) <= set(robots):
        raise ValueError(f'{path}: scan.yaw_offset_deg 는 robots {list(robots)} 의 일부를 키로 가진 매핑이어야 한다')
    for rid, v in offsets.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not -360 <= v <= 360:
            raise ValueError(f'{path}: scan.yaw_offset_deg.{rid} 는 -360~360 도의 숫자여야 한다')
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
        scan_yaw_offset_deg={rid: float(v) for rid, v in offsets.items()},
    )


PROC_ID = re.compile(r'^[a-z][a-z0-9_]{0,40}$')
ENV_NAME = re.compile(r'^[A-Z][A-Z0-9_]{0,60}$')
REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # pinky_slam_nav/


def _expand(text, path, what):
    """{repo} 만 치환한다 (다른 {..} 가 있으면 오타로 보고 거절)."""
    out = text.replace('{repo}', str(REPO_ROOT))
    if re.search(r'\{[A-Za-z_]+\}', out):
        raise ValueError(f'{path}: {what} 에 알 수 없는 치환 문자열이 있다 ({{repo}} 만 쓸 수 있다): {text!r}')
    return out


def _load_processes(path, robots):
    with open(path, encoding='utf-8') as f:
        raw = yaml.safe_load(f) or {}
    d = raw.get('defaults') or {}
    if not isinstance(d, dict):
        raise ValueError(f'{path}: defaults 는 매핑이어야 한다')
    items = raw.get('processes')
    if not isinstance(items, list) or not items:
        raise ValueError(f'{path}: processes 는 비어 있지 않은 목록이어야 한다')
    seen, out = set(), []
    for i, p in enumerate(items):
        if not isinstance(p, dict):
            raise ValueError(f'{path}: processes[{i}] 는 매핑이어야 한다')
        pid = p.get('id')
        if not isinstance(pid, str) or not PROC_ID.match(pid):
            raise ValueError(f'{path}: processes[{i}].id {pid!r} 는 영소문자·숫자·_ 로 된 이름이어야 한다')
        if pid in seen:
            raise ValueError(f'{path}: 프로세스 id {pid} 가 중복이다')
        seen.add(pid)
        kind = p.get('kind')
        if kind not in ('local', 'ssh'):
            raise ValueError(f'{path}: {pid}.kind 는 local 또는 ssh 여야 한다')
        command = p.get('command', '')
        if not isinstance(command, str):
            raise ValueError(f'{path}: {pid}.command 는 문자열이어야 한다')
        command = _expand(command.strip(), path, f'{pid}.command')
        domain = p.get('domain', d.get('domain'))
        if domain is not None and (isinstance(domain, bool) or not isinstance(domain, int) or not 0 <= domain <= 232):
            raise ValueError(f'{path}: {pid}.domain 은 0~232 정수여야 한다')
        src = p.get('source', d.get(f'{kind}_source', []))
        if not isinstance(src, list) or not all(isinstance(x, str) and x for x in src):
            raise ValueError(f'{path}: {pid}.source 는 문자열 목록이어야 한다')
        src = tuple(_expand(x, path, f'{pid}.source') for x in src)
        cwd = p.get('cwd', '')
        if not isinstance(cwd, str):
            raise ValueError(f'{path}: {pid}.cwd 는 문자열이어야 한다')
        cwd = _expand(cwd.strip(), path, f'{pid}.cwd')
        if cwd and not cwd.startswith('/'):
            raise ValueError(f'{path}: {pid}.cwd 는 절대 경로여야 한다 (~ 는 쓸 수 없다): {cwd!r}')
        detect = p.get('detect', '')
        stop_pattern = p.get('stop_pattern', '')
        for name, pat in (('detect', detect), ('stop_pattern', stop_pattern)):
            if not isinstance(pat, str) or (pat and not pat[0].isalnum()):
                raise ValueError(f'{path}: {pid}.{name} 는 영문/숫자로 시작하는 문자열이어야 한다 (자기 자신을 찾지 않게 첫 글자를 괄호로 감싸 쓴다)')
        robot = p.get('robot')
        if robot is not None and robot not in robots:
            raise ValueError(f'{path}: {pid}.robot {robot!r} 은 robots {list(robots)} 에 있어야 한다')
        health = p.get('health', False)
        if not isinstance(health, bool) or (health and robot is None):
            raise ValueError(f'{path}: {pid}.health 는 true/false 이고, true 면 robot 이 필요하다')
        host = user = key = pw_env = ''
        port = 22
        if kind == 'ssh':
            ssh = p.get('ssh') or {}
            if not isinstance(ssh, dict):
                raise ValueError(f'{path}: {pid}.ssh 는 매핑이어야 한다')
            host, user = str(ssh.get('host') or ''), str(ssh.get('user') or '')
            key = str(ssh.get('key') or '')
            pw_env = str(ssh.get('password_env') or '')
            port = ssh.get('port', 22)
            if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
                raise ValueError(f'{path}: {pid}.ssh.port 는 1~65535 정수여야 한다')
            if command and not (host and user):
                raise ValueError(f'{path}: {pid} 는 command 가 있으므로 ssh.host 와 ssh.user 가 필요하다')
            if re.search(r'[^A-Za-z0-9._:-]', host) or host.startswith('-') or re.search(r'[^A-Za-z0-9._-]', user) or user.startswith('-'):
                raise ValueError(f'{path}: {pid}.ssh.host/user 에 허용되지 않는 문자가 있다')
            if pw_env and not ENV_NAME.match(pw_env):
                raise ValueError(f'{path}: {pid}.ssh.password_env 는 환경 변수 이름이어야 한다 (비밀번호 값을 적지 않는다)')
            if key:
                key = str(Path(key).expanduser())
        group = str(p.get('group') or '').strip()
        if len(group) > 40:
            raise ValueError(f'{path}: {pid}.group 은 40자 이하여야 한다')
        env = p.get('env', d.get(f'{kind}_env', {}))
        if not isinstance(env, dict):
            raise ValueError(f'{path}: {pid}.env 는 매핑이어야 한다')
        for k, v in env.items():
            if not isinstance(k, str) or not ENV_NAME.match(k) or k == 'ROS_DOMAIN_ID':
                raise ValueError(f'{path}: {pid}.env 의 이름 {k!r} 이 올바르지 않다 (ROS_DOMAIN_ID 는 domain 으로 지정한다)')
            if not isinstance(v, (str, int)) or isinstance(v, bool) or '\n' in str(v):
                raise ValueError(f'{path}: {pid}.env.{k} 는 한 줄 문자열이어야 한다')
        env = tuple((k, _expand(str(v), path, f'{pid}.env.{k}')) for k, v in env.items())
        def num(name, default, hi):
            try:
                v = float(p.get(name, default))
            except (TypeError, ValueError):
                raise ValueError(f'{path}: {pid}.{name} 는 숫자여야 한다') from None
            if not 0 < v <= hi:
                raise ValueError(f'{path}: {pid}.{name} 는 0 초과 {hi} 이하여야 한다')
            return v
        flags = {}
        for name, default in (('stop_on_exit', True), ('confirm_stop', kind == 'ssh'), ('restart_if_stalled', False)):
            v = p.get(name, default)
            if not isinstance(v, bool):
                raise ValueError(f'{path}: {pid}.{name} 는 true/false 여야 한다')
            flags[name] = v
        out.append(ProcSpec(pid, str(p.get('label') or pid), kind, command, domain, src, cwd, detect, stop_pattern, robot, health,
                            flags['stop_on_exit'], flags['confirm_stop'], num('settle_sec', 2.0, 60), num('stop_timeout_sec', 5.0, 60),
                            host, user, port, key, pw_env, group, env, flags['restart_if_stalled'], num('ready_sec', 25.0, 120)))
    return tuple(out)


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
    motion = _load_motion(path, raw.get('motion'), raw.get('manual'), raw.get('scan'), robots)

    processes = ()
    if raw.get('processes_yaml') is not None:
        py = raw['processes_yaml']
        if not isinstance(py, str) or not py:
            raise ValueError(f'{path}: processes_yaml 은 문자열 경로여야 한다')
        processes = _load_processes((path.resolve().parent / py).resolve(), robots)

    def positive(key, default, cast):
        v = cast(raw.get(key, default))
        if v <= 0:
            raise ValueError(f'{path}: {key} 는 0보다 커야 한다')
        return v

    return Config(tuple(robots), zone_topic, timeout, hz, map_yaml, points, robot_settings,
                  positive('max_goto_points', 10, int), positive('no_response_sec', 5.0, float),
                  positive('starting_notice_sec', 5.0, float), positive('command_history_size', 50, int),
                  inflation, zone, motion, processes)
