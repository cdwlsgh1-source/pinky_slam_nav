"""경로 추정(벽을 피하는 최단 경로)과 mock 이동이 벽을 뚫지 않는지."""
import asyncio
import math

import pytest

from backend.config import load_config
from backend.map_loader import load_grid
from backend.mock import MockFleet
from backend.planner import GridPlanner, NoPath, path_length

CFG = load_config()
META, PIXELS = load_grid(CFG.map_yaml)
PL = GridPlanner(META, PIXELS, CFG.planner_inflation_m)
RAW = GridPlanner(META, PIXELS, 0.0)       # 팽창 없음: '벽 칸' 만 따진다
PTS = {n: (p['x'], p['y']) for n, p in CFG.points.items()}
PAIRS = [(a, b) for a in PTS for b in PTS if a != b and PTS[a] != PTS[b]]


def samples(path, step=0.005):
    """꺾은선을 step(m) 간격으로 훑는 점들"""
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        for i in range(n + 1):
            t = i / n
            yield x0 + (x1 - x0) * t, y0 + (y1 - y0) * t


def wall_cell(x, y):
    """(x, y) 가 지도 밖이거나 벽/미지 칸인가 (팽창 전)"""
    return not RAW.is_free_world(x, y)


def test_every_point_pair_has_a_path():
    for a, b in PAIRS:
        PL.plan(PTS[a], PTS[b])  # NoPath 가 나면 실패


@pytest.mark.parametrize('a,b', PAIRS)
def test_path_never_touches_a_wall(a, b):
    path = PL.plan(PTS[a], PTS[b])
    bad = [(round(x, 3), round(y, 3)) for x, y in samples(path) if wall_cell(x, y)]
    assert not bad, f'{a}>{b} 가 벽/미지 칸을 지난다: {bad[:3]}'


def test_endpoints_are_the_requested_coordinates():
    path = PL.plan(PTS['P1'], PTS['P2'])
    assert path[0] == PTS['P1'] and path[-1] == PTS['P2']


def test_p3_to_p6_detours_around_the_inner_wall():
    """지도의 가로 벽(y≈-0.2) 때문에 P3→P6 직선은 벽을 뚫는다. 경로는 벽 틈으로 돌아가야 한다."""
    straight = [PTS['P3'], PTS['P6']]
    assert any(wall_cell(x, y) for x, y in samples(straight)), '직선이 벽을 지나지 않으면 이 시험의 전제가 틀렸다'
    path = PL.plan(PTS['P3'], PTS['P6'])
    assert len(path) > 2 and path_length(path) > 1.5 * math.hypot(PTS['P3'][0] - PTS['P6'][0], PTS['P3'][1] - PTS['P6'][1])


def test_clear_line_stays_straight():
    path = PL.plan(PTS['P1'], PTS['P2'])
    assert len(path) == 2


def test_keeps_inflation_clearance_in_planned_cells():
    """꺾이는 점(시야선 단순화 뒤)이 벽에서 inflation 이상 떨어져 있다 (지점 자체는 요청 좌표라 제외)"""
    path = PL.plan(PTS['P3'], PTS['P6'])
    for x, y in path[1:-1]:
        cx, cy = PL.to_cell(x, y)
        assert PL._free(cx, cy)


def test_unreachable_or_outside_raises():
    with pytest.raises(NoPath):
        PL.plan((50.0, 50.0), PTS['P1'])           # 지도 밖
    with pytest.raises(NoPath):
        PL.plan(PTS['P1'], (-50.0, 0.0))


def test_is_free_world():
    assert PL.is_free_world(*PTS['P1'])
    assert not PL.is_free_world(50.0, 50.0)
    assert not PL.is_free_world(-0.15, 0.0)         # 왼쪽 벽 (x=-0.167 부근)


# ---- mock 이 벽을 뚫지 않는다 ----
def test_mock_goto_moves_along_planned_path_without_crossing_walls():
    async def go():
        q = asyncio.Queue()
        fleet = MockFleet(CFG, q, wait_scale=0.0, speed=30.0, calib_sec=0.2, planner=PL)
        bad, task = [], asyncio.create_task(fleet.run())

        async def watch():
            while True:
                for p in fleet._pose.values():
                    if wall_cell(p[0], p[1]):
                        bad.append(p[:2])
                await asyncio.sleep(0.01)

        w = asyncio.create_task(watch())
        await asyncio.sleep(0.1)
        fleet.publish_cmd('pinky1', 'goto:P3,P6')
        end = asyncio.get_event_loop().time() + 20
        done = False
        while asyncio.get_event_loop().time() < end and not done:
            while not q.empty():
                e = q.get_nowait()
                if e[0] == 'patrol' and e[1] == 'pinky1' and '"DONE"' in e[2]:
                    done = True
            await asyncio.sleep(0.02)
        for t in (task, w):
            t.cancel()
        await asyncio.gather(task, w, return_exceptions=True)
        assert done, 'goto 가 끝나지 않았다'
        assert not bad, f'mock 로봇이 벽/미지 칸에 들어갔다: {bad[:3]}'
    asyncio.run(go())


def test_mock_manual_drive_cannot_enter_a_wall():
    async def go():
        q = asyncio.Queue()
        fleet = MockFleet(CFG, q, planner=PL)
        task = asyncio.create_task(fleet.run())
        await asyncio.sleep(0.1)
        fleet._pose['pinky1'] = (-0.10, 0.0, math.pi)   # P1 근처에서 왼쪽 벽을 향해 서 있다
        fleet.publish_twist('pinky1', 0.2, 0.0)
        await asyncio.sleep(1.0)                        # 0.2 m/s 로 1초면 벽을 지나 버릴 거리
        x, y, _ = fleet._pose['pinky1']
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert not wall_cell(x, y) and x > -0.17
    asyncio.run(go())
