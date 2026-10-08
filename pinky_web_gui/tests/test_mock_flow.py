"""mock 노드 시뮬레이터 흐름 테스트 (ROS 불필요). 시간은 빠르게 줄여서 돌린다."""
import asyncio
import json
import time

from backend.config import load_config
from backend.mock import MockFleet

CFG = load_config()


class Run:
    """MockFleet 을 띄우고 큐에 나온 patrol/zone 이벤트를 모아 둔다."""

    def __init__(self, calib=0.3, wait_scale=0.02, speed=20.0):
        self.queue = asyncio.Queue()
        self.fleet = MockFleet(CFG, self.queue, wait_scale=wait_scale, speed=speed, calib_sec=calib)
        self.patrol = {rid: [] for rid in CFG.robots}   # rid -> [(t, state, waypoint, detail)]
        self.zone = []
        self.order = []                                  # 전체 순서: (rid|'zone', 값)
        self._tasks = []

    async def __aenter__(self):
        self._tasks = [asyncio.create_task(self.fleet.run()), asyncio.create_task(self._collect())]
        await asyncio.sleep(0.1)
        return self

    async def __aexit__(self, *exc):
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _collect(self):
        while True:
            e = await self.queue.get()
            if e[0] == 'patrol':
                d = json.loads(e[2])
                v = (time.monotonic(), d['state'], d['waypoint'], d['detail'])
                self.patrol[e[1]].append(v)
                self.order.append((e[1], v[1:]))
            elif e[0] == 'zone':
                self.zone.append(e[1])
                self.order.append(('zone', e[1]))

    def states(self, rid):
        return [(s, w, d) for _, s, w, d in self.patrol[rid]]

    async def until(self, rid, state, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(s == state for _, s, _, _ in self.patrol[rid]):
                return
            await asyncio.sleep(0.02)
        raise AssertionError(f'{rid} {state} 에 도달하지 못함: {self.states(rid)}')


def run(coro):
    return asyncio.run(coro)


def test_initial_state_idle_at_home_pose():
    async def go():
        async with Run() as r:
            assert r.states('pinky1') == [('IDLE', -1, '')]
            assert r.zone[0] == 'free'
    run(go())


def test_start_pinky1_runs_route_to_done():
    async def go():
        async with Run() as r:
            r.fleet.publish_cmd('pinky1', 'start')
            await r.until('pinky1', 'DONE')
            st = r.states('pinky1')
            assert st[1] == ('STARTING', -1, '')
            assert ('WAITING_ZONE', 2, '') in st or ('WAITING_ZONE', 1, '') in st
            assert st[-1] == ('DONE', 6, '')  # pinky1 경로는 6개
            assert [s for s in st if s[0] == 'MOVING'][-1] == ('MOVING', 6, '')
            assert r.zone[-1] == 'free' and 'occupied_by:pinky1' in r.zone
    run(go())


def test_start_pinky2_has_five_waypoints():
    async def go():
        async with Run() as r:
            r.fleet.publish_cmd('pinky2', 'start')
            await r.until('pinky2', 'DONE')
            st = r.states('pinky2')
            assert st[-1] == ('DONE', 5, '')
            assert ('WAITING_ZONE', 0, '') in st  # RED2IN 이 첫 웨이포인트
    run(go())


def test_stop_during_motion_gives_stopped():
    async def go():
        async with Run(calib=0.1, speed=0.5) as r:
            r.fleet.publish_cmd('pinky1', 'start')
            await r.until('pinky1', 'STARTING')
            await asyncio.sleep(0.6)
            r.fleet.publish_cmd('pinky1', 'stop')
            await r.until('pinky1', 'STOPPED')
            assert r.states('pinky1')[-1][0] == 'STOPPED'
            r.fleet.publish_cmd('pinky1', 'start')  # 끝난 뒤에는 다시 시작할 수 있다
            await r.until('pinky1', 'MOVING')
    run(go())


def test_busy_start_and_goto_silently_ignored_and_idle_stop_ignored():
    async def go():
        async with Run(calib=0.1, speed=0.5) as r:
            r.fleet.publish_cmd('pinky1', 'stop')  # 작업 중이 아니면 무시
            await asyncio.sleep(0.2)
            assert r.states('pinky1') == [('IDLE', -1, '')]
            r.fleet.publish_cmd('pinky1', 'start')
            await asyncio.sleep(0.4)
            n = len(r.patrol['pinky1'])
            r.fleet.publish_cmd('pinky1', 'start')
            r.fleet.publish_cmd('pinky1', 'goto:P2')
            await asyncio.sleep(0.2)
            assert len(r.patrol['pinky1']) == n  # 발행되는 상태 없음
            r.fleet.publish_cmd('pinky1', 'stop')
            await r.until('pinky1', 'STOPPED')
    run(go())


def test_first_task_calibration_is_long_and_second_is_short():
    async def go():
        async with Run(calib=0.5) as r:
            r.fleet.publish_cmd('pinky1', 'goto:P1')
            await r.until('pinky1', 'DONE')
            p = r.patrol['pinky1']
            starting = next(v for v in p if v[1] == 'STARTING')
            after = next(v for v in p if v[0] > starting[0] and v[1] != 'STARTING')
            assert after[0] - starting[0] >= 0.5  # 첫 작업: 보정 시간만큼 STARTING 유지
            n = len(p)
            r.fleet.publish_cmd('pinky1', 'goto:P1')
            await asyncio.sleep(1.4)
            q = r.patrol['pinky1'][n:]
            s2 = next(v for v in q if v[1] == 'STARTING')
            a2 = next(v for v in q if v[0] > s2[0] and v[1] != 'STARTING')
            assert a2[0] - s2[0] < 0.5 + 1.0  # 두 번째 이후는 보정 없음 (1초 대기)
    run(go())


def test_goto_multi_point_sequence_with_zone_and_home():
    async def go():
        async with Run() as r:
            r.fleet.publish_cmd('pinky1', 'goto:P2,P3,P6')
            await r.until('pinky1', 'DONE')
            st = r.states('pinky1')
            assert st[1] == ('STARTING', -1, 'P2,P3,P6')
            expected = [
                ('MOVING', 0, 'P2'), ('ARRIVED', 0, 'P2'),
                ('MOVING', 1, 'RED1IN'), ('WAITING_ZONE', 1, 'P3'),
                ('MOVING', 1, 'P3'), ('ARRIVED', 1, 'P3'),
                ('MOVING', 2, 'P6'), ('ARRIVED', 2, 'P6'),
                ('LEAVING_ZONE', 2, 'RED1OUT'),
                ('RETURNING', -1, 'P1'), ('DONE', -1, 'returned home'),
            ]
            assert st[2:] == expected
            assert r.zone == ['free', 'occupied_by:pinky1', 'free']
    run(go())


def test_goto_pinky2_returns_to_its_own_home():
    async def go():
        async with Run() as r:
            r.fleet.publish_cmd('pinky2', 'goto:P2')
            await r.until('pinky2', 'DONE')
            assert ('RETURNING', -1, 'P7') in r.states('pinky2')
    run(go())


def test_stop_during_goto_gives_stopped_with_point():
    async def go():
        async with Run(calib=0.1, wait_scale=1.0) as r:
            r.fleet.publish_cmd('pinky1', 'goto:P2,P1')
            await r.until('pinky1', 'ARRIVED')  # 지점 대기(10초) 중
            r.fleet.publish_cmd('pinky1', 'stop')
            await r.until('pinky1', 'STOPPED')
            assert r.states('pinky1')[-1] == ('STOPPED', 0, 'P2')
    run(go())


def test_stop_during_calibration_is_not_honored_until_it_ends():
    async def go():
        async with Run(calib=0.5) as r:
            r.fleet.publish_cmd('pinky1', 'start')
            await r.until('pinky1', 'STARTING')
            t_stop = time.monotonic()
            r.fleet.publish_cmd('pinky1', 'stop')
            await r.until('pinky1', 'STOPPED')
            t_stopped = next(t for t, s, _, _ in r.patrol['pinky1'] if s == 'STOPPED')
            assert t_stopped - t_stop >= 0.3  # 보정 회전이 끝난 뒤에야 반영
    run(go())


def test_invalid_goto_gives_failed_even_while_busy():
    async def go():
        async with Run(calib=0.1, speed=0.5) as r:
            f = r.fleet
            f.publish_cmd('pinky1', 'goto:')
            f.publish_cmd('pinky1', 'goto:P99')
            f.publish_cmd('pinky1', 'goto:RED1IN')
            await asyncio.sleep(0.1)
            assert r.states('pinky1')[1:] == [('FAILED', -1, 'no point given'),
                                              ('FAILED', -1, "unknown point: ['P99']"),
                                              ('FAILED', -1, "not allowed: ['RED1IN']")]
            f.publish_cmd('pinky1', 'start')
            await asyncio.sleep(0.3)
            n = len(r.patrol['pinky1'])
            f.publish_cmd('pinky1', 'goto:RED1IN')  # 작업 중이어도 검증이 먼저라 FAILED 가 나온다 (노드의 함정)
            await asyncio.sleep(0.1)
            assert r.states('pinky1')[n] == ('FAILED', -1, "not allowed: ['RED1IN']")
            f.publish_cmd('pinky1', 'stop')
            await r.until('pinky1', 'STOPPED')
    run(go())


def test_two_robots_contend_for_zone_one_waits():
    async def go():
        async with Run(calib=0.1, wait_scale=0.3) as r:
            r.fleet.publish_cmd('pinky1', 'goto:P3')
            r.fleet.publish_cmd('pinky2', 'goto:P3')
            await r.until('pinky1', 'DONE', timeout=10)
            await r.until('pinky2', 'DONE', timeout=10)
            waits = [rid for rid in CFG.robots if any(s == 'WAITING_ZONE' for s, _, _ in r.states(rid))]
            assert waits == ['pinky1', 'pinky2']  # 둘 다 문에서 WAITING_ZONE 을 거치지만
            z = r.zone
            assert z == ['free', 'occupied_by:pinky1', 'free', 'occupied_by:pinky2', 'free']
            # 두 번째 로봇의 ARRIVED 는 첫 번째 로봇이 LEAVING_ZONE 한 뒤다 (동시에 구역 안에 있지 않다)
            idx = {k: i for i, k in enumerate(r.order)}
            assert idx[('pinky1', ('LEAVING_ZONE', 0, 'RED1OUT'))] < idx[('pinky2', ('ARRIVED', 0, 'P3'))]
    run(go())
