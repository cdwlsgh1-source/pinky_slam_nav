"""--mock 용 가짜 데이터. ROS 없이 실제와 같은 이벤트를 같은 큐로 흘려 보낸다.

두 로봇(config 의 robots 순서대로)이 사각형 경로를 서로 다른 위상으로 돈다.
좌표는 데모용이며 지도의 통과 가능 여부와 무관하다.
"""
import asyncio
import json
import math
import time

# (x, y) 사각형 꼭짓점. 한 바퀴를 돌면 DONE 후 다시 STARTING.
CORNERS = [(0.0, 0.0), (1.5, 0.0), (1.5, -0.4), (0.0, -0.4)]
SPEED = 0.25        # m/s
POSE_HZ = 20
ZONE_PERIOD = 8.0   # s


def _path_point(dist):
    """사각형 둘레를 dist(m) 만큼 간 지점의 (x, y, yaw, 직전에 지난 꼭짓점 수)."""
    segs = []
    for i, a in enumerate(CORNERS):
        b = CORNERS[(i + 1) % len(CORNERS)]
        segs.append((a, b, math.hypot(b[0] - a[0], b[1] - a[1])))
    total = sum(s[2] for s in segs)
    d = dist % total
    for i, (a, b, ln) in enumerate(segs):
        if d <= ln:
            t = d / ln
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t,
                    math.atan2(b[1] - a[1], b[0] - a[0]), i)
        d -= ln
    return (*CORNERS[0], 0.0, 0)


def _patrol_json(state, waypoint, detail=''):
    return json.dumps({'state': state, 'waypoint': waypoint, 'detail': detail, 'time': time.time()})


async def run_mock(robots, queue):
    """robots 는 config 에서 온 ID 목록. 이벤트 형식은 ros_bridge 와 같다."""
    n = len(robots)
    perimeter = sum(math.hypot(CORNERS[(i + 1) % 4][0] - CORNERS[i][0],
                               CORNERS[(i + 1) % 4][1] - CORNERS[i][1]) for i in range(4))
    last_seg = {rid: None for rid in robots}
    t0 = time.monotonic()
    for rid in robots:
        queue.put_nowait(('patrol', rid, _patrol_json('IDLE', -1)))

    tick = 0
    while True:
        t = time.monotonic() - t0
        for k, rid in enumerate(robots):
            dist = t * SPEED + perimeter * k / n
            x, y, yaw, seg = _path_point(dist)
            queue.put_nowait(('pose', rid, x, y, yaw))
            if seg != last_seg[rid]:
                # start 순찰 형식: 도착한 웨이포인트 다음 번호를 MOVING 으로 발행
                if last_seg[rid] is None:
                    queue.put_nowait(('patrol', rid, _patrol_json('STARTING', -1)))
                if seg == 0 and last_seg[rid] is not None:
                    queue.put_nowait(('patrol', rid, _patrol_json('DONE', len(CORNERS))))
                    queue.put_nowait(('patrol', rid, _patrol_json('STARTING', -1)))
                queue.put_nowait(('patrol', rid, _patrol_json('MOVING', seg + 1)))
                last_seg[rid] = seg
        if tick % POSE_HZ == 0:  # 1초마다 배터리
            for k, rid in enumerate(robots):
                pct = max(0.05, 0.82 - 0.0005 * t - 0.03 * k)
                queue.put_nowait(('battery', rid, pct, 6.0 + 2.0 * pct))
            slot = int(t // ZONE_PERIOD) % (n + 1)
            queue.put_nowait(('zone', 'free' if slot == n else f'occupied_by:{robots[slot]}'))
        tick += 1
        await asyncio.sleep(1.0 / POSE_HZ)
