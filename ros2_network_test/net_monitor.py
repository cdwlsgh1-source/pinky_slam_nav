#!/usr/bin/env python3
"""ros2_net_diag.sh 가 만든 로그 폴더를 읽어 네트워크 이상 이력을 보여주는 웹 대시보드.

사용법:
  python3 net_monitor.py                      # 이 스크립트 옆의 ros2_net_diag_* 중 가장 최근 실행
  python3 net_monitor.py ros2_net_diag_2026.. # 특정 실행 폴더부터 열기
  python3 net_monitor.py --port 8088 --host 0.0.0.0

표준 라이브러리만 사용합니다. 진행 중인 로그는 5초마다 자동 갱신됩니다.
"""
import argparse
import csv
import glob
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
TS_FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_CFG = {
    "ping_latency_ms": 100,
    "wifi_signal_dbm": -75,
    "mcast_in_pps": 200,
    "tcp_retrans_per_interval": 20,
    "node_drop": 1,
    "cpu_load1": 8.0,
    "mem_used_pct": 90,
    "merge_gap_s": 15,
    "stale_after_s": 30,
}


# ---------------------------------------------------------------- 파싱

def epoch(s):
    return time.mktime(time.strptime(s, TS_FMT))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None  # 'NA', 빈 값, 쓰다 만 줄


def read_csv(path):
    """timestamp 를 제외한 컬럼별로 [[t, value|None], ...] 반환"""
    cols = {}
    try:
        f = open(path, newline="")
    except OSError:
        return cols
    with f:
        for row in csv.DictReader(f):
            try:
                t = epoch(row["timestamp"])
            except (KeyError, TypeError, ValueError):
                continue  # 쓰는 도중 잘린 마지막 줄
            for k, v in row.items():
                if k and k != "timestamp":  # k=None: 필드가 넘치는 깨진 행의 잉여 값
                    cols.setdefault(k, []).append([t, num(v)])
    return cols


PING_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (?:OK ([\d.]+)ms|LOST)")


def read_ping(path):
    pts = []
    try:
        for line in open(path):
            m = PING_RE.match(line)
            if m:
                pts.append([epoch(m.group(1)), float(m.group(2)) if m.group(2) else None])
    except OSError:
        pass
    return pts


IFACE_RE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \|.*?"
    r"RX:\s+bytes\s+packets\s+errors\s+dropped\s+\S+\s+\S+\s+\d+\s+\d+\s+(\d+)\s+(\d+).*?"
    r"TX:\s+bytes\s+packets\s+errors\s+dropped\s+\S+\s+\S+\s+\d+\s+\d+\s+(\d+)\s+(\d+)")


def read_iface_errors(path):
    """RX/TX 의 errors+dropped 누적값 → [[t, 누적]]"""
    pts = []
    try:
        for line in open(path):
            m = IFACE_RE.match(line)
            if m:
                pts.append([epoch(m.group(1)), float(sum(int(g) for g in m.groups()[1:]))])
    except OSError:
        pass
    return pts


BW_RE = re.compile(r"([\d.]+)\s*(B|KB|MB)/s from")
BLOCK_RE = re.compile(r"^=== (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ===$", re.M)
UNIT_KB = {"B": 1 / 1024, "KB": 1.0, "MB": 1024.0}


def read_topic_bw(path):
    """감시 토픽 하나의 [[t, KB/s|None]]. None = 해당 구간에 측정값 없음(미발행)"""
    try:
        text = open(path, errors="replace").read()
    except OSError:
        return []
    marks = list(BLOCK_RE.finditer(text))
    pts = []
    for i, m in enumerate(marks):
        block = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        hits = BW_RE.findall(block)
        val = float(hits[-1][0]) * UNIT_KB[hits[-1][1]] if hits else None
        pts.append([epoch(m.group(1)), val])
    return pts


def delta_series(pts):
    """누적 카운터 → 구간별 증가량 (리셋은 0)"""
    return [[b[0], max(0.0, b[1] - a[1])] for a, b in zip(pts, pts[1:])
            if a[1] is not None and b[1] is not None]


