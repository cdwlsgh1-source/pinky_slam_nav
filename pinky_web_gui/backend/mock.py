"""--mock: ROS 없이 두 로봇이 사각형 경로를 도는 가짜 데이터를 만든다.

실제 구독과 같은 hub.ingest_* 를 호출하므로 파싱, pose 제한, online 판정이 실제와 똑같이 거친다.
좌표는 모양만 흉내 낸 값이며 실제 지도의 포인트(P1~P7)와 무관하다.
"""
import asyncio
import json
import math
import time

# 사각형 꼭짓점 (m). 로봇마다 시작 위치를 어긋나게 둬서 화면에서 두 마커가 겹치지 않게 한다.
_CORNERS = [(0.0, 0.0), (0.8, 0.0), (0.8, -0.5), (0.0, -0.5)]
_SPEED = 0.2          # m/s
_POSE_HZ = 20         # 일부러 pose_max_hz 보다 높게 발행해 제한이 동작하는지 볼 수 있게 한다.


def _position(distance):
    """사각형 둘레를 따라 distance(m) 만큼 간 위치와 진행 방향(yaw)."""
    sides = []
    for i, (x0, y0) in enumerate(_CORNERS):
        x1, y1 = _CORNERS[(i + 1) % len(_CORNERS)]
        sides.append((x0, y0, x1, y1, math.hypot(x1 - x0, y1 - y0)))
    d = distance % sum(s[4] for s in sides)
    for index, (x0, y0, x1, y1, length) in enumerate(sides):
        if d <= length:
            t = d / length
            return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, math.atan2(y1 - y0, x1 - x0), index
        d -= length
    return _CORNERS[0][0], _CORNERS[0][1], 0.0, 0


async def _run_robot(hub, robot, offset, silence_after):
    started = time.monotonic()
    last_side = None
    battery = 0.9
    next_battery = 0.0
    hub.ingest_patrol(robot, json.dumps({'state': 'IDLE', 'waypoint': -1, 'detail': '', 'time': time.time()}))

    while True:
        elapsed = time.monotonic() - started
        if silence_after is not None and elapsed >= silence_after:
            return   # 이 로봇은 이후 아무것도 보내지 않는다 (online 만료 확인용)

        x, y, yaw, side = _position(elapsed * _SPEED + offset)
        hub.ingest_pose(robot, x, y, yaw)

        if side != last_side:   # 변을 바꿀 때마다 순찰 상태를 한 번 발행한다 (patrol_status 는 변화 시에만 발행)
            last_side = side
            hub.ingest_patrol(robot, json.dumps(
                {'state': 'MOVING', 'waypoint': side, 'detail': f'corner{side}', 'time': time.time()}))

        if elapsed >= next_battery:
            next_battery = elapsed + 1.0
            battery = max(0.0, battery - 0.0005)
            hub.ingest_battery(robot, battery, 6.0 + 2.0 * battery)

        await asyncio.sleep(1.0 / _POSE_HZ)


async def _run_zone(hub, robots):
    """zone 점유자를 번갈아 바꾼다. free 구간도 둔다."""
    i = 0
    while True:
        hub.ingest_zone('free')
        await asyncio.sleep(4)
        hub.ingest_zone(f'occupied_by:{robots[i % len(robots)]}')
        await asyncio.sleep(6)
        i += 1


def start_mock(config, hub, silence=None):
    """mock 태스크를 시작하고 태스크 목록을 돌려준다.

    silence: (robot_id, 초) — 그 로봇이 지정한 초 뒤에 데이터를 멈춘다.
    """
    silent_robot, silent_after = silence if silence else (None, None)
    tasks = []
    for n, robot in enumerate(config.robots):
        offset = n * 1.3   # 로봇마다 사각형 위 시작 위치를 다르게
        after = silent_after if robot == silent_robot else None
        tasks.append(asyncio.create_task(_run_robot(hub, robot, offset, after)))
    tasks.append(asyncio.create_task(_run_zone(hub, list(config.robots))))
    return tasks
