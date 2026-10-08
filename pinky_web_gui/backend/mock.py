"""--mock 용 순찰 노드 시뮬레이터. ROS 없이 실제 노드와 같은 이벤트를 같은 큐로 흘려 보낸다.

로봇은 홈 지점에서 IDLE 로 정지해 있다가 publish_cmd 로 받은 명령(`start`, `stop`, `goto:...`)대로 움직인다.
`pinky_patrol_node_pinky1_v2.py` 의 `_cmd_cb`, `_run_patrol`, `_run_route` 동작을 따른다:
  - 작업 중에 오는 start/goto 는 조용히 무시 (상태 발행 없음), 작업 중이 아닐 때의 stop 도 무시
  - goto 입력 검증(`no point given`/`unknown point`/`not allowed`)은 작업 중 확인보다 먼저 한다
  - 노드를 켠 뒤 첫 작업은 초기 위치 보정(제자리 회전)으로 STARTING 이 길고 그 동안 stop 을 확인하지 않는다
  - 구역(P3~P6) 락은 로봇끼리 공유한다. 이미 점유 중이면 WAITING_ZONE 으로 기다린다
실제 노드와 다른 점: stop 으로 끝날 때 구역 락을 즉시 반납한다 (실제는 max_hold_sec 타임아웃에 의존).
이동은 벽을 무시하는 직선 보간이다. 배터리 percentage 는 실제 로봇과 같이 0~100 단위다.

Step 4: cmd_vel 을 받으면 순찰 중이 아닌 로봇의 pose 를 적분한다. 마지막 cmd_vel 이 계속 유지된다 (로봇 쪽 타임아웃이 있는지
코드로 확인되지 않아서, 0 속도가 안 오면 계속 움직이는 쪽으로 가정한다). 순찰 중에는 cmd_vel 을 무시한다 (실제 노드와 cmd_vel 이
경합하는 상황은 흉내 내지 않는다). LiDAR 는 스캔을 켠 로봇에 대해 지도 점유 격자에 레이캐스트해서 만든다 (벽 위에 점이 떨어진다).
"""
import asyncio
import json
import logging
import math
import time
from collections import deque

from .map_loader import load_grid
from .scan import compact, rot_z

log = logging.getLogger('backend.mock')

POSE_HZ = 20
STEP_SEC = 1.0 / POSE_HZ
ZONE_POLL_SEC = 0.1

SCAN_BEAMS = 360
SCAN_RANGE_MIN = 0.05
SCAN_RANGE_MAX = 3.0
SCAN_STEP_M = 0.03   # 레이캐스트 전진 간격 (적중 뒤 이분법으로 다듬는다)


class _Stopped(Exception):
    """작업 중 stop 이 요청됐다."""