def load_run(path):
    s = {}
    s["ping_ms"] = read_ping(os.path.join(path, "ping.log"))
    for fname in ("iface_bw.csv", "wifi_quality.csv", "node_count.csv", "cpu_load.csv", "proto_load.csv"):
        s.update(read_csv(os.path.join(path, fname)))
    s["iface_err_drop"] = delta_series(read_iface_errors(os.path.join(path, "iface.log")))
    for f in sorted(glob.glob(os.path.join(path, "topic_bw_*.log"))):
        name = "/" + os.path.basename(f)[len("topic_bw_"):-len(".log")]
        s["bw:" + name] = read_topic_bw(f)
    return {k: v for k, v in s.items() if v}


# ---------------------------------------------------------------- 이상 판정

def spans(pts, bad, kind, label, severity, cfg, threshold=None, unit="", worst=max):
    """bad(v) 가 참인 연속 샘플을 merge_gap_s 이내면 하나의 구간 이벤트로 병합"""
    events, cur = [], None
    for t, v in pts:
        if not bad(v):
            continue
        if cur and t - cur["end"] <= cfg["merge_gap_s"]:
            cur["end"] = t
            cur["count"] += 1
            if v is not None:
                cur["value"] = v if cur["value"] is None else worst(cur["value"], v)
        else:
            cur = {"start": t, "end": t, "kind": kind, "label": label, "severity": severity,
                   "value": v, "threshold": threshold, "unit": unit, "count": 1}
            events.append(cur)
    return events


def is_mcast_storm(value, cfg):
    """멀티캐스트 수신 pps 한 샘플이 discovery 폭주로 볼 만한지 판정한다.

    TODO(human): 지금은 net_monitor.json 의 절대 임계값(mcast_in_pps) 하나만 쓴다.
    로봇 대수·노드 수가 달라지면 정상 수준 자체가 변하므로, 실행 초기 구간의 평균 같은
    기준선 대비 배수로 판정하도록 바꿔 볼 수 있다. (value 한 샘플만 받으므로 필요하면
    시그니처를 확장해도 된다. 호출부는 detect_events 의 '멀티캐스트' 블록 하나뿐이다.)
    """
    return value is not None and value > cfg["mcast_in_pps"]


def detect_events(s, cfg):
    ev = []
    ev += spans(s.get("ping_ms", []), lambda v: v is None, "ping_lost", "핑 끊김", "critical", cfg)
    ev += spans(s.get("ping_ms", []), lambda v: v is not None and v > cfg["ping_latency_ms"],
                "ping_slow", "핑 지연", "warn", cfg, cfg["ping_latency_ms"], "ms")
    ev += spans(s.get("signal_dbm", []), lambda v: v is not None and v < cfg["wifi_signal_dbm"],
                "wifi_weak", "WiFi 신호 약함", "warn", cfg, cfg["wifi_signal_dbm"], "dBm", worst=min)

    nodes = s.get("node_count", [])
    drops = [[b[0], a[1] - b[1]] for a, b in zip(nodes, nodes[1:]) if a[1] is not None and b[1] is not None]
    ev += spans(drops, lambda v: v >= cfg["node_drop"], "node_drop", "노드 수 감소", "critical",
                cfg, cfg["node_drop"], "개")

    ev += spans(s.get("udp_rcvbuf_err", []), lambda v: v is not None and v > 0,
                "udp_rcvbuf", "UDP 수신버퍼 오버플로", "critical", cfg, 0, "건")
    ev += spans(s.get("tcp_retrans", []), lambda v: v is not None and v > cfg["tcp_retrans_per_interval"],
                "tcp_retrans", "TCP 재전송 급증", "warn", cfg, cfg["tcp_retrans_per_interval"], "건/5s")
    ev += spans(s.get("mcast_in_pps", []), lambda v: is_mcast_storm(v, cfg),
                "mcast_storm", "멀티캐스트 폭주 의심", "warn", cfg, cfg["mcast_in_pps"], "pps")
    ev += spans(s.get("iface_err_drop", []), lambda v: v > 0, "iface_drop", "인터페이스 에러/드랍 증가",
                "critical", cfg, 0, "건")

    ev += spans(s.get("cpu_load1", []), lambda v: v is not None and v > cfg["cpu_load1"],
                "cpu_high", "CPU 부하 높음", "warn", cfg, cfg["cpu_load1"])
    ev += spans(s.get("mem_used_pct", []), lambda v: v is not None and v > cfg["mem_used_pct"],
                "mem_high", "메모리 사용률 높음", "warn", cfg, cfg["mem_used_pct"], "%")

    for key, pts in s.items():
        if key.startswith("bw:"):
            ev += spans(pts, lambda v: v is None, "topic_silent", key[3:] + " 측정값 없음(미발행)",
                        "warn", cfg)
    ev.sort(key=lambda e: e["start"])
    return ev


