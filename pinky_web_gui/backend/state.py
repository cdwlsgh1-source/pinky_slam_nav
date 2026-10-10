"""로봇별 상태 모델과 변경 이벤트 생성.

StateStore 는 asyncio 이벤트 루프 스레드에서만 호출한다 (rclpy 스레드는 큐로 이벤트만 넘긴다).
그래서 락이 필요 없다. 시간은 now 함수로 주입받아 테스트에서 가짜 시계를 쓸 수 있다.
"""
import json
import logging
import math
import time

from .zone import ZoneTracker

log = logging.getLogger('backend.state')


def quat_to_yaw(x, y, z, w):
    """쿼터니언 -> z축 회전(yaw, rad)."""
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def _num(v):
    """JSON 에 NaN/Inf 가 들어가면 직렬화가 깨지므로 None 으로 바꾼다."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


class StateStore:
    def __init__(self, robots, zone_topic, online_timeout_sec, pose_max_hz, now=time.time):
        self._robots = tuple(robots)
        self._timeout = online_timeout_sec
        self._pose_interval = 1.0 / pose_max_hz
        self._now = now
        self.zone = None  # 수신 전에는 null. 원문 문자열 (free | occupied_by:<id>[:<token>])
        self._zone = ZoneTracker(now)
        self.zone_topic = zone_topic
        self._r = {
            rid: {'online': False, 'last_seen': None, 'patrol': None, 'pose': None, 'battery': None}
            for rid in self._robots
        }
        # pose 속도 제한: 마지막 전송 시각, 아직 못 보낸 최신값
        self._pose_sent = {rid: 0.0 for rid in self._robots}
        self._pose_pending = {rid: False for rid in self._robots}

    # ---- 조회 ----
    def snapshot(self):
        robots = {}
        for rid, s in self._r.items():
            robots[rid] = {
                'online': s['online'],
                'last_seen': s['last_seen'],
                'patrol': s['patrol'],
                'pose': s['pose'],
                'battery': s['battery'],
            }
        return {'type': 'snapshot', 'robots': robots, 'zone': self.zone, 'zone_info': self.zone_info()}

    def zone_info(self):
        """구역 상태 해석 결과. 수신 전이면 None."""
        return None if self.zone is None else self._zone.info()

    def pose(self, rid):
        return self._r[rid]['pose']

    def patrol_state(self, rid):
        p = self._r[rid]['patrol']
        return p['state'] if p else None

    def is_online(self, rid):
        return self._r[rid]['online']

    # ---- 이벤트 반영. 반환값은 WebSocket 으로 내보낼 메시지 목록 ----
    def apply(self, event):
        kind = event[0]
        if kind == 'zone':
            return self._apply_zone(event[1])
        rid = event[1]
        if rid not in self._r:
            log.warning('알 수 없는 로봇 ID 이벤트 무시: %r', rid)
            return []
        out = self._touch(rid)
        if kind == 'patrol':
            out += self._apply_patrol(rid, event[2])
        elif kind == 'pose':
            out += self._apply_pose(rid, event[2], event[3], event[4])
        elif kind == 'battery':
            out += self._apply_battery(rid, event[2], event[3])
        elif kind == 'alive':
            pass  # /odom 처럼 '살아 있다' 는 사실만 필요한 토픽: online 갱신(_touch)으로 충분하다
        elif kind == 'scan':
            pass  # 수신했다는 사실(online 갱신)만 반영한다. 점은 StateStore 가 아니라 scan 경로로 나간다
        else:
            log.warning('알 수 없는 이벤트 종류 무시: %r', kind)
        return out

    def _touch(self, rid):
        """어떤 토픽이든 수신하면 last_seen 갱신 (FR1-5). 오프라인에서 돌아오면 online 이벤트."""
        s = self._r[rid]
        s['last_seen'] = self._now()
        if not s['online']:
            s['online'] = True
            return [self._update(rid, 'online', True)]
        return []

    def _apply_zone(self, status):
        if status == self.zone:
            return []
        self.zone = status
        self._zone.update(status)
        return [{'type': 'zone', 'status': status, **self._zone.info()}]

    def _apply_patrol(self, rid, raw):
        """FR1-6: JSON 이 아니면 서버는 유지하고, 원문을 detail 에 보관한다.
        이 경우 state 는 직전 값을 유지(없으면 null), waypoint 는 -1 로 둔다."""
        prev = self._r[rid]['patrol']
        patrol = None
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                patrol = {
                    'state': data.get('state'),
                    'waypoint': data.get('waypoint', -1),
                    'detail': data.get('detail', ''),
                    'time': data.get('time'),
                }
        except (ValueError, TypeError):
            pass
        if patrol is None:
            log.warning('%s patrol_status 가 JSON 객체가 아니다: %r', rid, raw)
            patrol = {
                'state': prev['state'] if prev else None,
                'waypoint': -1,
                'detail': raw if isinstance(raw, str) else repr(raw),
                'time': self._now(),
            }
        if patrol == prev:
            return []
        self._r[rid]['patrol'] = patrol
        return [self._update(rid, 'patrol', patrol)]

    def _apply_pose(self, rid, x, y, yaw):
        x, y, yaw = _num(x), _num(y), _num(yaw)
        if x is None or y is None or yaw is None:
            log.warning('%s pose 에 유한하지 않은 값이 있어 무시', rid)
            return []
        self._r[rid]['pose'] = {'x': x, 'y': y, 'yaw': yaw}  # 최신값은 항상 반영
        return self._emit_pose(rid, force=False)

    def _emit_pose(self, rid, force):
        now = self._now()
        if not force and now - self._pose_sent[rid] < self._pose_interval:
            self._pose_pending[rid] = True
            return []
        self._pose_sent[rid] = now
        self._pose_pending[rid] = False
        return [self._update(rid, 'pose', self._r[rid]['pose'])]

    def _apply_battery(self, rid, percentage, voltage):
        """percent 와 voltage 는 별도 토픽으로 오므로, 안 온 쪽(None)은 직전 값을 유지하고 합친다."""
        prev = self._r[rid]['battery'] or {'percentage': None, 'voltage': None}
        battery = {
            'percentage': prev['percentage'] if percentage is None else _num(percentage),
            'voltage': prev['voltage'] if voltage is None else _num(voltage),
        }
        if battery == self._r[rid]['battery']:
            return []
        self._r[rid]['battery'] = battery
        return [self._update(rid, 'battery', battery)]

    # ---- 주기 작업: online 타임아웃, 보류된 pose 전송 ----
    def tick(self):
        out = []
        now = self._now()
        for rid, s in self._r.items():
            if s['online'] and now - s['last_seen'] > self._timeout:
                s['online'] = False
                out.append(self._update(rid, 'online', False))
            # 속도 제한 때문에 건너뛴 마지막 pose 가 영영 안 나가는 일을 막는다
            if self._pose_pending[rid] and now - self._pose_sent[rid] >= self._pose_interval:
                out += self._emit_pose(rid, force=True)
        return out

    @staticmethod
    def _update(rid, field, data):
        return {'type': 'robot_update', 'robot': rid, 'field': field, 'data': data}