class MockFleet:
    def __init__(self, cfg, queue, wait_scale=1.0, speed=0.5, calib_sec=6.0, planner=None, scan_mount_yaw=0.0, scan_mount_tf=True):
        self._cfg = cfg
        self._queue = queue
        self._scan_mount_tf = scan_mount_tf    # False 면 센서가 돌려 달렸어도 tf 를 주지 않는다 (amcl + 보정 각도 대체 경로 시험용)
        self._scan_mount_yaw = scan_mount_yaw  # 가짜 센서가 로봇 정면에서 돌아 달린 각도(rad). 0 이 아니면 tf 경로로 보낸다
        self._planner = planner      # 있으면 지점 사이를 벽을 피하는 경로로 이동한다 (없으면 직선)
        self._wait_scale = wait_scale
        self._speed = speed          # m/s
        self._calib_sec = calib_sec  # 첫 작업의 초기 위치 보정 시간 (STARTING 이 길어지는 구간)
        self._robots = tuple(cfg.robots)
        self._points = cfg.points
        self._pose = {}
        self._running = {rid: False for rid in self._robots}
        self._stop = {rid: False for rid in self._robots}
        self._initialized = {rid: False for rid in self._robots}
        self._cur = {rid: (-1, '') for rid in self._robots}  # stop 시 STOPPED 에 실을 (waypoint, detail)
        self._tasks = {}
        self._zone_owner = None
        self._bg = []
        self._vel = {rid: (0.0, 0.0) for rid in self._robots}   # 마지막으로 받은 cmd_vel (선속도, 각속도)
        self.cmd_vel_log = deque(maxlen=1000)                    # (시각, rid, 선속도, 각속도). 테스트가 읽는다
        self.cmd_log = deque(maxlen=200)                         # (시각, rid, patrol_cmd 문자열)
        self._scan_on = set()
        self._grid = None
        if cfg.map_yaml:
            try:
                self._grid = load_grid(cfg.map_yaml)
            except Exception:
                log.exception('mock LiDAR 용 지도를 읽지 못했다: %s', cfg.map_yaml)

    # ---- 노드 쪽 명령 입구 (RosBridge.publish_cmd 와 같은 시그니처) ----
    def publish_cmd(self, rid, data):
        """`_cmd_cb` 와 같은 규칙으로 처리한다. 반드시 이벤트 루프 스레드에서 호출한다."""
        if rid not in self._running:
            raise KeyError(rid)
        self.cmd_log.append((time.time(), rid, data))
        raw = data.strip()
        cmd = raw.lower()
        if cmd == 'start':
            if self._running[rid]:
                log.info('%s 이미 작업 중, start 무시', rid)
                return
            self._begin(rid, self._run_patrol(rid))
        elif cmd.startswith('goto:'):
            names = raw.split(':', 1)[1].replace(',', ' ').upper().split()
            allowed = {n for n in self._cfg.robot_settings[rid].goto_allowed}
            if not names:
                self._status(rid, 'FAILED', -1, 'no point given')
                return
            unknown = [n for n in names if n not in self._points]
            if unknown:
                self._status(rid, 'FAILED', -1, f'unknown point: {unknown}')
                return
            denied = [n for n in names if n not in allowed]
            if denied:
                self._status(rid, 'FAILED', -1, f'not allowed: {denied}')
                return
            if self._running[rid]:
                log.info('%s 작업 중, goto 무시', rid)
                return
            self._begin(rid, self._run_route(rid, names))
        elif cmd == 'stop':
            if self._running[rid]:
                self._stop[rid] = True
            else:
                log.info('%s 작업 중이 아니라 stop 무시', rid)
        else:
            log.info('%s 알 수 없는 명령 무시: %r', rid, raw)

    # ---- Step 4: cmd_vel, LiDAR (RosBridge 와 같은 시그니처) ----
    def publish_twist(self, rid, linear, angular):
        if rid not in self._vel:
            raise KeyError(rid)
        self.cmd_vel_log.append((time.time(), rid, float(linear), float(angular)))
        self._vel[rid] = (float(linear), float(angular))
        return True

    def scan_enable(self, rid, on):
        if rid not in self._vel:
            raise KeyError(rid)
        (self._scan_on.add if on else self._scan_on.discard)(rid)

    def scan_subscribed(self):
        return sorted(self._scan_on)

    def _is_occupied(self, x, y):
        meta, pixels = self._grid
        res, ox, oy, h, w = meta['resolution'], meta['origin'][0], meta['origin'][1], meta['height'], meta['width']
        col, row = math.floor((x - ox) / res), math.floor(h - (y - oy) / res)   # mapmath.js 의 worldToPixel 과 같은 식을 정수 셀로
        if not (0 <= col < w and 0 <= row < h):
            return False
        v = pixels[row * w + col]
        p = v / 255 if meta['negate'] else (255 - v) / 255
        return p > meta['occupied_thresh']

    def _raycast(self, x, y, ang):
        """(x, y) 에서 ang 방향으로 처음 만나는 점유 셀까지의 거리. 없으면 inf."""
        d = SCAN_RANGE_MIN
        cx, cy = math.cos(ang), math.sin(ang)
        prev = d
        while d <= SCAN_RANGE_MAX:
            if self._is_occupied(x + d * cx, y + d * cy):
                lo, hi = prev, d
                for _ in range(6):  # 이분법으로 경계까지 다듬는다 (오차 < 1 mm)
                    mid = (lo + hi) / 2
                    if self._is_occupied(x + mid * cx, y + mid * cy):
                        hi = mid
                    else:
                        lo = mid
                return hi
            prev = d
            d += SCAN_STEP_M
        return float('inf')

    def _emit_scans(self):
        if self._grid is None:
            return
        step = self._cfg.motion.scan_decimate
        for rid in sorted(self._scan_on):
            pose = self._pose.get(rid)
            if pose is None:
                continue
            x, y, yaw = pose
            inc = 2 * math.pi / SCAN_BEAMS
            sensor_yaw = yaw + self._scan_mount_yaw
            ranges = [self._raycast(x, y, sensor_yaw + i * inc) for i in range(SCAN_BEAMS)]  # 각도 0 = 센서 앞(+x)
            # 센서가 돌려 달려 있으면 map -> 센서 변환(tf)을 같이 보낸다. 아니면 None (amcl_pose 로 그린다)
            tf = (rot_z(sensor_yaw), (x, y, 0.0)) if self._scan_mount_yaw and self._scan_mount_tf else None
            self._queue.put_nowait(('scan', rid, tf, 'mock_laser') + compact(0.0, inc, ranges, step, SCAN_RANGE_MIN, SCAN_RANGE_MAX))

    def _drift(self):
        """순찰 중이 아닌 로봇은 마지막 cmd_vel 로 움직인다."""
        for rid in self._robots:
            lin, ang = self._vel[rid]
            if self._running[rid] or (lin == 0.0 and ang == 0.0) or rid not in self._pose:
                continue
            x, y, yaw = self._pose[rid]
            yaw += ang * STEP_SEC
            nx, ny = x + lin * math.cos(yaw) * STEP_SEC, y + lin * math.sin(yaw) * STEP_SEC
            if self._planner is not None and not self._planner.is_free_world(nx, ny):
                nx, ny = x, y  # 벽 안으로는 들어가지 않는다 (제자리 회전은 가능)
            self._pose[rid] = (nx, ny, yaw)

    def _begin(self, rid, coro):
        self._stop[rid] = False
        self._running[rid] = True  # 코루틴이 시작되기 전에 세팅 (연타 시 중복 실행 방지)
        self._cur[rid] = (-1, '')

        async def wrapper():
            try:
                await coro
            except _Stopped:
                wp, detail = self._cur[rid]
                self._status(rid, 'STOPPED', wp, detail)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('%s mock 작업 오류', rid)
                self._status(rid, 'FAILED', -1, 'mock error')
            finally:
                self._release_zone(rid)
                self._running[rid] = False

        self._tasks[rid] = asyncio.create_task(wrapper())

    # ---- 이벤트 ----
    def _status(self, rid, state, waypoint, detail=''):
        payload = json.dumps({'state': state, 'waypoint': waypoint, 'detail': detail, 'time': time.time()})
        self._queue.put_nowait(('patrol', rid, payload))

    def _release_zone(self, rid):
        if self._zone_owner == rid:
            self._zone_owner = None
            self._queue.put_nowait(('zone', 'free'))

    # ---- 기본 동작 ----
    def _check_stop(self, rid):
        if self._stop[rid]:
            raise _Stopped()

    async def _sleep(self, rid, sec, honor_stop=True):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            if honor_stop:
                self._check_stop(rid)
            await asyncio.sleep(min(STEP_SEC, max(0.0, end - time.monotonic())))
        if honor_stop:
            self._check_stop(rid)

    async def _move(self, rid, name):
        """name 지점까지 간다. 지도가 있으면 벽을 피하는 추정 경로(backend.planner)를 따라가고, 없으면 직선이다. stop 이 오면 _Stopped."""
        p = self._points[name]
        tx, ty = p['x'], p['y']
        x, y, yaw = self._pose[rid]
        path = [(x, y), (tx, ty)]
        if self._planner is not None:
            try:
                path = self._planner.plan((x, y), (tx, ty))
            except Exception:
                log.warning('%s 경로 추정 실패, 직선으로 이동: %s', rid, name)
        for (ax, ay), (bx, by) in zip(path, path[1:]):
            dist = math.hypot(bx - ax, by - ay)
            if dist <= 1e-6:
                continue
            yaw = math.atan2(by - ay, bx - ax)
            steps = max(1, int(dist / self._speed / STEP_SEC))
            for i in range(1, steps + 1):
                self._check_stop(rid)
                t = i / steps
                self._pose[rid] = (ax + (bx - ax) * t, ay + (by - ay) * t, yaw)
                await asyncio.sleep(STEP_SEC)
        self._check_stop(rid)

    async def _init_pose_once(self, rid):
        """첫 작업의 초기 위치 보정. 제자리 회전 동안 stop 을 확인하지 않는다 (실제 노드와 같음)."""
        if self._initialized[rid]:
            await self._sleep(rid, 1.0)  # Nav2 활성 대기 정도
            return
        self._initialized[rid] = True
        x, y, yaw = self._pose[rid]
        steps = max(1, int(self._calib_sec / STEP_SEC))
        for i in range(steps):
            self._pose[rid] = (x, y, yaw + 2 * math.pi * (i + 1) / steps)
            await asyncio.sleep(STEP_SEC)
        self._pose[rid] = (x, y, yaw)
        self._check_stop(rid)

    def _doors(self, rid):
        """로봇의 구역 문 이름 (start_route 의 *IN, *OUT)."""
        route = self._cfg.robot_settings[rid].start_route
        door_in = next((n for n in route if self._points[n]['door'] and n.endswith('IN')), None)
        door_out = next((n for n in route if self._points[n]['door'] and n.endswith('OUT')), None)
        return door_in, door_out

    async def _enter_zone(self, rid, door_in, wp, name):
        """문까지 가서 락을 기다린다 (notice: WAITING_ZONE)."""
        await self._move(rid, door_in)
        self._status(rid, 'WAITING_ZONE', wp, name)
        self._cur[rid] = (wp, name)
        while self._zone_owner not in (None, rid):
            await self._sleep(rid, ZONE_POLL_SEC)
        self._zone_owner = rid
        self._queue.put_nowait(('zone', f'occupied_by:{rid}'))

    async def _leave_zone(self, rid, door_out, wp):
        self._status(rid, 'LEAVING_ZONE', wp, door_out)
        self._cur[rid] = (wp, door_out)
        await self._move(rid, door_out)
        self._release_zone(rid)

    # ---- start: start_route 순회 (_run_patrol) ----
    async def _run_patrol(self, rid):
        route = self._cfg.robot_settings[rid].start_route
        door_in, door_out = self._doors(rid)
        self._status(rid, 'STARTING', -1)
        await self._init_pose_once(rid)
        for i, name in enumerate(route):
            self._cur[rid] = (i, '')
            await self._move(rid, name)
            self._status(rid, 'MOVING', i + 1)  # 도착한 웨이포인트 다음 번호, 도착 후 발행
            if name == door_in:
                self._status(rid, 'WAITING_ZONE', i)
                while self._zone_owner not in (None, rid):
                    await self._sleep(rid, ZONE_POLL_SEC)
                self._zone_owner = rid
                self._queue.put_nowait(('zone', f'occupied_by:{rid}'))
            elif name == door_out:
                self._release_zone(rid)
        self._status(rid, 'DONE', len(route))

    # ---- goto: 지점 순서대로 -> 홈 (_run_route) ----
    async def _run_route(self, rid, names):
        s = self._cfg.robot_settings[rid]
        door_in, door_out = self._doors(rid)
        self._status(rid, 'STARTING', -1, ','.join(names))
        await self._init_pose_once(rid)
        in_zone = False
        for idx, name in enumerate(names):
            self._cur[rid] = (idx, name)
            target_in = self._points[name]['in_zone']
            if not in_zone and target_in:
                self._status(rid, 'MOVING', idx, door_in)
                await self._enter_zone(rid, door_in, idx, name)
                in_zone = True
            elif in_zone and not target_in:
                await self._leave_zone(rid, door_out, idx)
                in_zone = False
            self._cur[rid] = (idx, name)
            self._status(rid, 'MOVING', idx, name)
            await self._move(rid, name)
            self._status(rid, 'ARRIVED', idx, name)
            if s.wait_every_point or idx == len(names) - 1:
                await self._sleep(rid, s.goto_wait_sec * self._wait_scale)
        if in_zone:
            await self._leave_zone(rid, door_out, len(names) - 1)
        self._cur[rid] = (-1, 'return')
        self._status(rid, 'RETURNING', -1, s.home)
        await self._move(rid, s.home)
        self._status(rid, 'DONE', -1, 'returned home')

    # ---- 배경: pose, 배터리, zone 초기값 ----
    async def run(self):
        for rid in self._robots:
            home = self._points[self._cfg.robot_settings[rid].home]
            self._pose[rid] = (home['x'], home['y'], 0.0)
            self._status(rid, 'IDLE', -1)
        self._queue.put_nowait(('zone', 'free'))
        t0 = time.monotonic()
        tick = 0
        scan_every = max(1, round(POSE_HZ / self._cfg.motion.scan_max_hz))
        try:
            while True:
                self._drift()
                if tick % scan_every == 0 and self._scan_on:
                    self._emit_scans()
                for rid in self._robots:
                    x, y, yaw = self._pose[rid]
                    self._queue.put_nowait(('pose', rid, x, y, yaw))
                if tick % POSE_HZ == 0:  # 1초마다 배터리
                    t = time.monotonic() - t0
                    for k, rid in enumerate(self._robots):
                        pct = max(5.0, 82.0 - 0.05 * t - 3.0 * k)
                        self._queue.put_nowait(('battery', rid, pct, 6.0 + 2.0 * pct / 100))
                tick += 1
                await asyncio.sleep(STEP_SEC)
        finally:
            for t in self._tasks.values():
                t.cancel()
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