def run_status(s, events, cfg):
    last = max((p[-1][0] for p in s.values()), default=0)
    live = time.time() - last < cfg["stale_after_s"]
    active = [e for e in events if e["end"] >= last - cfg["stale_after_s"]]
    recent = [e for e in events if e["end"] >= last - 300]
    if any(e["severity"] == "critical" for e in active):
        level = "critical"
    elif active or recent:
        level = "warn"
    else:
        level = "ok"
    return {"level": level, "live": live, "last": last}


# ---------------------------------------------------------------- 서버

class Store:
    def __init__(self, base, cfg):
        self.base, self.cfg, self.cache = base, cfg, {}

    def runs(self):
        out = []
        for d in sorted(glob.glob(os.path.join(self.base, "ros2_net_diag_*"))):
            if os.path.isdir(d):
                out.append({"name": os.path.basename(d), "mtime": os.path.getmtime(d)})
        return out

    def run(self, name):
        if name not in {r["name"] for r in self.runs()}:  # 경로 탈출 방지: 목록에 있는 폴더만
            return None
        path = os.path.join(self.base, name)
        sig = tuple(sorted((f, os.path.getmtime(f), os.path.getsize(f))
                           for f in glob.glob(os.path.join(path, "*"))))
        hit = self.cache.get(name)
        if hit and hit[0] == sig:
            return hit[1]
        series = load_run(path)
        events = detect_events(series, self.cfg)
        result = {"name": name, "series": series, "events": events,
                  "status": run_status(series, events, self.cfg), "cfg": self.cfg}
        self.cache[name] = (sig, result)
        return result


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/":
                self._send(200, PAGE, "text/html; charset=utf-8")
            elif u.path == "/api/runs":
                self._send(200, json.dumps(store.runs()), "application/json")
            elif u.path == "/api/run":
                data = store.run(parse_qs(u.query).get("name", [""])[0])
                if data is None:
                    self._send(404, '{"error":"unknown run"}', "application/json")
                else:
                    self._send(200, json.dumps(data), "application/json")
            else:
                self._send(404, "not found", "text/plain")

        def log_message(self, *a):
            pass

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", nargs="?", help="처음에 열 로그 폴더 (생략 시 가장 최근)")
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--host", default="127.0.0.1", help="다른 PC에서 보려면 0.0.0.0")
    ap.add_argument("--config", default=os.path.join(HERE, "net_monitor.json"))
    args = ap.parse_args()

    cfg = dict(DEFAULT_CFG)
    try:
        cfg.update(json.load(open(args.config)))
    except (OSError, ValueError) as e:
        print(f"[알림] 설정 파일을 읽지 못해 기본 임계값을 사용합니다: {e}")

    base = os.path.dirname(os.path.abspath(args.run)) if args.run else HERE
    store = Store(base, cfg)
    first = os.path.basename(os.path.abspath(args.run)) if args.run else \
        (store.runs()[-1]["name"] if store.runs() else "")
    print(f"로그 위치: {base}  ({len(store.runs())}개 실행)")
    print(f"대시보드  : http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/#{first}")
    ThreadingHTTPServer((args.host, args.port), make_handler(store)).serve_forever()


PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>네트워크 모니터</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--fg:#1c2330;--muted:#6b7686;--grid:#e3e7ee;--line:#d5dae3;
--ok:#1f9d55;--warn:#b7791f;--crit:#d64545;--warn-bg:rgba(240,170,40,.22);--crit-bg:rgba(220,70,70,.22)}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1b212b;--fg:#e4e9f0;--muted:#8d99aa;--grid:#2a3240;--line:#2f3846;
--ok:#4cc38a;--warn:#e0a948;--crit:#ff7070;--warn-bg:rgba(224,169,72,.25);--crit-bg:rgba(255,112,112,.25)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Noto Sans KR",sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:12px 16px;background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
h1{font-size:16px;margin:0 8px 0 0}
select,button{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px 10px;cursor:pointer}
#badge{padding:3px 12px;border-radius:12px;font-weight:600;border:1px solid currentColor}
.ok{color:var(--ok)}.warn{color:var(--warn)}.critical{color:var(--crit)}
#meta{color:var(--muted);font-size:12px}
main{display:grid;grid-template-columns:minmax(300px,420px) 1fr;gap:16px;padding:16px;max-width:1500px;margin:0 auto}
@media (max-width:900px){main{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
h2{font-size:13px;margin:0 0 8px;color:var(--muted);font-weight:600}
#events{max-height:calc(100vh - 120px);overflow:auto}
table{width:100%;border-collapse:collapse;font-size:12.5px}
td,th{padding:5px 6px;border-bottom:1px solid var(--grid);text-align:left;vertical-align:top}
tr.ev{cursor:pointer}tr.ev:hover,tr.sel{background:var(--grid)}
.tag{font-size:11px;font-weight:700;padding:1px 6px;border-radius:4px;border:1px solid currentColor;white-space:nowrap}
.empty{color:var(--muted);padding:16px 4px}
#charts{display:flex;flex-direction:column;gap:12px}
canvas{width:100%;height:170px;display:block}
.ctitle{font-size:13px;font-weight:600;margin:0 0 2px}
</style></head><body>
<header>
  <h1>ROS 2 네트워크 모니터</h1>
  <select id="run" aria-label="실행 선택"></select>
  <span id="badge" class="ok">-</span>
  <button id="all">전체 보기</button>
  <button id="last10">최근 10분</button>
  <span id="meta"></span>
</header>
<main>
  <section><h2>이상 이벤트 이력 (클릭하면 해당 시점으로 확대)</h2><div id="events"></div></section>
  <div id="charts"></div>
</main>
<script>
const CHARTS=[
 {id:'ping',title:'핑 지연 (ms) · 빨간 눈금 = 끊김',lost:'ping_ms',series:[['ping_ms','RTT']]},
 {id:'bw',title:'인터페이스 처리량 (KB/s)',series:[['rx_KBps','RX'],['tx_KBps','TX']]},
 {id:'wifi',title:'WiFi 신호 (dBm)',auto:true,series:[['signal_dbm','signal']]},
 {id:'pps',title:'프로토콜별 패킷률 (pps) · UDP / TCP / 멀티캐스트 / 유니캐스트',series:[['udp_in_pps','UDP 수신'],['udp_out_pps','UDP 송신'],['tcp_in_pps','TCP 수신'],['tcp_out_pps','TCP 송신'],['mcast_in_pps','멀티캐스트 수신'],['ucast_in_pps','유니캐스트 수신']]},
 {id:'kbps',title:'멀티캐스트 vs 유니캐스트 수신량 (KB/s)',series:[['mcast_in_KBps','멀티캐스트'],['ucast_in_KBps','유니캐스트']]},
 {id:'err',title:'오류 · 재전송 (5초당 건수)',series:[['udp_err','UDP 오류'],['udp_rcvbuf_err','UDP 수신버퍼'],['tcp_retrans','TCP 재전송'],['iface_err_drop','IF 에러/드랍']]},
 {id:'node',title:'노드 / 토픽 수',series:[['node_count','노드'],['topic_count','토픽']]},
 {id:'cpu',title:'CPU load (1분)',series:[['cpu_load1','load1']]},
 {id:'mem',title:'메모리 사용률 (%)',series:[['mem_used_pct','mem']]},
 {id:'topics',title:'감시 토픽 대역폭 (KB/s)',dynamic:'bw:'},
];
const PALETTE=['#4fa3ff','#ff9f43','#2ecc71','#e056fd','#f1c40f','#ff6b6b','#1abc9c','#a29bfe'];
const LABEL={ok:'정상',warn:'주의',critical:'이상'};
let D=null, view=null, marker=null, hoverT=null, selIdx=-1;
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt=t=>new Date(t*1000).toTimeString().slice(0,8);
const fmtD=t=>{const d=new Date(t*1000);return (d.getMonth()+1)+'/'+d.getDate()+' '+fmt(t)};
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

function range(){
  let a=Infinity,b=-Infinity;
  for(const p of Object.values(D.series)){if(p.length){a=Math.min(a,p[0][0]);b=Math.max(b,p[p.length-1][0]);}}
  return a<b?[a,b]:[0,1];
}
function seriesOf(c){
  if(c.dynamic) return Object.keys(D.series).filter(k=>k.startsWith(c.dynamic)).map(k=>[k,k.slice(c.dynamic.length)]);
  return c.series.filter(([k])=>D.series[k]);
}
function nearest(pts,t){
  let lo=0,hi=pts.length-1;
  while(lo<hi){const m=(lo+hi)>>1;if(pts[m][0]<t)lo=m+1;else hi=m;}
  if(lo>0&&Math.abs(pts[lo-1][0]-t)<Math.abs(pts[lo][0]-t))lo--;
  return pts[lo];
}

function drawChart(c,canvas,series){
  const dpr=window.devicePixelRatio||1, W=canvas.clientWidth, H=canvas.clientHeight;
  canvas.width=W*dpr;canvas.height=H*dpr;
  const g=canvas.getContext('2d');g.scale(dpr,dpr);
  const L=46,R=10,T=22,B=20,pw=W-L-R,ph=H-T-B,[v0,v1]=view;
  const x=t=>L+(t-v0)/(v1-v0)*pw;
  // y 범위: 보이는 구간의 값
  let lo=Infinity,hi=-Infinity;
  for(const [k] of series)for(const [t,v] of D.series[k])if(v!==null&&t>=v0&&t<=v1){lo=Math.min(lo,v);hi=Math.max(hi,v);}
  if(lo===Infinity){lo=0;hi=1;}
  if(!c.auto)lo=Math.min(lo,0);
  if(hi===lo)hi=lo+1;
  const pad=(hi-lo)*.08;hi+=pad;if(c.auto)lo-=pad;
  const y=v=>T+ph-(v-lo)/(hi-lo)*ph;
  g.font='11px system-ui';g.textBaseline='middle';
  // 이벤트 음영
  for(const e of D.events){
    if(e.end<v0||e.start>v1)continue;
    const a=Math.max(L,x(e.start)-2),b=Math.min(L+pw,x(e.end)+2);
    g.fillStyle=css(e.severity==='critical'?'--crit-bg':'--warn-bg');g.fillRect(a,T,Math.max(3,b-a),ph);
  }
  // 격자 + 축
  g.strokeStyle=css('--grid');g.fillStyle=css('--muted');g.lineWidth=1;g.textAlign='right';
  for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,yy=y(v);g.beginPath();g.moveTo(L,yy);g.lineTo(L+pw,yy);g.stroke();
    g.fillText(Math.abs(v)>=100?v.toFixed(0):v.toFixed(1),L-5,yy);}
  g.textAlign='center';
  for(let i=0;i<=5;i++){const t=v0+(v1-v0)*i/5;g.fillText(fmt(t),L+pw*i/5,H-8);}
  // 선
  series.forEach(([k],si)=>{
    g.strokeStyle=PALETTE[si%PALETTE.length];g.lineWidth=1.5;g.beginPath();let pen=false;
    for(const [t,v] of D.series[k]){
      if(t<v0||t>v1)continue;
      if(v===null){pen=false;continue;}
      pen?g.lineTo(x(t),y(v)):g.moveTo(x(t),y(v));pen=true;
    }
    g.stroke();
  });
  // 핑 끊김 눈금
  if(c.lost){g.strokeStyle=css('--crit');g.lineWidth=2;
    for(const [t,v] of D.series[c.lost]||[])if(v===null&&t>=v0&&t<=v1){g.beginPath();g.moveTo(x(t),T+ph-14);g.lineTo(x(t),T+ph);g.stroke();}}
  // 선택/호버 기준선
  for(const [t,col,dash] of [[marker,css('--crit'),[4,3]],[hoverT,css('--fg'),[]]]){
    if(t===null||t<v0||t>v1)continue;
    g.strokeStyle=col;g.lineWidth=1;g.setLineDash(dash);g.beginPath();g.moveTo(x(t),T);g.lineTo(x(t),T+ph);g.stroke();g.setLineDash([]);
  }
  // 범례 (호버 시 값 표시)
  g.textAlign='left';let lx=L;
  series.forEach(([k,label],si)=>{
    let txt=label;
    if(hoverT!==null&&D.series[k].length){const p=nearest(D.series[k],hoverT);txt+=' '+(p[1]===null?'-':(+p[1].toFixed(2)));}
    g.fillStyle=PALETTE[si%PALETTE.length];g.fillRect(lx,7,10,3);
    g.fillStyle=css('--fg');g.fillText(txt,lx+14,9);lx+=g.measureText(txt).width+30;
  });
  if(hoverT!==null){g.fillStyle=css('--muted');g.textAlign='right';g.fillText(fmt(hoverT),W-R,9);}
  canvas._x={L,pw,v0,v1};
}

const canvases={};
function buildCharts(){
  const box=document.getElementById('charts');box.innerHTML='';
  for(const c of CHARTS){
    const ser=seriesOf(c);
    if(!ser.length&&c.id!=='topics')continue;
    const s=document.createElement('section');
    s.innerHTML='<p class="ctitle">'+esc(c.title)+'</p>';
    const cv=document.createElement('canvas');s.appendChild(cv);box.appendChild(s);
    cv.addEventListener('mousemove',ev=>{const r=cv.getBoundingClientRect(),m=cv._x;
      if(!m)return;const px=ev.clientX-r.left;
      hoverT=px<m.L||px>m.L+m.pw?null:m.v0+(px-m.L)/m.pw*(m.v1-m.v0);redraw();});
    cv.addEventListener('mouseleave',()=>{hoverT=null;redraw();});
    canvases[c.id]=cv;
  }
}
function redraw(){
  for(const c of CHARTS){const cv=canvases[c.id];if(cv&&D)drawChart(c,cv,seriesOf(c));}
}

function renderEvents(){
  const el=document.getElementById('events');
  if(!D.events.length){el.innerHTML='<div class="empty">감지된 이상이 없습니다.</div>';return;}
  const rows=[...D.events].map((e,i)=>[e,i]).reverse().map(([e,i])=>{
    const when=fmtD(e.start)+(e.end>e.start?' ~ '+fmt(e.end):'');
    const val=e.value===null?'-':(+e.value.toFixed(1))+(e.unit?' '+e.unit:'');
    const thr=e.threshold===null?'':' (기준 '+e.threshold+')';
    return '<tr class="ev'+(i===selIdx?' sel':'')+'" data-i="'+i+'"><td>'+when+'</td><td><span class="tag '+e.severity+'">'+
      (e.severity==='critical'?'이상':'주의')+'</span> '+esc(e.label)+'</td><td>'+esc(val+thr)+'</td></tr>';
  }).join('');
  el.innerHTML='<table><tr><th>시각</th><th>종류</th><th>값</th></tr>'+rows+'</table>';
  el.querySelectorAll('tr.ev').forEach(tr=>tr.onclick=()=>{
    selIdx=+tr.dataset.i;const e=D.events[selIdx];marker=e.start;
    view=[e.start-120,Math.max(e.end,e.start)+120];renderEvents();redraw();});
}

async function load(follow){
  const name=document.getElementById('run').value;if(!name)return;
  const r=await fetch('/api/run?name='+encodeURIComponent(name));if(!r.ok)return;
  const fresh=await r.json();
  const rebuild=!D||D.name!==name||Object.keys(fresh.series).join()!==Object.keys(D.series).join();
  const full=!view||(D&&D.name!==name)||(follow&&view[1]>=range()[1]-1);
  D=fresh;
  if(D.name!==name||full)view=range();
  if(rebuild)buildCharts();
  const b=document.getElementById('badge');
  b.className=D.status.level;b.textContent=LABEL[D.status.level]+(D.status.live?' · 실시간':' · 종료됨');
  document.getElementById('meta').textContent='마지막 데이터 '+fmtD(D.status.last)+' · 이벤트 '+D.events.length+'건';
  renderEvents();redraw();
}

async function init(){
  const runs=await (await fetch('/api/runs')).json();
  const sel=document.getElementById('run');
  sel.innerHTML=runs.slice().reverse().map(r=>'<option>'+esc(r.name)+'</option>').join('');
  const want=decodeURIComponent(location.hash.slice(1));
  if(want&&runs.some(r=>r.name===want))sel.value=want;
  sel.onchange=()=>{location.hash=sel.value;D=null;view=null;marker=null;selIdx=-1;load(false);};
  document.getElementById('all').onclick=()=>{view=range();marker=null;selIdx=-1;renderEvents();redraw();};
  document.getElementById('last10').onclick=()=>{const r=range();view=[Math.max(r[0],r[1]-600),r[1]];redraw();};
  window.onresize=redraw;
  await load(false);
  setInterval(()=>load(true),5000);
}
init();
</script></body></html>
"""

if __name__ == "__main__":
    main()
