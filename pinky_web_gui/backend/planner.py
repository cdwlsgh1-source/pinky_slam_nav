"""지도 점유 격자 위의 최단 경로 추정. rclpy 에 의존하지 않는다.

화면의 경로 선과 mock 로봇의 이동이 벽을 뚫지 않게 하려고 쓴다. 로봇의 실제 경로는 Nav2 가 정하고 `/plan` 토픽은 브리지에 없어서,
이 경로는 **추정**이다 (Nav2 의 코스트맵 팽창, 경로 평활화와 다를 수 있다). 벽(occupied)과 미지(unknown) 칸은 지나가지 않고,
벽에서 inflation_m 이내인 칸도 피한다.

알고리즘: 8방향 A* (대각 이동은 모서리를 가로지르지 않을 때만) -> 시야선(line of sight)으로 꺾이는 점을 줄인다.
"""
import heapq
import math

SQRT2 = math.sqrt(2.0)
NEIGHBORS = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
             (1, 1, SQRT2), (1, -1, SQRT2), (-1, 1, SQRT2), (-1, -1, SQRT2))


class NoPath(Exception):
    """시작/도착을 잇는 경로가 없다 (또는 지도 밖)."""


class GridPlanner:
    def __init__(self, meta, pixels, inflation_m=0.08):
        self.w, self.h = meta['width'], meta['height']
        self.res = float(meta['resolution'])
        self.ox, self.oy = meta['origin'][0], meta['origin'][1]
        self.inflation_m = inflation_m
        # 통행 가능 = free 이고(map_server trinary 규칙) 벽에서 inflation 이상 떨어진 칸
        w, h = self.w, self.h
        free_thr, occ_thr = meta['free_thresh'], meta['occupied_thresh']
        blocked = bytearray(w * h)
        for i, v in enumerate(pixels):
            p = v / 255 if meta['negate'] else (255 - v) / 255
            if not p < free_thr:  # occupied 와 unknown 모두 막는다
                blocked[i] = 1
        self._raw_blocked = bytes(blocked)
        r = int(math.ceil(inflation_m / self.res))
        offs = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1) if math.hypot(dx, dy) * self.res <= inflation_m]
        grown = bytearray(blocked)
        occ_cells = [i for i, v in enumerate(pixels) if ((v / 255 if meta['negate'] else (255 - v) / 255) > occ_thr)]
        for i in occ_cells:  # 팽창은 '벽' 에만 적용한다 (미지 칸 주변까지 넓히면 좁은 통로가 막힌다)
            x, y = i % w, i // w
            for dx, dy in offs:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    grown[ny * w + nx] = 1
        self._blocked = grown

    # ---- 좌표 변환 (mapmath.js 의 worldToPixel 과 같은 식을 정수 칸으로) ----
    def to_cell(self, x, y):
        return math.floor((x - self.ox) / self.res), math.floor(self.h - (y - self.oy) / self.res)

    def to_world(self, cx, cy):
        return self.ox + (cx + 0.5) * self.res, self.oy + (self.h - cy - 0.5) * self.res

    def _free(self, cx, cy):
        return 0 <= cx < self.w and 0 <= cy < self.h and not self._blocked[cy * self.w + cx]

    def is_free_world(self, x, y):
        """(x, y) 가 벽(팽창 전)이나 지도 밖이 아닌지. 수동 조작으로 벽 안으로 들어가지 않게 막는 데 쓴다."""
        cx, cy = self.to_cell(x, y)
        return 0 <= cx < self.w and 0 <= cy < self.h and not self._raw_blocked[cy * self.w + cx]

    def _nearest_free(self, cx, cy, limit=12):
        """시작/도착이 팽창 영역 안이면 가장 가까운 통행 가능 칸으로 옮긴다 (limit 칸 안에서)."""
        if self._free(cx, cy):
            return cx, cy
        best = None
        for d in range(1, limit + 1):
            for dx in range(-d, d + 1):
                for dy in range(-d, d + 1):
                    if max(abs(dx), abs(dy)) != d:
                        continue
                    if self._free(cx + dx, cy + dy):
                        dist = math.hypot(dx, dy)
                        if best is None or dist < best[0]:
                            best = (dist, cx + dx, cy + dy)
            if best:
                return best[1], best[2]
        raise NoPath(f'지도 밖이거나 막힌 위치입니다: 칸 ({cx}, {cy})')

    def _line_free(self, a, b):
        (x0, y0), (x1, y1) = a, b
        n = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1  # 칸의 절반 간격으로 표본 추출
        for i in range(n + 1):
            t = i / n
            if not self._free(int(round(x0 + (x1 - x0) * t)), int(round(y0 + (y1 - y0) * t))):
                return False
        return True

    def plan(self, a, b):
        """월드 좌표 a=(x, y), b=(x, y) -> [(x, y), ...] (시작과 끝 포함). 경로가 없으면 NoPath."""
        s = self._nearest_free(*self.to_cell(*a))
        g = self._nearest_free(*self.to_cell(*b))
        w = self.w
        start, goal = s[1] * w + s[0], g[1] * w + g[0]
        h = lambda i: math.hypot(i % w - g[0], i // w - g[1])  # noqa: E731
        dist, prev, heap = {start: 0.0}, {}, [(h(start), start)]
        closed = set()
        while heap:
            _, cur = heapq.heappop(heap)
            if cur == goal:
                break
            if cur in closed:
                continue
            closed.add(cur)
            cx, cy = cur % w, cur // w
            for dx, dy, cost in NEIGHBORS:
                nx, ny = cx + dx, cy + dy
                if not self._free(nx, ny):
                    continue
                if dx and dy and not (self._free(cx + dx, cy) and self._free(cx, cy + dy)):
                    continue  # 대각 이동이 벽 모서리를 스치지 않게
                ni = ny * w + nx
                nd = dist[cur] + cost
                if nd < dist.get(ni, 1e18):
                    dist[ni], prev[ni] = nd, cur
                    heapq.heappush(heap, (nd + h(ni), ni))
        else:
            raise NoPath('경로가 없습니다')
        if goal not in dist:
            raise NoPath('경로가 없습니다')
        cells = [goal]
        while cells[-1] != start:
            cells.append(prev[cells[-1]])
        cells = [(c % w, c // w) for c in reversed(cells)]
        # 시야선으로 불필요한 꺾임을 줄인다 (string pulling)
        keep, i = [cells[0]], 0
        while i < len(cells) - 1:
            j = len(cells) - 1
            while j > i + 1 and not self._line_free(cells[i], cells[j]):
                j -= 1
            keep.append(cells[j])
            i = j
        pts = [self.to_world(cx, cy) for cx, cy in keep]
        pts[0], pts[-1] = tuple(a), tuple(b)  # 양 끝은 요청한 좌표 그대로 (지점 마커와 정확히 이어진다)
        return pts


def path_length(path):
    return sum(math.hypot(path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1]) for i in range(len(path) - 1))
