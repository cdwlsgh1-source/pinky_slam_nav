"""로봇별 상태 모델.

ROS 에도 FastAPI 에도 의존하지 않는 순수 파이썬이다. 그래서 mock 과 실제 ROS 구독이
같은 갱신 함수를 쓰고, ROS 없이 단위 검증도 할 수 있다.

모든 ingest_* 는 브라우저로 보낼 이벤트 목록을 돌려준다. 호출은 항상 asyncio 이벤트 루프
스레드에서만 한다 (ROS 스레드는 call_soon_threadsafe 로 넘긴다) — 그래서 락이 필요 없다.
"""
import json
import logging
import math
import time

log = logging.getLogger('pinky_web_gui.state')


def quaternion_to_yaw(x, y, z, w):
    """쿼터니언 → yaw(rad). 2D 지도 위 방향만 쓰므로 roll/pitch 는 버린다."""
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _num(value):
    """NaN/inf 를 None 으로. JSON 표준에 없어서 브라우저의 JSON.parse 가 실패한다.
    (BatteryState.percentage 는 측정 불가일 때 NaN 이다.)"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def parse_patrol_status(raw):
    """patrol_status 문자열(JSON) → dict. 형식이 틀려도 예외를 내지 않는다.

    JSON 이 아니거나 객체가 아니면 서버가 죽지 않게 경고만 남기고, 원문을 detail 에 보관한다.
    """
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('JSON 객체가 아님')
    except (ValueError, TypeError) as e:
        log.warning('patrol_status 파싱 실패 (%s): %r', e, raw)
        return {'state': 'UNKNOWN', 'waypoint': -1, 'detail': str(raw), 'time': None}

    waypoint = data.get('waypoint', -1)
    return {
        'state': str(data.get('state', 'UNKNOWN')),
        'waypoint': waypoint if isinstance(waypoint, int) and not isinstance(waypoint, bool) else -1,
        'detail': str(data.get('detail', '')),
        # 로봇 시계 기준 값이다. online 판정에는 쓰지 않는다 (PC 수신 시각을 쓴다).
        'time': _num(data.get('time')),
    }


def _update(robot, field, data):
    return {'type': 'robot_update', 'robot': robot, 'field': field, 'data': data}


class FleetState:
    def __init__(self, robot_ids, online_timeout_sec, pose_max_hz,
                 monotonic=time.monotonic, wall=time.time):
        self._timeout = online_timeout_sec
        self._min_pose_interval = 1.0 / pose_max_hz
        self._monotonic = monotonic
        self._wall = wall

        # patrol/pose/battery 는 첫 메시지가 올 때까지 None 이다.
        self.robots = {
            rid: {'online': False, 'patrol': None, 'pose': None, 'battery': None, 'last_seen': None}
            for rid in robot_ids
        }
        self.zone = 'unknown'
        self._seen_mono = {rid: None for rid in robot_ids}
        self._pose_emitted_mono = {rid: None for rid in robot_ids}

    # ---- 내부 ----
    def _touch(self, robot):
        """수신 시각을 갱신한다. offline → online 으로 바뀌면 이벤트를 돌려준다."""
        self._seen_mono[robot] = self._monotonic()
        state = self.robots[robot]
        state['last_seen'] = self._wall()
        if not state['online']:
            state['online'] = True
            return [_update(robot, 'online', True)]
        return []

    # ---- 갱신 ----
    def ingest_patrol(self, robot, raw):
        if robot not in self.robots:
            return []
        events = self._touch(robot)
        patrol = parse_patrol_status(raw)
        self.robots[robot]['patrol'] = patrol
        events.append(_update(robot, 'patrol', patrol))
        return events

    def ingest_pose(self, robot, x, y, yaw):
        if robot not in self.robots:
            return []
        events = self._touch(robot)
        pose = {'x': _num(x), 'y': _num(y), 'yaw': _num(yaw)}
        # 상태에는 항상 최신 값을 두고, 브라우저로 보내는 이벤트만 로봇당 pose_max_hz 로 줄인다.
        self.robots[robot]['pose'] = pose
        now = self._monotonic()
        last = self._pose_emitted_mono[robot]
        if last is None or now - last >= self._min_pose_interval:
            self._pose_emitted_mono[robot] = now
            events.append(_update(robot, 'pose', pose))
        return events

    def ingest_battery(self, robot, percentage, voltage):
        if robot not in self.robots:
            return []
        events = self._touch(robot)
        battery = {'percentage': _num(percentage), 'voltage': _num(voltage)}
        self.robots[robot]['battery'] = battery
        events.append(_update(robot, 'battery', battery))
        return events

    def ingest_zone(self, status):
        """zone manager 는 점유가 바뀔 때만 발행하므로 같은 값은 다시 보내지 않는다."""
        if status == self.zone:
            return []
        self.zone = status
        return [{'type': 'zone', 'status': status}]

    def check_online(self):
        """마지막 수신 후 timeout 이 지난 로봇을 offline 으로 바꾼다. 주기적으로 호출한다."""
        events = []
        now = self._monotonic()
        for rid, state in self.robots.items():
            seen = self._seen_mono[rid]
            if state['online'] and (seen is None or now - seen > self._timeout):
                state['online'] = False
                events.append(_update(rid, 'online', False))
        return events

    # ---- 조회 ----
    def snapshot(self):
        return {
            'type': 'snapshot',
            'robots': {rid: dict(state) for rid, state in self.robots.items()},
            'zone': self.zone,
        }
