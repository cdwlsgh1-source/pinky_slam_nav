"""frontend/alarmsound.js (알람 소리의 순수 함수와 컨트롤러, 어댑터) 시험. tests/alarmsound_cases.js 를 headless Chrome 에서 돌린다 (이 PC 에는 node 가 없다).
Chrome 이 없으면 건너뛴다. 실제 소리는 나지 않는다 (가짜 어댑터와 가짜 AudioContext 만 쓴다)."""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CHROME = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')

PAGE = """<!doctype html><meta charset=utf-8><pre id=out>RUNNING</pre>
<script>{lib}</script>
<script>{cases}</script>
<script>
const results = [];
runCases(window.AlarmSound, (name, ok) => results.push([name, !!ok]))
  .then(() => {{ document.getElementById('out').textContent = JSON.stringify({{ results }}); }})
  .catch((e) => {{ document.getElementById('out').textContent = JSON.stringify({{ error: String(e && e.stack || e), results }}); }});
</script>"""


@pytest.mark.skipif(CHROME is None, reason='Chrome 이 없다')
def test_alarmsound_cases_in_chrome():
    page = PAGE.format(lib=(ROOT / 'frontend' / 'alarmsound.js').read_text(encoding='utf-8'),
                       cases=(ROOT / 'tests' / 'alarmsound_cases.js').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / 't.html'
        f.write_text(page, encoding='utf-8')
        out = subprocess.run([CHROME, '--headless=new', '--no-sandbox', '--disable-gpu', '--virtual-time-budget=10000', '--dump-dom', f'file://{f}'],
                             capture_output=True, text=True, timeout=90).stdout
    m = re.search(r'<pre id="out">(.*?)</pre>', out, re.S)
    assert m and m.group(1) != 'RUNNING', out[-300:]
    res = json.loads(m.group(1).replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>'))
    assert 'error' not in res, res.get('error')
    failed = [n for n, ok in res['results'] if not ok]
    assert len(res['results']) >= 104
    assert not failed, failed
