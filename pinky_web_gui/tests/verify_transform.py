"""frontend/mapmath.js 의 변환이 기존 map_view_pc/pinky_map_viewer.html, PRD 식과 같은지 headless Chrome 으로 검증한다.

실행: .venv/bin/python tests/verify_transform.py   (google-chrome 필요, 없으면 SKIP)
- 뷰어 html 에서 META, xMin/xMax/yMin/yMax, pxToMap, mapToPx 를 정규식으로 그대로 꺼내 실행한다 (복사본이 아니라 원본 텍스트).
- 뷰어의 META 가 map yaml/pgm(백엔드가 /api/map 으로 내려주는 값)과 같은지도 확인한다.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.config import load_config  # noqa: E402
from backend.map_loader import load_map  # noqa: E402

VIEWER = ROOT.parent / 'map_view_pc' / 'pinky_map_viewer.html'


def main():
    chrome = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not chrome:
        print('SKIP: Chrome 이 없다')
        return 0
    src = VIEWER.read_text(encoding='utf-8')
    parts = [
        re.search(r'const META = \{.*?\};', src, re.S).group(0),
        re.search(r'const xMin = [^\n]*\n', src).group(0),
        re.search(r'const xMax = [^\n]*\n', src).group(0),
        re.search(r'const yMax = [^\n]*\n', src).group(0),
        re.search(r'function pxToMap\(.*?\n\}', src, re.S).group(0),
        re.search(r'function mapToPx\(.*?\n\}', src, re.S).group(0),
    ]
    meta, _ = load_map(load_config().map_yaml)
    page = f"""<!doctype html><meta charset=utf-8><pre id=out>RUNNING</pre>
<script>{(ROOT / 'frontend' / 'mapmath.js').read_text(encoding='utf-8')}</script>
<script>
{chr(10).join(parts)}
const API = {json.dumps(meta)};
const viewerMeta = {{resolution: META.resolution, origin: META.origin, height: META.image_h, width: META.image_w}};
const tf = MapMath.createTransform(API);
let worst = 0, n = 0, bad = [];
function chk(name, a, b) {{ const d = Math.abs(a - b); worst = Math.max(worst, d); n++; if (d > 1e-9) bad.push(name); }}
for (let py = 0; py <= API.height; py += 0.5) for (let px = 0; px <= API.width; px += 0.5) {{
  const w = tf.pixelToWorld(px, py), v = pxToMap(px, py);            // 픽셀 -> 월드: 뷰어와 비교
  chk('x', w.x, v[0]); chk('y', w.y, v[1]);
  const p = tf.worldToPixel(w.x, w.y), q = mapToPx(w.x, w.y);        // 월드 -> 픽셀: 뷰어와 비교
  chk('px', p.px, q[0]); chk('py', p.py, q[1]);
  chk('prd_px', p.px, (w.x - API.origin[0]) / API.resolution);       // PRD 식과 직접 비교
  chk('prd_py', p.py, API.height - (w.y - API.origin[1]) / API.resolution);
  chk('rt_px', p.px, px); chk('rt_py', p.py, py);                    // 왕복 변환
}}
const metaSame = viewerMeta.resolution === API.resolution && viewerMeta.height === API.height
  && viewerMeta.width === API.width && viewerMeta.origin.every((v, i) => v === API.origin[i]);
document.getElementById('out').textContent = JSON.stringify({{checks: n, worst, bad: [...new Set(bad)], metaSame, viewerMeta, apiMeta: {{resolution: API.resolution, origin: API.origin, w: API.width, h: API.height}}}});
</script>"""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / 't.html'
        f.write_text(page, encoding='utf-8')
        out = subprocess.run([chrome, '--headless=new', '--no-sandbox', '--disable-gpu', '--dump-dom', f'file://{f}'],
                             capture_output=True, text=True, timeout=60).stdout
    m = re.search(r'<pre id="out">(.*?)</pre>', out, re.S)
    if not m or m.group(1) == 'RUNNING':
        print('FAIL: 결과를 읽지 못했다', out[-300:])
        return 1
    res = json.loads(m.group(1).replace('&quot;', '"'))
    print(json.dumps(res, ensure_ascii=False, indent=1))
    ok = not res['bad'] and res['metaSame'] and res['worst'] < 1e-9
    print('PASS' if ok else 'FAIL')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
