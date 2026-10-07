// 월드(map 프레임, m) <-> 지도 이미지 픽셀 변환. 이 파일이 변환식이 있는 유일한 곳이다 (FR2-2).
// 모든 오버레이(마커, 호버 좌표, 클릭 좌표, 이후 Step 의 경로/구역)는 createTransform 이 돌려준 함수만 쓴다.
//
//   px = (x - origin_x) / res            py = H - (y - origin_y) / res
//   x  = origin_x + px * res             y  = origin_y + (H - py) * res
//
// px, py 는 이미지 왼쪽 위 모서리 기준 연속 좌표다 (픽셀 중심이 아니라 모서리). 이미지 행 0 이 위쪽이다.
(function (root) {
  function createTransform(meta) {
    const res = meta.resolution;
    const ox = meta.origin[0];
    const oy = meta.origin[1];
    const H = meta.height;
    return {
      worldToPixel(x, y) {
        return { px: (x - ox) / res, py: H - (y - oy) / res };
      },
      pixelToWorld(px, py) {
        return { x: ox + px * res, y: oy + (H - py) * res };
      },
    };
  }

  // 지도 YAML 의 임계값으로 occupancy 판정 (map_server trinary 규칙). value: 0~255 그레이 값.
  function classifyOccupancy(value, meta) {
    const p = meta.negate ? value / 255 : (255 - value) / 255;
    if (p > meta.occupied_thresh) return 'occupied';
    if (p < meta.free_thresh) return 'free';
    return 'unknown';
  }

  const api = { createTransform, classifyOccupancy };
  root.MapMath = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
