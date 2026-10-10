'use strict';
// 지도 화면. 좌표 변환은 mapmath.js 의 MapMath.createTransform 만 쓴다 (여기서 식을 다시 쓰지 않는다).

const PALETTE = ['#e8590c', '#1c7ed6', '#2f9e44', '#ae3ec9', '#f08c00', '#0b7285']; // 로봇별 고정 색 (robots 순서)
const OFFLINE_COLOR = '#8a8a8a';
const MARKER_R = 9;          // 마커 반지름 (화면 px, 확대해도 일정)
const MIN_ZOOM_RATIO = 0.5;  // 맞춤 배율 대비 최소 배율
const MAX_SCALE = 60;
const MAX_ALARMS = 4;

const el = (id) => document.getElementById(id);
const canvas = el('mapCanvas');
const ctx = canvas.getContext('2d');

const app = {
  meta: null, tf: null, img: null, gray: null,   // 지도
  view: { scale: 1, ox: 0, oy: 0, fitScale: 1 },  // 화면 = 이미지px * scale + (ox, oy)
  robots: {},   // id -> {color, online, patrol, pose, battery, disp:{x,y,yaw}|null}
  order: [],
  zone: null,         // 구역 상태 원문 (free | occupied_by:<id>[:<token>])
  zoneInfo: null,     // 서버가 해석한 구역 상태 {state, holder, token, held_sec}
  zoneRecv: 0,        // zoneInfo 를 받은 시각(ms). 점유 시간을 화면에서 이어서 세는 데 쓴다
  zoneCfg: null,      // /api/zone/config (구역 사각형, 문, confirmed)
  showRoutes: false,  // 로봇 경로 선(Step 4 FR4-4 오버레이). 우선 꺼 두고, 지도의 '경로' 버튼으로 켠다
  plans: null,        // /api/plans: 'A>B' -> {path:[[x,y],...]}  지점 사이의 벽을 피하는 경로 추정 (Nav2 의 실제 경로가 아니다)
  plansWarned: false,
  motion: null,       // /api/motion/config (비상정지 burst, 수동 조작 상한, LiDAR 설정)
  scan: {},           // 로봇 id -> {on, points, t}: LiDAR 오버레이 (켠 로봇만 서버가 구독한다)
  wsUp: false,
  clicks: [],   // {x, y}
  hover: null,
  alarms: [],
  dirtyCards: true,
  fitted: false,
  showGrid: true,
  cfg: null,          // /api/commands/config (로봇별 허용 지점, 홈, 대기, 지점 좌표, 임계 시간)
  routes: {},         // 로봇 id -> 경로 이름 목록 (goto 로 보낼 지점들)
  routeTarget: null,  // 지도에서 포인트를 클릭하면 경로가 추가되는 로봇
  history: [],        // 명령 이력, 최신이 앞 (서버가 보낸 command 이벤트로 갱신)
  locks: {},          // 'id:cmd' -> 연타 방지 해제 시각(ms)
  me: null,           // /api/me: {auth, role, procs_allowed, procs_configured}. role 이 operator 일 때만 명령·수동 조작·프로세스 제어 버튼이 켜진다
  procs: null,        // 서버가 보낸 프로세스 상태 {enabled, procs:[...], sequence}
  procLogOpen: null,  // 로그를 펼친 프로세스 id
};
const POINT_HIT_R = 14;      // 포인트 클릭 판정 반경 (화면 px)
const BTN_LOCK_MS = 1000;    // FR3-7: 같은 버튼 연타 방지

// ---------- 유틸 ----------
const hhmmss = () => new Date().toTimeString().slice(0, 8);
const isOperator = () => !!app.me && app.me.role === 'operator';
function normAngle(a) { while (a > Math.PI) a -= 2 * Math.PI; while (a < -Math.PI) a += 2 * Math.PI; return a; }

function addAlarm(level, text) {
  app.alarms.unshift({ level, text, t: hhmmss() });
  app.alarms.length = Math.min(app.alarms.length, MAX_ALARMS);
  const ul = el('alarmList');
  ul.replaceChildren();
  for (const a of app.alarms) {
    const li = document.createElement('li');
    li.className = a.level;
    const t = document.createElement('time'); t.textContent = a.t;
    li.append(t, document.createTextNode(a.text));
    ul.appendChild(li);
  }
}

function setChip(id, level, text) {
  const c = el(id); c.dataset.level = level; c.textContent = text;
}

// ---------- 지도 ----------
async function loadMap() {
  const res = await fetch('/api/map');
  if (!res.ok) throw new Error('지도 메타데이터를 받지 못했습니다 (' + res.status + ')');
  app.meta = await res.json();
  app.tf = MapMath.createTransform(app.meta);
  app.img = await new Promise((ok, fail) => {
    const im = new Image();
    im.onload = () => ok(im); im.onerror = () => fail(new Error('지도 이미지를 불러오지 못했습니다'));
    im.src = app.meta.image_url;
  });
  // occupancy 판정용 그레이 값 (PNG 는 8비트 그레이 무손실이라 R 채널이 원래 pgm 값이다)
  const off = document.createElement('canvas');
  off.width = app.meta.width; off.height = app.meta.height;
  const octx = off.getContext('2d');
  octx.drawImage(app.img, 0, 0);
  const rgba = octx.getImageData(0, 0, off.width, off.height).data;
  app.gray = new Uint8Array(off.width * off.height);
  for (let i = 0; i < app.gray.length; i++) app.gray[i] = rgba[i * 4];
  el('stageNote').textContent = '';
  fitView();
}

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const r = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(r.width * dpr));
  canvas.height = Math.max(1, Math.round(r.height * dpr));
  if (app.meta) {
    const wasFit = app.fitted; // 사용자가 이동/확대하지 않았을 때만 창 크기에 맞춰 다시 맞춘다
    computeFit();
    if (wasFit) fitView();
  }
}

function cssSize() { const r = canvas.getBoundingClientRect(); return { w: r.width, h: r.height }; }

function computeFit() {
  const { w, h } = cssSize();
  const pad = 12;
  app.view.fitScale = Math.max(0.1, Math.min((w - pad * 2) / app.meta.width, (h - pad * 2) / app.meta.height));
}

function fitView() {
  if (!app.meta) return;
  computeFit();
  const { w, h } = cssSize();
  const v = app.view;
  v.scale = v.fitScale;
  v.ox = (w - app.meta.width * v.scale) / 2;
  v.oy = (h - app.meta.height * v.scale) / 2;
  app.fitted = true;
}

const toScreen = (px, py) => ({ sx: px * app.view.scale + app.view.ox, sy: py * app.view.scale + app.view.oy });
const toPixel = (sx, sy) => ({ px: (sx - app.view.ox) / app.view.scale, py: (sy - app.view.oy) / app.view.scale });
function worldToScreen(x, y) { const p = app.tf.worldToPixel(x, y); return toScreen(p.px, p.py); }

// ---------- 그리기 ----------
const GRID_STEPS = [0.05, 0.1, 0.2, 0.5, 1, 2]; // m. 화면 간격이 너무 좁지 않은 가장 작은 값을 고른다
const GRID_MIN_PX = 60;

// 월드 좌표(m) 격자와 x, y 눈금 라벨. 위치는 모두 app.tf(MapMath)를 거친다.
function drawGrid() {
  const m = app.meta, v = app.view;
  const pxPerM = v.scale / m.resolution;
  const step = GRID_STEPS.find((st) => st * pxPerM >= GRID_MIN_PX) || GRID_STEPS[GRID_STEPS.length - 1];
  const xMin = m.origin[0], yMin = m.origin[1];
  const xMax = xMin + m.width * m.resolution, yMax = yMin + m.height * m.resolution;
  const { w, h } = cssSize();
  // 지도가 화면에 보이는 영역 (라벨을 이 안쪽 가장자리에 붙인다)
  const left = Math.max(0, v.ox), top = Math.max(0, v.oy);
  const right = Math.min(w, v.ox + m.width * v.scale), bottom = Math.min(h, v.oy + m.height * v.scale);
  const decimals = step < 0.1 ? 2 : step < 1 ? 1 : 0;

  ctx.save();
  ctx.font = '11px ' + getComputedStyle(document.body).fontFamily;
  ctx.lineWidth = 1;
  for (let i = Math.ceil(xMin / step); i * step <= xMax; i++) {
    const x = i * step, { sx } = worldToScreen(x, yMin);
    if (sx < left - 1 || sx > right + 1) continue;
    ctx.strokeStyle = i === 0 ? 'rgba(31,200,140,.9)' : 'rgba(31,143,230,.5)';
    ctx.beginPath(); ctx.moveTo(Math.round(sx) + .5, top); ctx.lineTo(Math.round(sx) + .5, bottom); ctx.stroke();
    drawLabel(x.toFixed(decimals), sx + 3, top + 11, 'left');
  }
  for (let j = Math.ceil(yMin / step); j * step <= yMax; j++) {
    const y = j * step, { sy } = worldToScreen(xMin, y);
    if (sy < top - 1 || sy > bottom + 1) continue;
    ctx.strokeStyle = j === 0 ? 'rgba(31,200,140,.9)' : 'rgba(31,143,230,.5)';
    ctx.beginPath(); ctx.moveTo(left, Math.round(sy) + .5); ctx.lineTo(right, Math.round(sy) + .5); ctx.stroke();
    drawLabel(y.toFixed(decimals), left + 3, sy - 3, 'left');
  }
  drawLabel('x [m] →', right - 4, top + 24, 'right');
  drawLabel('y [m] ↑', left + 3, top + 24, 'left');
  ctx.restore();
}

function drawLabel(text, x, y, align) {
  ctx.save(); // lineWidth 등이 격자선 그리기에 번지지 않게 상태를 되돌린다
  ctx.textAlign = align;
  ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(255,255,255,.85)';
  ctx.strokeText(text, x, y);
  ctx.fillStyle = '#0b4f8a';
  ctx.fillText(text, x, y);
  ctx.restore();
}

function drawMarker(id, r) {
  const d = r.disp;
  if (!d) return;
  const { sx, sy } = worldToScreen(d.x, d.y);
  const color = r.online ? r.color : OFFLINE_COLOR;
  const ang = -d.yaw; // 월드 yaw 는 반시계(+x 기준), 화면 y 는 아래로 증가하므로 부호 반전
  ctx.save();
  ctx.globalAlpha = app.wsUp ? 1 : 0.35;
  ctx.translate(sx, sy);
  ctx.rotate(ang);
  ctx.fillStyle = color;
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 1.5;
  ctx.beginPath(); // 방향 화살표: 앞쪽이 +x
  ctx.moveTo(MARKER_R * 1.7, 0);
  ctx.lineTo(-MARKER_R * 0.9, MARKER_R * 0.95);
  ctx.lineTo(-MARKER_R * 0.35, 0);
  ctx.lineTo(-MARKER_R * 0.9, -MARKER_R * 0.95);
  ctx.closePath();
  ctx.fill(); ctx.stroke();
  ctx.restore();
  const waiting = r.patrol && r.patrol.state === 'WAITING_ZONE';
  if (waiting) {  // FR4-3: 구역 진입 대기
    ctx.save();
    ctx.strokeStyle = '#e03131'; ctx.lineWidth = 3; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.arc(sx, sy, MARKER_R + 6, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
  }
  ctx.save();
  ctx.globalAlpha = app.wsUp ? 1 : 0.35;
  ctx.font = 'bold 13px ' + getComputedStyle(document.body).fontFamily;
  ctx.textAlign = 'center';
  ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,0,.55)';
  ctx.strokeText(id, sx, sy - MARKER_R - 7);
  ctx.fillStyle = '#fff';
  ctx.fillText(id, sx, sy - MARKER_R - 7);
  if (waiting) {
    const t = ZoneView.waitingText(robotCfg(id), r.lastTask, r.patrol);
    ctx.strokeText(t, sx, sy + MARKER_R + 20);
    ctx.fillStyle = '#ff8787';
    ctx.fillText(t, sx, sy + MARKER_R + 20);
  }
  ctx.restore();
}

function draw() {
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const { w, h } = cssSize();
  ctx.clearRect(0, 0, w, h);
  if (!app.img) return;
  const v = app.view;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(app.img, v.ox, v.oy, app.meta.width * v.scale, app.meta.height * v.scale);
  ctx.strokeStyle = 'rgba(128,128,128,.6)'; ctx.lineWidth = 1;
  ctx.strokeRect(v.ox + .5, v.oy + .5, app.meta.width * v.scale, app.meta.height * v.scale);

  if (app.showGrid) drawGrid();
  app.clicks.forEach((c, i) => {
    const { sx, sy } = worldToScreen(c.x, c.y);
    ctx.fillStyle = '#f2a65a'; ctx.strokeStyle = '#222'; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(sx, sy, 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.font = 'bold 12px monospace'; ctx.fillStyle = '#f2a65a';
    ctx.fillText('C' + (i + 1), sx + 6, sy - 6);
  });
  drawZone();
  drawScan();
  if (app.showRoutes) drawRoutes();
  drawPoints();
  for (const id of app.order) drawMarker(id, app.robots[id]);
}

// FR4-1, FR4-2: 위험 구역 사각형과 진입/이탈 문. 점유 중이면 빨갛게 칠한다.
function drawZone() {
  const z = app.zoneCfg;
  if (!z) return;
  const occupied = app.zoneInfo && app.zoneInfo.state === 'occupied';
  const a = worldToScreen(z.rect.x_min, z.rect.y_max), b = worldToScreen(z.rect.x_max, z.rect.y_min);
  ctx.save();
  ctx.fillStyle = occupied ? 'rgba(224,49,49,.28)' : 'rgba(240,140,0,.10)';
  ctx.strokeStyle = occupied ? '#e03131' : '#f08c00';
  ctx.lineWidth = 2; ctx.setLineDash(z.confirmed ? [] : [6, 4]);  // 미확인 초안은 점선
  ctx.fillRect(a.sx, a.sy, b.sx - a.sx, b.sy - a.sy);
  ctx.strokeRect(a.sx, a.sy, b.sx - a.sx, b.sy - a.sy);
  ctx.setLineDash([]);
  ctx.font = 'bold 12px ' + getComputedStyle(document.body).fontFamily;
  const label = '위험 구역' + (z.confirmed ? '' : ' (초안: 미확인)') + (occupied ? ' · ' + app.zoneInfo.holder + ' 점유' : '');
  drawLabel(label, a.sx + 4, a.sy + 13, 'left');
  if (!z.confirmed) {  // 초안일 때는 사용자가 지도와 대조할 수 있게 모서리 좌표를 적는다
    const r = z.rect, f = (v) => v.toFixed(2);
    drawLabel('(' + f(r.x_min) + ', ' + f(r.y_max) + ')', a.sx + 4, a.sy + 27, 'left');
    drawLabel('(' + f(r.x_max) + ', ' + f(r.y_min) + ')', b.sx - 4, b.sy - 6, 'right');
  }
  // 문: 로봇 색 마름모 (IN 과 OUT 은 같은 위치라 한 번만 그린다)
  app.order.forEach((id, i) => {
    const d = z.doors[id], pt = d && app.cfg && app.cfg.points[d.in];
    if (!pt) return;
    const { sx, sy } = worldToScreen(pt.x, pt.y), r = 6;
    ctx.fillStyle = app.robots[id].color; ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(sx, sy - r); ctx.lineTo(sx + r, sy); ctx.lineTo(sx, sy + r); ctx.lineTo(sx - r, sy); ctx.closePath();
    ctx.fill(); ctx.stroke();
    drawLabel(d.in.replace(/IN$/, '') + ' 문', sx - 9, sy + 4 + i * 12, 'right');
  });
  ctx.restore();
}

// FR4-5, FR4-6: LiDAR 점. 서버가 amcl_pose 기준 지도 좌표로 바꿔서 보낸다. 오래된 스캔(로봇이 끊김 등)은 그리지 않는다.
const SCAN_STALE_MS = 3000;
function drawScan() {
  const now = Date.now();
  ctx.save();
  for (const id of app.order) {
    const s = app.scan[id];
    if (!s || !s.on || !s.points.length || now - s.t > SCAN_STALE_MS) continue;
    ctx.fillStyle = app.robots[id].color; ctx.globalAlpha = 0.85;
    for (const [x, y] of s.points) {
      const { sx, sy } = worldToScreen(x, y);
      ctx.fillRect(sx - 1.5, sy - 1.5, 3, 3);
    }
  }
  ctx.restore();
}

// 선분 하나를 그릴 꺾은선. 서버가 계산한 벽을 피하는 경로(/api/plans)를 쓰고, 없으면(아직 못 받았거나 경로 추정 실패) 직선이다.
function polylineFor(sg) {
  const hit = app.plans && app.plans[sg.from + '>' + sg.to];
  return hit ? hit.path : [[sg.a.x, sg.a.y], [sg.b.x, sg.b.y]];
}

// FR4-4: 로봇별 경로 선. 지난 구간은 연하게, 현재 목표로 가는 구간은 굵게, 남은 구간은 점선. 현재 목표에는 고리.
function drawRoutes() {
  if (!app.cfg || !app.zoneCfg) return;
  for (const id of app.order) {
    const r = app.robots[id];
    const ov = ZoneView.routeOverlay(robotCfg(id), app.zoneCfg.doors[id], app.cfg.points, r.lastTask, r.patrol);
    if (!ov) continue;
    ctx.save();
    ctx.strokeStyle = r.online ? r.color : OFFLINE_COLOR; ctx.lineWidth = 3; ctx.lineCap = 'round';
    for (const sg of ZoneView.segments(ov, app.cfg.points)) {
      ctx.globalAlpha = sg.state === 'done' ? 0.25 : sg.state === 'active' ? 1 : 0.7;
      ctx.lineWidth = sg.state === 'active' ? 5 : 3;
      ctx.setLineDash(sg.state === 'todo' ? [7, 6] : []);
      ctx.beginPath();
      polylineFor(sg).forEach(([x, y], i) => { const s = worldToScreen(x, y); if (i) ctx.lineTo(s.sx, s.sy); else ctx.moveTo(s.sx, s.sy); });
      ctx.stroke();
    }
    ctx.restore();
    const t = ov.target >= 0 ? app.cfg.points[ov.names[ov.target]] : null;
    if (t) {
      const { sx, sy } = worldToScreen(t.x, t.y);
      ctx.save();
      ctx.strokeStyle = r.color; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(sx, sy, 11, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
    }
  }
}

// goto 가능한 포인트 (구역 문 RED* 는 노드가 알아서 지나가므로 그리지 않는다). 구역 안은 사각형 + 주황.
function gotoPoints() {
  if (!app.cfg) return [];
  const names = new Set();
  for (const r of Object.values(app.cfg.robots)) r.goto_allowed.forEach((n) => names.add(n));
  return [...names].filter((n) => app.cfg.points[n] && !app.cfg.points[n].door).map((n) => ({ name: n, ...app.cfg.points[n] }));
}

function drawPoints() {
  const route = app.routeTarget ? app.routes[app.routeTarget] || [] : [];
  ctx.save();
  ctx.font = 'bold 12px ' + getComputedStyle(document.body).fontFamily;
  for (const p of gotoPoints()) {
    const { sx, sy } = worldToScreen(p.x, p.y);
    const color = p.in_zone ? '#e8590c' : '#1971c2';
    ctx.fillStyle = 'rgba(255,255,255,.9)'; ctx.strokeStyle = color; ctx.lineWidth = 2;
    ctx.beginPath();
    if (p.in_zone) ctx.rect(sx - 6, sy - 6, 12, 12); else ctx.arc(sx, sy, 6, 0, Math.PI * 2);
    ctx.fill(); ctx.stroke();
    const label = p.name + (p.in_zone ? ' (구역 안)' : '');
    const order = route.reduce((acc, n, i) => (n === p.name ? acc.concat(i + 1) : acc), []);
    const text = label + (order.length ? '  [' + order.join(',') + ']' : '');
    const flip = sx + 9 + ctx.measureText(text).width > cssSize().w - 4; // 화면 오른쪽 끝이면 라벨을 왼쪽에
    ctx.textAlign = flip ? 'right' : 'left';
    const tx = flip ? sx - 9 : sx + 9;
    ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(255,255,255,.9)';
    ctx.strokeText(text, tx, sy + 4);
    ctx.fillStyle = order.length ? '#d9480f' : color;
    ctx.fillText(text, tx, sy + 4);
  }
  ctx.restore();
}

function pointAt(sx, sy) {
  let best = null, bd = POINT_HIT_R;
  for (const p of gotoPoints()) {
    const q = worldToScreen(p.x, p.y);
    const d = Math.hypot(q.sx - sx, q.sy - sy);
    if (d <= bd) { bd = d; best = p; }
  }
  return best;
}

let lastT = performance.now();
function frame(now) {
  const dt = Math.min(0.2, (now - lastT) / 1000); lastT = now;
  const k = 1 - Math.exp(-dt * 14); // pose 는 10Hz 로 오므로 프레임마다 목표값으로 부드럽게 접근
  for (const id of app.order) {
    const r = app.robots[id];
    if (!r.pose) { r.disp = null; continue; }
    if (!r.disp) { r.disp = { ...r.pose }; continue; }
    r.disp.x += (r.pose.x - r.disp.x) * k;
    r.disp.y += (r.pose.y - r.disp.y) * k;
    r.disp.yaw = normAngle(r.disp.yaw + normAngle(r.pose.yaw - r.disp.yaw) * k);
  }
  if (app.dirtyCards) { renderCards(); app.dirtyCards = false; }
  draw();
  requestAnimationFrame(frame);
}

// ---------- 로봇 카드 ----------
const cardEls = {};
function ensureCard(id) {
  if (cardEls[id]) return cardEls[id];
  const root = document.createElement('div'); root.className = 'card';
  root.style.borderLeftColor = app.robots[id].color;
  root.addEventListener('click', () => selectRouteTarget(id));
  const head = document.createElement('div'); head.className = 'card-head';
  const name = document.createElement('span'); name.className = 'card-name'; name.textContent = id;
  const badges = document.createElement('span');
  const badge = document.createElement('span'); badge.className = 'badge';
  const stateBadge = document.createElement('span'); stateBadge.className = 'state-badge';
  badges.append(badge, stateBadge);
  head.append(name, badges);
  const dl = document.createElement('dl');
  const f = {};
  for (const [key, label] of [['prog', '진행'], ['batt', '배터리'], ['pos', '좌표']]) {
    const dt = document.createElement('dt'); dt.textContent = label;
    const dd = document.createElement('dd'); f[key] = dd;
    dl.append(dt, dd);
  }
  const meta = document.createElement('p'); meta.className = 'meta';
  const notice = document.createElement('p'); notice.className = 'notice';
  const noResp = document.createElement('p'); noResp.className = 'notice';
  const row = document.createElement('div'); row.className = 'cmd-row';
  const btns = {};
  for (const [cmd, label] of [['start', '순찰 시작'], ['stop', '순찰 정지'], ['goto', '경로 이동']]) {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = label;
    b.addEventListener('click', (e) => { e.stopPropagation(); onCommandClick(id, cmd); });
    btns[cmd] = b; row.appendChild(b);
  }
  // 비상 정지: 권한, online 여부, 연타 잠금과 상관없이 항상 누를 수 있다 (FR4-7). 카드 클릭(경로 대상 선택)은 막는다
  const estopRow = document.createElement('div'); estopRow.className = 'cmd-row estop-row';
  const estopBtn = document.createElement('button'); estopBtn.type = 'button'; estopBtn.className = 'danger';
  estopBtn.textContent = '비상 정지'; estopBtn.dataset.robot = id;
  estopBtn.title = id + ' 에 stop 과 cmd_vel 0 속도를 보냅니다';
  estopBtn.addEventListener('click', (e) => { e.stopPropagation(); estop(id); });
  estopRow.appendChild(estopBtn);
  root.append(head, dl, meta, notice, noResp, row, estopRow);
  el('robotCards').appendChild(root);
  return (cardEls[id] = { root, badge, stateBadge, meta, notice, noResp, btns, ...f });
}

function robotCfg(id) { return app.cfg && app.cfg.robots[id]; }

function locked(id, cmd) { return (app.locks[id + ':' + cmd] || 0) > Date.now(); }

function renderCards() {
  const now = Date.now();
  for (const id of app.order) {
    const r = app.robots[id], c = ensureCard(id), cfg = robotCfg(id);
    c.badge.textContent = r.online ? 'online' : 'offline';
    c.badge.classList.toggle('on', r.online);
    c.root.classList.toggle('target', app.routeTarget === id);
    const p = r.patrol, state = p && p.state;
    c.stateBadge.textContent = state || '-';
    c.stateBadge.dataset.level = PatrolView.badgeLevel(state);
    if (state === 'WAITING_ZONE') {  // FR4-3
      const zi = app.zoneInfo, holder = zi && zi.state === 'occupied' && zi.holder !== id ? ' · ' + zi.holder + ' 사용 중' : '';
      c.prog.textContent = ZoneView.waitingText(cfg, r.lastTask, p) + holder;
    } else {
      c.prog.textContent = PatrolView.progressText(cfg, r.lastTask, p);
    }
    const b = r.battery;
    c.batt.textContent = b && b.percentage != null ? b.percentage.toFixed(1) + ' %' + (b.voltage != null ? ' (' + b.voltage.toFixed(2) + ' V)' : '') : '-';
    c.pos.textContent = r.pose ? '(' + r.pose.x.toFixed(2) + ', ' + r.pose.y.toFixed(2) + ') m' : '-';
    c.meta.textContent = cfg ? '완료 후 복귀: ' + cfg.home + ' · ' + (cfg.wait_every_point ? '지점마다 ' : '마지막 지점에서 ') + cfg.goto_wait_sec + '초 대기' : '';
    const starting = app.cfg && PatrolView.startingNotice(state, r.stateSince, now, app.cfg.starting_notice_sec);
    c.notice.style.display = starting ? 'block' : 'none';
    c.notice.textContent = starting ? '시작 준비 중 (첫 작업은 초기 위치 보정으로 로봇이 제자리에서 회전합니다)' : '';
    const lc = r.lastCommand;
    const noResp = lc && lc.result === 'no_response' && r.noRespDismissed !== lc.id;
    c.noResp.style.display = noResp ? 'block' : 'none';
    c.noResp.textContent = noResp ? '로봇이 응답하지 않았거나 무시했을 수 있음 (' + (lc.sent || lc.cmd) + ')' : '';
    const ready = !!app.cfg && app.wsUp && isOperator();   // 보기 전용(viewer)은 명령 버튼이 꺼진다
    const route = app.routes[id] || [];
    c.btns.start.disabled = !ready || !PatrolView.canStart(state, r.online) || locked(id, 'start');
    c.btns.stop.disabled = !ready || !PatrolView.canStop(state, r.online) || locked(id, 'stop');
    c.btns.goto.disabled = !ready || !PatrolView.canStart(state, r.online) || !route.length || locked(id, 'goto');
    c.btns.goto.textContent = '경로 이동' + (route.length ? ' (' + route.length + ')' : '');
  }
  renderDrivePanel();
  renderScanInfo();
}

// ---------- 명령 ----------
function confirmDialog(text) {
  const dlg = el('confirmDlg');
  el('confirmText').textContent = text;
  return new Promise((resolve) => {
    dlg.addEventListener('close', () => resolve(dlg.returnValue === 'ok'), { once: true });
    dlg.returnValue = 'cancel';
    dlg.showModal();
  });
}

function lockButton(id, cmd) {
  app.locks[id + ':' + cmd] = Date.now() + BTN_LOCK_MS;
  app.dirtyCards = true;
  setTimeout(() => { app.dirtyCards = true; }, BTN_LOCK_MS + 30);
}

async function onCommandClick(id, cmd) {
  const cfg = robotCfg(id);
  if (!cfg || locked(id, cmd)) return;
  let points;
  if (cmd === 'goto') {
    points = (app.routes[id] || []).slice();
    if (!points.length) return;
    if (!(await confirmDialog(PatrolView.routeSummary(id, cfg, points)))) return;
  } else if (cmd === 'start') {
    if (!(await confirmDialog(PatrolView.startSummary(id, cfg)))) return;
  }
  lockButton(id, cmd); // stop 은 확인 없이 즉시 보내므로 눌렀을 때 바로 잠근다
  await sendCommand(id, cmd, points);
}

async function sendCommand(id, cmd, points) {
  const body = { cmd };
  if (points) body.points = points;
  try {
    const res = await fetch('/api/robots/' + encodeURIComponent(id) + '/command', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) showLogin('세션이 만료됐습니다. 다시 로그인하세요');
    else if (!res.ok) addAlarm('bad', id + ' ' + cmd + ' 실패: ' + (data.error || res.status));
  } catch (err) {
    addAlarm('bad', id + ' ' + cmd + ' 전송 오류: ' + err.message);
  }
}

// ---------- 경로 목록 ----------
function selectRouteTarget(id) {
  app.routeTarget = id;
  el('routeRobot').value = id;
  renderRoute();
  app.dirtyCards = true;
}

function addRoutePoint(name) {
  const id = app.routeTarget, cfg = robotCfg(id);
  if (!cfg) return;
  if (!cfg.goto_allowed.includes(name)) { addAlarm('warn', id + ' 는 ' + name + ' 로 이동할 수 없습니다'); return; }
  const route = app.routes[id] || (app.routes[id] = []);
  if (route.length >= app.cfg.max_goto_points) { addAlarm('warn', '경로는 최대 ' + app.cfg.max_goto_points + '개 지점까지입니다'); return; }
  route.push(name);
  renderRoute();
  app.dirtyCards = true;
}

function renderRoute() {
  const id = app.routeTarget, ul = el('routeList');
  ul.replaceChildren();
  const route = (id && app.routes[id]) || [];
  el('routeEmpty').style.display = route.length ? 'none' : 'block';
  route.forEach((name, i) => {
    const li = document.createElement('li');
    li.append(document.createTextNode(name));
    const pt = app.cfg && app.cfg.points[name];
    if (pt && pt.in_zone) { const z = document.createElement('span'); z.className = 'zone-tag'; z.textContent = '구역 안'; li.appendChild(z); }
    const mk = (label, fn, dis) => {
      const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.disabled = !!dis;
      b.onclick = fn; return b;
    };
    li.append(' ', mk('▲', () => { route.splice(i - 1, 0, route.splice(i, 1)[0]); renderRoute(); app.dirtyCards = true; }, i === 0),
      mk('▼', () => { route.splice(i + 1, 0, route.splice(i, 1)[0]); renderRoute(); app.dirtyCards = true; }, i === route.length - 1),
      mk('삭제', () => { route.splice(i, 1); renderRoute(); app.dirtyCards = true; }));
    ul.appendChild(li);
  });
  const cfg = id && robotCfg(id);
  el('routeMeta').textContent = cfg ? '완료 후 ' + cfg.home + ' 복귀 · 최대 ' + app.cfg.max_goto_points + '개 · ' + route.length + '개 선택' : '';
  el('routeClearBtn').disabled = !route.length;
}

function setupRoutePanel() {
  const sel = el('routeRobot');
  sel.replaceChildren();
  for (const id of app.order) {
    const o = document.createElement('option'); o.value = id; o.textContent = id; sel.appendChild(o);
  }
  if (!app.routeTarget || !app.order.includes(app.routeTarget)) app.routeTarget = app.order[0] || null;
  sel.value = app.routeTarget || '';
  renderRoute();
}
el('routeRobot').onchange = (e) => selectRouteTarget(e.target.value);
el('routeClearBtn').onclick = () => { if (app.routeTarget) { app.routes[app.routeTarget] = []; renderRoute(); app.dirtyCards = true; } };

// ---------- 명령 이력 ----------
const hhmmssOf = (t) => new Date(t * 1000).toTimeString().slice(0, 8);

function renderHistory() {
  const body = el('historyBody');
  body.replaceChildren();
  el('historyEmpty').style.display = app.history.length ? 'none' : 'block';
  for (const e of app.history) {
    const tr = document.createElement('tr');
    const cmdText = e.cmd === 'goto' ? 'goto ' + e.points.join(', ') : e.cmd === 'estop' ? '비상정지 (' + e.sent + ')' : e.cmd;
    const res = PatrolView.resultLabel(e.result) + (e.detail ? ' · ' + e.detail : '');
    for (const [text, cls] of [[hhmmssOf(e.time)], [e.robot], [cmdText, 'cmd'], [res, 'res-' + e.result]]) {
      const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

function upsertHistory(entry) {
  const i = app.history.findIndex((e) => e.id === entry.id);
  if (i >= 0) app.history[i] = entry; else app.history.unshift(entry);
  app.history.sort((a, b) => b.id - a.id);
  const max = app.cfg ? app.cfg.history_size : 50;
  if (app.history.length > max) app.history.length = max;
  renderHistory();
}

// ---------- WebSocket ----------
function robotModel(id, i, data) {
  return { color: PALETTE[i % PALETTE.length], online: !!data.online, patrol: data.patrol, pose: data.pose,
           battery: data.battery, disp: null, lastCommand: data.last_command || null, lastTask: data.last_task || null, noRespDismissed: null,
           stateSince: data.patrol ? Date.now() : null };
}

function applySnapshot(msg) {
  const ids = Object.keys(msg.robots);
  app.order = ids;
  ids.forEach((id, i) => {
    const prev = app.robots[id];
    const m = robotModel(id, i, msg.robots[id]);
    if (prev) { m.disp = prev.disp; if (prev.patrol && m.patrol && prev.patrol.state === m.patrol.state) m.stateSince = prev.stateSince; }
    app.robots[id] = m;
  });
  for (const id of Object.keys(app.robots)) if (!ids.includes(id)) delete app.robots[id];
  setZone(msg.zone, msg.zone_info);
  if (msg.procs) setProcs(msg.procs);
  app.history = (msg.commands || []).slice().reverse();
  renderHistory();
  setupRoutePanel();
  setupScanToggles(); setupDrive();
  app.dirtyCards = true;
}

// info: 서버가 해석한 {state, holder, token, held_sec}. 수신 전이면 null.
function setZone(status, info) {
  app.zone = status;
  app.zoneInfo = status == null ? null : info || null;
  app.zoneRecv = Date.now();
  renderZoneChip();
  app.dirtyCards = true;
}

function renderZoneChip() {
  const c = ZoneView.chip(app.zoneInfo, Date.now(), app.zoneRecv);
  setChip('zoneChip', c.level, c.text);
}

function onMessage(msg) {
  if (msg.type === 'snapshot') { applySnapshot(msg); return; }
  if (msg.type === 'zone') { setZone(msg.status, msg); return; }
  if (msg.type === 'command') { upsertHistory(msg.entry); return; }
  if (msg.type === 'procs') { setProcs(msg); return; }
  if (msg.type === 'scan') {
    const s = app.scan[msg.robot];
    if (s && s.on) { s.points = msg.points; s.t = Date.now(); s.source = msg.source; s.frame = msg.frame; s.offset = msg.offset_deg; }
    return;
  }
  if (msg.type === 'drive_denied') { driveDenied(msg.robot, msg.reason); return; }
  if (msg.type !== 'robot_update') return;
  const r = app.robots[msg.robot];
  if (!r) return;
  if (msg.field === 'online') {
    r.online = !!msg.data;
    addAlarm(r.online ? 'ok' : 'bad', msg.robot + (r.online ? ' 온라인' : ' 오프라인 (5초 이상 수신 없음)'));
  } else if (msg.field === 'patrol') {
    const prevState = r.patrol && r.patrol.state;
    r.patrol = msg.data;
    const s = msg.data && msg.data.state;
    if (s !== prevState) r.stateSince = Date.now();
    // "응답 없음" 이 표시된 뒤에 상태가 바뀌면(늦은 응답이거나 다른 작업의 진행) 그 안내는 더 이상 맞지 않는다
    if (r.lastCommand && r.lastCommand.result === 'no_response') r.noRespDismissed = r.lastCommand.id;
    if (s !== prevState && (s === 'FAILED' || s === 'RETRY' || s === 'STOPPED')) {
      addAlarm(s === 'STOPPED' ? 'warn' : 'bad', msg.robot + ' ' + s + (msg.data.detail ? ': ' + (PatrolView.failReason(msg.data.detail) || msg.data.detail) : ''));
    }
  } else if (msg.field === 'last_command') {
    r.lastCommand = msg.data;
  } else if (msg.field === 'last_task') {
    r.lastTask = msg.data;
  } else if (msg.field === 'pose' || msg.field === 'battery') {
    r[msg.field] = msg.data;
  }
  app.dirtyCards = true;
}

let attempt = 0;
let sock = null;
function sendWs(obj) {
  if (sock && sock.readyState === WebSocket.OPEN) { sock.send(JSON.stringify(obj)); return true; }
  return false;
}
function connect() {
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  sock = ws;
  ws.onopen = () => {
    if (attempt > 0) addAlarm('ok', '서버에 다시 연결됨');
    attempt = 0; app.wsUp = true; setChip('connChip', 'ok', '연결됨');
    // 서버는 연결이 끊기면 이 화면의 LiDAR 구독을 지운다. 켜 둔 토글은 다시 요청한다
    for (const [rid, s] of Object.entries(app.scan)) if (s.on) sendWs({ type: 'scan', robot: rid, on: true });
  };
  ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch (err) { console.error('메시지 처리 오류', err); } };
  ws.onclose = () => {
    driveHalt();  // 연결이 끊기면 서버도 0 속도를 보내지만, 화면에서도 즉시 조작을 멈춘다
    if (app.wsUp || attempt === 0) addAlarm('bad', '서버 연결 끊김');
    app.wsUp = false; setChip('connChip', 'bad', '연결 끊김');
    attempt += 1;
    checkSession();  // 인증이 켜져 있고 세션이 사라졌다면(만료, 서버 재시작) 로그인 화면을 띄운다
    setTimeout(connect, Math.min(5000, 1000 * attempt)); // 1초 -> 5초까지 1초씩 늘려가며 재연결
  };
  ws.onerror = () => ws.close();
}

// ---------- 마우스: 이동, 확대, 호버, 클릭 ----------
function evPos(e) { const r = canvas.getBoundingClientRect(); return { sx: e.clientX - r.left, sy: e.clientY - r.top }; }

function updateReadout(sx, sy) {
  if (!app.meta) return;
  const { px, py } = toPixel(sx, sy);
  const w = app.tf.pixelToWorld(px, py);
  const inside = px >= 0 && py >= 0 && px < app.meta.width && py < app.meta.height;
  el('pxOut').textContent = inside ? '(' + Math.floor(px) + ', ' + Math.floor(py) + ')' : '범위 밖';
  el('xyOut').textContent = w.x.toFixed(3) + ' m, ' + w.y.toFixed(3) + ' m';
  if (!inside) { el('occOut').textContent = '-'; return; }
  const v = app.gray[Math.floor(py) * app.meta.width + Math.floor(px)];
  el('occOut').textContent = MapMath.classifyOccupancy(v, app.meta) + ' (' + v + ')';
}

let drag = null;
canvas.addEventListener('pointerdown', (e) => {
  canvas.setPointerCapture(e.pointerId);
  const p = evPos(e);
  drag = { x0: p.sx, y0: p.sy, ox: app.view.ox, oy: app.view.oy, moved: false };
});
canvas.addEventListener('pointermove', (e) => {
  const p = evPos(e);
  if (drag) {
    const dx = p.sx - drag.x0, dy = p.sy - drag.y0;
    if (Math.abs(dx) + Math.abs(dy) > 4) { drag.moved = true; canvas.classList.add('dragging'); }
    if (drag.moved) { app.view.ox = drag.ox + dx; app.view.oy = drag.oy + dy; app.fitted = false; }
  }
  updateReadout(p.sx, p.sy);
});
canvas.addEventListener('pointerup', (e) => {
  canvas.classList.remove('dragging');
  if (drag && !drag.moved && app.meta) { // 이동 없이 뗐으면 클릭
    const p = evPos(e), { px, py } = toPixel(p.sx, p.sy);
    const hit = pointAt(p.sx, p.sy);
    if (hit) { // goto 포인트를 눌렀으면 경로에 추가하고, 그 밖은 기존처럼 좌표를 기록한다
      if (app.routeTarget) addRoutePoint(hit.name);
    } else if (px >= 0 && py >= 0 && px < app.meta.width && py < app.meta.height) {
      const w = app.tf.pixelToWorld(px, py);
      app.clicks.push({ x: w.x, y: w.y });
      renderClicks();
    }
  }
  drag = null;
});
canvas.addEventListener('pointercancel', () => { drag = null; canvas.classList.remove('dragging'); });
canvas.addEventListener('wheel', (e) => {
  if (!app.meta) return;
  e.preventDefault();
  const p = evPos(e), v = app.view;
  const factor = Math.exp(-e.deltaY * 0.0015);
  const next = Math.min(MAX_SCALE, Math.max(v.fitScale * MIN_ZOOM_RATIO, v.scale * factor));
  const { px, py } = toPixel(p.sx, p.sy); // 커서 아래 지점을 고정한 채 확대
  v.scale = next; v.ox = p.sx - px * next; v.oy = p.sy - py * next;
  app.fitted = false;
  updateReadout(p.sx, p.sy);
}, { passive: false });

// ---------- 클릭 좌표 목록 ----------
function fmt(c) { return c.x.toFixed(3) + ', ' + c.y.toFixed(3); }

async function copyText(text, btn) {
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch (_) {
    const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
    ta.remove();
  }
  if (btn) { const old = btn.textContent; btn.textContent = ok ? '복사됨' : '실패'; setTimeout(() => (btn.textContent = old), 900); }
}

function renderClicks() {
  const ul = el('pointsList');
  ul.replaceChildren();
  el('pointsEmpty').style.display = app.clicks.length ? 'none' : 'block';
  app.clicks.forEach((c, i) => {
    const li = document.createElement('li');
    const label = document.createElement('span'); label.textContent = 'C' + (i + 1) + '  ' + fmt(c);
    const box = document.createElement('span');
    const cp = document.createElement('button'); cp.type = 'button'; cp.textContent = '복사';
    cp.onclick = () => copyText(fmt(c), cp);
    const del = document.createElement('button'); del.type = 'button'; del.textContent = '삭제';
    del.onclick = () => { app.clicks.splice(i, 1); renderClicks(); };
    box.append(cp, ' ', del);
    li.append(label, box);
    ul.appendChild(li);
  });
}
el('copyAllBtn').onclick = (e) => { if (app.clicks.length) copyText(app.clicks.map((c, i) => 'C' + (i + 1) + ': ' + fmt(c)).join('\n'), e.target); };
el('clearBtn').onclick = () => { app.clicks = []; renderClicks(); };
el('fitBtn').onclick = fitView;
el('gridBtn').onclick = () => {
  app.showGrid = !app.showGrid;
  el('gridBtn').setAttribute('aria-pressed', String(app.showGrid));
};

// ---------- 비상정지 (FR4-7, FR4-8) ----------
// 상단 '비상 정지' 는 모든 로봇, 로봇별 비상 정지는 로봇 카드 안의 버튼이다.
// 확인 팝업도, 1초 잠금도 없다: 누를 때마다 보낸다 (정지 요청을 막는 일이 없어야 한다). 서버가 stop 과 cmd_vel 0 속도 burst 를 보낸다.
async function estop(id) {
  driveHalt();
  const url = id ? '/api/robots/' + encodeURIComponent(id) + '/estop' : '/api/estop';
  const who = id || '모두';
  try {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const data = await res.json().catch(() => ({}));
    const items = data.results || [data];
    const errs = items.flatMap((r) => (r.errors || []).map((e) => (r.robot ? r.robot + ': ' : '') + e));
    if (!res.ok) addAlarm('bad', who + ' 비상정지 실패: ' + (errs.join(' / ') || data.error || res.status));
    else addAlarm(errs.length ? 'warn' : 'ok', who + ' 비상정지 전송' + (errs.length ? ' (일부 실패: ' + errs.join(' / ') + ')' : ' (stop + 0 속도)'));
  } catch (err) {
    addAlarm('bad', who + ' 비상정지 전송 오류: ' + err.message);
  }
}

el('estopAll').onclick = () => estop(null);

// ---------- LiDAR 토글 (FR4-5) ----------
function setupScanToggles() {
  const box = el('scanToggles');
  box.replaceChildren();
  for (const id of app.order) {
    const label = document.createElement('label'); label.className = 'check';
    const cb = document.createElement('input'); cb.type = 'checkbox'; cb.dataset.robot = id;
    cb.checked = !!(app.scan[id] && app.scan[id].on);
    cb.onchange = () => setScan(id, cb.checked);
    const info = document.createElement('small'); info.className = 'scan-info'; info.dataset.robot = id;
    label.append(cb, document.createTextNode(' ' + id + ' LiDAR '), info);
    box.appendChild(label);
  }
}

// LiDAR 점을 어떤 기준으로 그리는지 보여 준다. tf 면 센서의 실제 위치/방향, amcl 이면 로봇 pose + 설정의 보정 각도다.
function scanInfoText(s) {
  if (!s || !s.on) return '';
  if (!s.source) return '(수신 대기)';
  if (s.source === 'tf') return '(tf: 센서 프레임 ' + s.frame + ')';
  return '(amcl 위치 + 보정 ' + (s.offset || 0) + '°, tf 없음: 프레임 ' + s.frame + ')';
}
function renderScanInfo() {
  document.querySelectorAll('#scanToggles .scan-info').forEach((n) => { n.textContent = scanInfoText(app.scan[n.dataset.robot]); });
}

function setScan(id, on) {
  app.scan[id] = { on, points: [], t: 0 };
  if (!sendWs({ type: 'scan', robot: id, on })) addAlarm('warn', '서버에 연결되면 LiDAR 가 켜집니다');
}

// ---------- 수동 조작 (FR4-9) ----------
// 데드맨: 버튼/방향키를 누르는 동안만 조작 입력을 보낸다. 떼기, 포커스 잃음, 탭 숨김, 연결 끊김, 체크 해제, 로봇 변경은 모두 즉시 정지한다.
// 서버도 입력이 0.5초 없거나 연결이 끊기면 스스로 0 속도를 보낸다 (화면 쪽 정지는 첫 번째 방어선일 뿐이다).
const DRIVE_KEYS = { ArrowUp: 'fwd', w: 'fwd', W: 'fwd', ArrowDown: 'back', s: 'back', S: 'back',
                     ArrowLeft: 'left', a: 'left', A: 'left', ArrowRight: 'right', d: 'right', D: 'right' };
const drive = { dirs: new Set(), timer: null, sent: false };

function driveRobotId() { return el('driveRobot').value; }

function driveAllowed(id) {
  const r = app.robots[id], m = app.motion && app.motion.manual;
  if (!isOperator()) return '보기 전용 계정은 수동 조작할 수 없습니다';
  if (!r || !m || !m.enabled) return '수동 조작이 설정에서 꺼져 있습니다';
  if (!app.wsUp) return '서버에 연결되어 있지 않습니다';
  if (!r.online) return id + ' 가 오프라인입니다';
  if (r.patrol && ZoneView.WORKING.includes(r.patrol.state)) return '순찰 중에는 수동 조작을 할 수 없습니다 (' + r.patrol.state + ')';
  return null;
}

function driveVector() {
  const m = app.motion.manual, d = drive.dirs;
  return { linear: ((d.has('fwd') ? 1 : 0) - (d.has('back') ? 1 : 0)) * m.max_linear,
           angular: ((d.has('left') ? 1 : 0) - (d.has('right') ? 1 : 0)) * m.max_angular };
}

function driveSend() {
  const id = driveRobotId(), why = driveAllowed(id);
  if (!el('driveEnable').checked || why || !drive.dirs.size) return driveHalt();
  const v = driveVector();
  sendWs({ type: 'drive', robot: id, linear: v.linear, angular: v.angular });
  drive.sent = true;
}

function driveUpdate() {
  for (const b of el('drivePad').children) b.classList.toggle('down', drive.dirs.has(b.dataset.dir));
  if (!drive.dirs.size) return driveHalt();
  if (!drive.timer) drive.timer = setInterval(driveSend, 1000 / app.motion.manual.rate_hz);
  driveSend();
}

function driveHalt() {
  drive.dirs.clear();
  if (drive.timer) { clearInterval(drive.timer); drive.timer = null; }
  for (const b of el('drivePad').children) b.classList.remove('down');
  if (drive.sent) { sendWs({ type: 'drive_stop' }); drive.sent = false; }
}

function driveDenied(id, reason) {
  driveHalt();
  el('driveMsg').textContent = '거절됨: ' + reason;
  addAlarm('warn', id + ' 수동 조작 거절: ' + reason);
}

function renderDrivePanel() {
  const id = driveRobotId(), enabled = el('driveEnable').checked;
  const why = id ? driveAllowed(id) : '로봇 없음';
  const m = app.motion && app.motion.manual;
  el('driveEnable').disabled = !!why;
  if (why && enabled) { el('driveEnable').checked = false; driveHalt(); }
  for (const b of el('drivePad').children) b.disabled = !!why || !el('driveEnable').checked;
  el('driveInfo').textContent = m ? '선속도 최대 ' + m.max_linear + ' m/s, 각속도 최대 ' + m.max_angular + ' rad/s. 버튼이나 방향키(WASD)를 누르는 동안만 움직이고, ' + m.input_timeout_sec + '초 동안 입력이 없거나 연결이 끊기면 정지합니다.' : '';
  if (why) el('driveMsg').textContent = why;
  else if (el('driveMsg').textContent.startsWith('거절됨') === false) el('driveMsg').textContent = enabled ? '조작 중이 아님' : '';
}

function setupDrive() {
  const sel = el('driveRobot'), cur = sel.value;
  sel.replaceChildren();
  for (const id of app.order) { const o = document.createElement('option'); o.value = id; o.textContent = id; sel.appendChild(o); }
  if (app.order.includes(cur)) sel.value = cur;
}

for (const b of el('drivePad').children) {
  const dir = b.dataset.dir;
  b.addEventListener('pointerdown', (e) => {
    if (b.disabled) return;
    e.preventDefault();
    b.setPointerCapture(e.pointerId);
    drive.dirs.add(dir); driveUpdate();
  });
  for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture', 'pointerleave']) {
    b.addEventListener(ev, () => { if (drive.dirs.delete(dir)) driveUpdate(); });
  }
  b.addEventListener('contextmenu', (e) => e.preventDefault());
}
window.addEventListener('keydown', (e) => {
  const dir = DRIVE_KEYS[e.key];
  if (!dir || !el('driveEnable').checked || el('driveEnable').disabled) return;
  if (/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement && document.activeElement.tagName) && document.activeElement.id !== 'driveEnable') return;
  e.preventDefault();
  if (!drive.dirs.has(dir)) { drive.dirs.add(dir); driveUpdate(); }
});
window.addEventListener('keyup', (e) => { const dir = DRIVE_KEYS[e.key]; if (dir && drive.dirs.delete(dir)) driveUpdate(); });
window.addEventListener('blur', driveHalt);
window.addEventListener('pagehide', driveHalt);
document.addEventListener('visibilitychange', () => { if (document.hidden) driveHalt(); });
el('driveEnable').onchange = () => { driveHalt(); el('driveMsg').textContent = ''; renderDrivePanel(); };
el('driveRobot').onchange = () => { driveHalt(); el('driveMsg').textContent = ''; renderDrivePanel(); };

// ---------- 패널 크기 조절 ----------
// 사이드바 너비(SIDE)와 명령 이력 높이(HIST)를 끌어서 바꾼다. 값은 .app 의 CSS 변수(--side-w, --hist-h)이고 브라우저에 저장한다.
// 지도 캔버스는 ResizeObserver 로 따라간다. 사이드바의 각 섹션은 제목을 눌러 접고 펼친다.
// ---------- 로그인 ----------
function renderAuth() {
  const me = app.me, box = el('authBox');
  box.hidden = !(me && me.auth && me.role);
  el('authRole').textContent = me && me.role ? (me.role === 'operator' ? '명령 가능' : '보기 전용') : '';
  app.dirtyCards = true;
  renderProcs();
}

function showLogin(message) {
  const dlg = el('loginDlg');
  el('loginErr').textContent = message || '';
  if (!dlg.open) dlg.showModal();
  setTimeout(() => el('loginPw').focus(), 0);
}

async function checkSession() {
  if (!app.me || !app.me.auth) return;
  try {
    const me = await (await fetch('/api/me')).json();
    if (!me.role) { app.me = me; renderAuth(); showLogin('세션이 만료됐습니다. 다시 로그인하세요'); }
  } catch (e) { /* 서버가 내려가 있으면 다시 연결될 때까지 기다린다 */ }
}

el('loginDlg').addEventListener('cancel', (e) => e.preventDefault());  // 로그인 전에는 닫을 수 없다
el('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = el('loginBtn');
  btn.disabled = true; el('loginErr').textContent = '';
  try {
    const res = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: el('loginPw').value }) });
    const data = await res.json().catch(() => ({}));
    if (res.ok) { el('loginPw').value = ''; location.reload(); return; }
    el('loginErr').textContent = data.error || '로그인 실패 (' + res.status + ')';
    el('loginPw').select();
  } catch (err) {
    el('loginErr').textContent = '서버에 연결하지 못했습니다: ' + err.message;
  } finally {
    btn.disabled = false;
  }
});
el('logoutBtn').onclick = async () => {
  driveHalt();
  try { await fetch('/api/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); } catch (e) { /* 무시 */ }
  location.reload();
};

// ---------- 시스템 패널: 프로세스 시작/정지 ----------
function setProcs(msg) { app.procs = msg; app.procsRecv = Date.now(); renderProcs(); }

// 서버는 상태가 바뀔 때만 보내므로, 실행 시간은 화면에서 1초마다 흘려 보낸다
function procSubText(p) {
  const up = ProcView.uptimeText(ProcView.uptimeNow(p, Date.now(), app.procsRecv || Date.now()));
  return [p.kind === 'ssh' ? 'SSH ' + (p.host || '(host 미설정)') : '이 PC', up].filter(Boolean).join(' · ');
}
function tickProcUptimes() {
  if (!app.procs) return;
  for (const p of app.procs.procs) {
    const li = document.querySelector('#procList li[data-id="' + p.id + '"]');
    if (li) { const sub = li.querySelector('.proc-sub'), t = procSubText(p); if (sub.textContent !== t) sub.textContent = t; }
  }
}

function procCtx() {
  return { enabled: !!(app.procs && app.procs.enabled), role: app.me && app.me.role, seqRunning: !!(app.procs && app.procs.sequence && app.procs.sequence.state === 'running') };
}

async function procPost(path, body) {
  try {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) showLogin('세션이 만료됐습니다. 다시 로그인하세요');
    else if (!res.ok) addAlarm('bad', (data.error || '요청 실패 (' + res.status + ')'));
    return res.ok;
  } catch (err) {
    addAlarm('bad', '프로세스 제어 요청 오류: ' + err.message);
    return false;
  }
}

async function procStart(p) {
  await procPost('/api/procs/' + encodeURIComponent(p.id) + '/start');
}

async function procStop(p) {
  if (p.confirm_stop && !(await confirmDialog(ProcView.stopSummary(p)))) return;
  await procPost('/api/procs/' + encodeURIComponent(p.id) + '/stop', { confirm: !!p.confirm_stop });
}

async function loadProcLog(id) {
  try {
    const res = await fetch('/api/procs/' + encodeURIComponent(id) + '/log');
    if (!res.ok) return;
    const data = await res.json();
    const pre = document.querySelector('.proc-log[data-id="' + id + '"]');
    if (pre) {
      const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
      pre.textContent = data.lines.join('\n') || '(출력 없음)';
      if (atEnd) pre.scrollTop = pre.scrollHeight;
    }
  } catch (e) { /* 다음 갱신에서 다시 시도 */ }
}

const procLogN = {};   // 로그를 마지막으로 받았을 때의 줄 수 (줄이 늘었을 때만 다시 받는다)
function renderProcs() {
  const pp = app.procs, list = el('procList');
  if (!pp) return;
  const ctx = procCtx(), procs = pp.procs;
  const note = !pp.enabled ? '프로세스 제어가 꺼져 있습니다. 서버를 PINKY_OPERATOR_PASSWORD 환경 변수와 함께 실행해야 켜집니다.'
    : ctx.role !== 'operator' ? '보기 전용 계정입니다. 상태만 볼 수 있습니다.' : '';
  el('procNote').textContent = note;
  const sa = ProcView.startAllBlocked(procs, ctx), oa = ProcView.stopAllBlocked(procs, ctx);
  el('procStartAll').disabled = !!sa; el('procStartAll').title = sa || '브릿지 → zone_manager → bringup → map 순서로 시작';
  el('procStopAll').disabled = !!oa; el('procStopAll').title = oa || '시작의 반대 순서로 정지';
  const seq = pp.sequence;
  const labelOf = (id) => (procs.find((p) => p.id === id) || { label: id }).label;
  el('procSeq').textContent = ProcView.sequenceText(seq, labelOf);
  el('procSeq').style.color = seq && seq.state === 'failed' ? 'var(--bad)' : '';
  // 행이 이미 있으면 내용만 고친다 (로그를 보고 있는 도중에 목록이 다시 그려져 스크롤이 튀지 않게)
  const have = new Map([...list.querySelectorAll('li[data-id]')].map((li) => [li.dataset.id, li]));
  // 기능별 묶음: 설정의 group 이름이 같은 것끼리 한 카드로 묶는다 (순서는 처음 나온 순서)
  const groupBox = (name) => {
    let g = [...list.children].find((c) => c.dataset.group === name);
    if (!g) {
      g = document.createElement('div'); g.className = 'proc-group'; g.dataset.group = name;
      g.innerHTML = '<div class="proc-group-head"><span class="proc-group-name"></span><span class="proc-group-sum"></span></div><ul class="proc-rows"></ul>';
      g.querySelector('.proc-group-name').textContent = name;
      list.appendChild(g);
    }
    return g;
  };
  for (const p of procs) {
    let li = have.get(p.id);
    if (!li) {
      li = document.createElement('li'); li.dataset.id = p.id;
      li.innerHTML = '<div class="proc-head"><span class="proc-name"></span><span class="proc-state"></span><span class="proc-sub"></span>' +
        '<span class="proc-actions"><button type="button" data-act="start">시작</button><button type="button" data-act="stop">정지</button><button type="button" data-act="log">로그</button></span></div>' +
        '<p class="proc-msg"></p><pre class="proc-log" hidden></pre>';
      li.querySelector('[data-act="start"]').onclick = () => procStart(app.procs.procs.find((x) => x.id === li.dataset.id));
      li.querySelector('[data-act="stop"]').onclick = () => procStop(app.procs.procs.find((x) => x.id === li.dataset.id));
      li.querySelector('[data-act="log"]').onclick = () => {
        app.procLogOpen = app.procLogOpen === li.dataset.id ? null : li.dataset.id;
        procLogN[li.dataset.id] = -1;
        renderProcs();
      };
      li.querySelector('.proc-log').dataset.id = p.id;
    }
    const gbox = groupBox(p.group || '기타');
    if (li.parentNode !== gbox.querySelector('.proc-rows')) gbox.querySelector('.proc-rows').appendChild(li);
    have.delete(p.id);
    const b = ProcView.badge(p);
    li.querySelector('.proc-name').textContent = p.label;
    const st = li.querySelector('.proc-state'); st.textContent = b.text; st.dataset.level = b.level;
    li.querySelector('.proc-sub').textContent = procSubText(p);
    const sb = ProcView.startBlocked(p, ctx), tb = ProcView.stopBlocked(p, ctx);
    const start = li.querySelector('[data-act="start"]'), stop = li.querySelector('[data-act="stop"]');
    start.disabled = !!sb; start.title = sb || '';
    stop.disabled = !!tb; stop.title = tb || '';
    li.querySelector('.proc-msg').textContent = p.message && p.state !== 'running' ? p.message : '';
    const pre = li.querySelector('.proc-log'), open = app.procLogOpen === p.id;
    pre.hidden = !open;
    if (open && pp.enabled && ctx.role === 'operator' && procLogN[p.id] !== p.log_n) { procLogN[p.id] = p.log_n; loadProcLog(p.id); }
  }
  for (const li of have.values()) li.remove();
  for (const g of [...list.children]) {   // 묶음 머리글: 실행 중 개수 요약. 비어 버린 묶음은 지운다
    const rows = procs.filter((p) => (p.group || '기타') === g.dataset.group);
    if (!rows.length) { g.remove(); continue; }
    const set = rows.filter((p) => p.configured !== false && p.state !== 'unset');
    const run = set.filter((p) => p.state === 'running').length;
    const bad = set.filter((p) => p.state === 'failed').length;
    const sum = g.querySelector('.proc-group-sum');
    sum.textContent = !set.length ? '명령 미설정' : run + '/' + set.length + ' 실행' + (bad ? ' · 실패 ' + bad : '');
    sum.dataset.level = bad ? 'bad' : run && run === set.length ? 'ok' : run ? 'warn' : 'dim';
  }
}

el('procStartAll').onclick = async () => {
  const procs = app.procs.procs;
  if (!(await confirmDialog(ProcView.startAllSummary(procs)))) return;
  await procPost('/api/procs/start_all');
};
el('procStopAll').onclick = async () => {
  const procs = app.procs.procs;
  if (!(await confirmDialog(ProcView.stopAllSummary(procs)))) return;
  await procPost('/api/procs/stop_all', { confirm: true });
};

// ---------- 사이드바 탭 ----------
const TAB_KEY = 'pinky.tab.v1';
function setupTabs() {
  const bar = el('sideTabs');
  const panels = [...document.querySelectorAll('.sidebar > section[data-tab]')];
  const tabs = {};
  let saved = null;
  try { saved = localStorage.getItem(TAB_KEY); } catch (e) { /* 저장소를 못 쓰면 기본 탭 */ }
  for (const sec of panels) {
    const name = sec.dataset.tab;
    const b = document.createElement('button');
    b.type = 'button'; b.id = 'tab-' + name; b.className = 'tab'; b.textContent = sec.dataset.tabLabel;
    b.setAttribute('role', 'tab'); b.setAttribute('aria-controls', sec.id);
    sec.setAttribute('role', 'tabpanel'); sec.setAttribute('aria-labelledby', b.id);
    b.addEventListener('click', () => show(name, true));
    b.addEventListener('keydown', (e) => {
      const i = panels.indexOf(sec), n = panels.length;
      const to = e.key === 'ArrowRight' ? (i + 1) % n : e.key === 'ArrowLeft' ? (i + n - 1) % n : e.key === 'Home' ? 0 : e.key === 'End' ? n - 1 : -1;
      if (to < 0) return;
      e.preventDefault(); show(panels[to].dataset.tab, true); tabs[panels[to].dataset.tab].focus();
    });
    tabs[name] = b; bar.appendChild(b);
  }
  function show(name, persist) {
    if (!tabs[name]) name = panels[0].dataset.tab;
    for (const sec of panels) {
      const on = sec.dataset.tab === name;
      sec.hidden = !on;
      tabs[sec.dataset.tab].setAttribute('aria-selected', String(on));
      tabs[sec.dataset.tab].tabIndex = on ? 0 : -1;
    }
    app.tab = name;
    if (persist) { try { localStorage.setItem(TAB_KEY, name); } catch (e) { /* 무시 */ } }
  }
  app.showTab = (name) => show(name, false);
  show(saved, false);
}

// ---------- 레이아웃 편집 ----------
// 지도, 사이드바, 명령 이력 세 패널을 작업 영역 안의 24x20 격자에 놓는다. 좌표는 격자 칸 수(정수)라서 창 크기가 바뀌어도 비율이 유지된다.
// 알람바(비상 정지)와 메뉴바는 항상 보여야 해서 배치 대상이 아니다.
const LAYOUT_KEY = 'pinky.layout.v2';
const GRID = { cols: 24, rows: 20 };
const MIN_SIZE = { w: 4, h: 3 };
const DEFAULT_RECTS = {
  map: { x: 0, y: 0, w: 18, h: 16 },
  side: { x: 18, y: 0, w: 6, h: 16 },
  hist: { x: 0, y: 16, w: 24, h: 4 },
};
const PANEL_ELS = { map: 'panelMap', side: 'panelSide', hist: 'panelHist' };
const cloneRects = (r) => JSON.parse(JSON.stringify(r));

function validRect(r) {
  const EPS = 1e-6;   // 분할선으로 끈 값은 정수 칸이 아니라 소수다
  return r && ['x', 'y', 'w', 'h'].every((k) => Number.isFinite(r[k]))
    && r.w >= MIN_SIZE.w - EPS && r.h >= MIN_SIZE.h - EPS && r.x >= -EPS && r.y >= -EPS && r.x + r.w <= GRID.cols + EPS && r.y + r.h <= GRID.rows + EPS;
}
function loadLayout() {
  try {
    const v = JSON.parse(localStorage.getItem(LAYOUT_KEY) || 'null');
    if (v && v.rects && Object.keys(PANEL_ELS).every((k) => validRect(v.rects[k]))) return cloneRects(v.rects);
  } catch (e) { /* 저장소를 못 쓰거나 값이 깨졌으면 기본 배치 */ }
  return cloneRects(DEFAULT_RECTS);
}
function saveLayout(rects) { try { localStorage.setItem(LAYOUT_KEY, JSON.stringify({ rects })); } catch (e) { /* 무시 */ } }

const lay = { rects: loadLayout(), backup: null, editing: false, z: 1 };
function applyLayout() {
  for (const [k, id] of Object.entries(PANEL_ELS)) {
    const r = lay.rects[k], st = el(id).style;
    st.setProperty('--x', r.x / GRID.cols); st.setProperty('--y', r.y / GRID.rows);
    st.setProperty('--w', r.w / GRID.cols); st.setProperty('--h', r.h / GRID.rows);
  }
}

// 이동/크기 조절: 방향 문자열(n,s,e,w 조합, 'move' 는 이동)과 시작 사각형, 격자 칸 이동량으로 새 사각형을 구한다 (순수 계산이라 따로 뗐다)
function dragRect(start, dir, dx, dy) {
  let { x, y, w, h } = start;
  if (dir === 'move') {
    x = clamp(x + dx, 0, GRID.cols - w); y = clamp(y + dy, 0, GRID.rows - h);
    return { x, y, w, h };
  }
  if (dir.includes('e')) w = clamp(start.w + dx, MIN_SIZE.w, GRID.cols - start.x);
  if (dir.includes('s')) h = clamp(start.h + dy, MIN_SIZE.h, GRID.rows - start.y);
  if (dir.includes('w')) { const right = start.x + start.w; x = clamp(start.x + dx, 0, right - MIN_SIZE.w); w = right - x; }
  if (dir.includes('n')) { const bottom = start.y + start.h; y = clamp(start.y + dy, 0, bottom - MIN_SIZE.h); h = bottom - y; }
  return { x, y, w, h };
}
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function setupLayoutEdit() {
  const ws = el('workspace');
  const overlays = {};
  for (const [key, id] of Object.entries(PANEL_ELS)) {
    const panel = el(id);
    const ov = document.createElement('div'); ov.className = 'edit-overlay'; ov.dataset.panel = key;
    const title = document.createElement('div'); title.className = 'edit-title'; title.textContent = panel.dataset.title + ' (끌어서 이동)';
    ov.appendChild(title);
    for (const dir of ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']) {
      const h = document.createElement('div'); h.className = 'edit-handle edit-' + dir; h.dataset.dir = dir; ov.appendChild(h);
    }
    ov.addEventListener('pointerdown', (e) => {
      if (!lay.editing || e.button > 0) return;
      e.preventDefault();
      const dir = e.target.dataset.dir || 'move';
      const box = ws.getBoundingClientRect();
      const cw = box.width / GRID.cols, ch = box.height / GRID.rows;
      const start = { ...lay.rects[key] }, x0 = e.clientX, y0 = e.clientY;
      panel.style.zIndex = ++lay.z;   // 최근에 만진 패널이 위로 온다
      ov.setPointerCapture(e.pointerId);
      const move = (ev) => {
        lay.rects[key] = dragRect(start, dir, Math.round((ev.clientX - x0) / cw), Math.round((ev.clientY - y0) / ch));
        applyLayout();
      };
      const end = () => { ov.removeEventListener('pointermove', move); ov.removeEventListener('pointerup', end); ov.removeEventListener('pointercancel', end); };
      ov.addEventListener('pointermove', move); ov.addEventListener('pointerup', end); ov.addEventListener('pointercancel', end);
    });
    panel.appendChild(ov);
    overlays[key] = ov;
  }
  const setEditing = (on) => {
    lay.editing = on;
    document.body.classList.toggle('layout-editing', on);
    el('layoutEditBtn').setAttribute('aria-pressed', String(on));
    el('layoutEditActions').hidden = !on;
    if (app.placeSplits) app.placeSplits();
    el('layoutEditBtn').hidden = on;
  };
  el('layoutEditBtn').onclick = () => { lay.backup = cloneRects(lay.rects); setEditing(true); };
  el('layoutDone').onclick = () => { saveLayout(lay.rects); lay.backup = null; setEditing(false); };
  el('layoutReset').onclick = () => { lay.rects = cloneRects(DEFAULT_RECTS); applyLayout(); };
  const cancel = () => { if (lay.backup) { lay.rects = lay.backup; lay.backup = null; applyLayout(); } setEditing(false); };
  el('layoutCancel').onclick = cancel;
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && lay.editing) { e.preventDefault(); cancel(); } });
  app.layout = lay;  // 테스트가 확인한다
}

applyLayout();
setupTabs();
setupLayoutEdit();

// ---------- 분할선: 편집 모드 없이 경계를 끌어 크기 조절 ----------
// 지도|사이드바 사이(세로선)와 지도·사이드바|이력 사이(가로선). 두 패널이 맞닿아 있을 때만 보인다 (자유 배치로 떨어뜨렸다면 숨김).
// 더블클릭은 기본 배치, 방향키는 한 칸의 1/2 씩(Shift 는 2칸). 놓을 때 저장한다.
function splitGeom() {
  const m = lay.rects.map, sd = lay.rects.side, h = lay.rects.hist, E = 0.01;
  const v = Math.abs(m.x + m.w - sd.x) < E && Math.abs(m.y - sd.y) < E && Math.abs(m.h - sd.h) < E;
  const hz = Math.abs(Math.max(m.y + m.h, sd.y + sd.h) - h.y) < E && Math.abs(m.y + m.h - (sd.y + sd.h)) < E && Math.abs(h.x) < E && Math.abs(h.w - GRID.cols) < E;
  return { v, h: hz };
}
function moveSplitV(b) {   // b: 새 경계 x (칸)
  const m = lay.rects.map, sd = lay.rects.side, right = sd.x + sd.w;
  b = clamp(b, m.x + MIN_SIZE.w, right - MIN_SIZE.w);
  m.w = b - m.x; sd.x = b; sd.w = right - b;
}
function moveSplitH(b) {   // b: 새 경계 y (칸)
  const m = lay.rects.map, sd = lay.rects.side, h = lay.rects.hist, bottom = h.y + h.h;
  b = clamp(b, Math.max(m.y, sd.y) + MIN_SIZE.h, bottom - MIN_SIZE.h);
  m.h = b - m.y; sd.h = b - sd.y; h.y = b; h.h = bottom - b;
}
function setupSplits() {
  const ws = el('workspace');
  const defs = [
    { id: 'splitV', cls: 'split-v', key: 'v', label: '지도와 사이드바 경계 (끌어서 너비 조절, 더블클릭: 초기화)', axis: 'x',
      get: () => lay.rects.side.x, set: moveSplitV,
      place: (st) => { const sd = lay.rects.side; st.left = `calc(${sd.x / GRID.cols * 100}% - 5px)`; st.top = `${sd.y / GRID.rows * 100}%`; st.height = `${sd.h / GRID.rows * 100}%`; } },
    { id: 'splitH', cls: 'split-h', key: 'h', label: '지도와 명령 이력 경계 (끌어서 높이 조절, 더블클릭: 초기화)', axis: 'y',
      get: () => lay.rects.hist.y, set: moveSplitH,
      place: (st) => { st.top = `calc(${lay.rects.hist.y / GRID.rows * 100}% - 5px)`; st.left = '0'; st.width = '100%'; } },
  ];
  const nodes = defs.map((d) => {
    const n = document.createElement('div');
    n.id = d.id; n.className = 'split ' + d.cls; n.tabIndex = 0;
    n.setAttribute('role', 'separator'); n.setAttribute('aria-orientation', d.axis === 'x' ? 'vertical' : 'horizontal'); n.title = d.label;
    ws.appendChild(n);
    let drag = null;
    const finish = () => { if (!drag) return; drag = null; n.classList.remove('active'); document.body.classList.remove('resizing', 'col', 'row'); saveLayout(lay.rects); };
    n.addEventListener('pointerdown', (e) => {
      if (lay.editing || e.button > 0) return;
      e.preventDefault(); n.setPointerCapture(e.pointerId);
      drag = { start: d.axis === 'x' ? e.clientX : e.clientY, base: d.get() };
      n.classList.add('active'); document.body.classList.add('resizing', d.axis === 'x' ? 'col' : 'row');
    });
    n.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const box = ws.getBoundingClientRect();
      const px = (d.axis === 'x' ? e.clientX : e.clientY) - drag.start;
      d.set(drag.base + px / (d.axis === 'x' ? box.width / GRID.cols : box.height / GRID.rows));
      applyLayout(); placeSplits();
    });
    for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture']) n.addEventListener(ev, finish);
    n.addEventListener('dblclick', () => {
      if (lay.editing) return;
      d.set(DEFAULT_RECTS[d.key === 'v' ? 'side' : 'hist'][d.key === 'v' ? 'x' : 'y']);
      applyLayout(); placeSplits(); saveLayout(lay.rects);
    });
    n.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 2 : 0.5;
      const dec = d.axis === 'x' ? 'ArrowLeft' : 'ArrowUp', inc = d.axis === 'x' ? 'ArrowRight' : 'ArrowDown';
      if (e.key === dec) d.set(d.get() - step); else if (e.key === inc) d.set(d.get() + step);
      else if (e.key === 'Home') d.set(DEFAULT_RECTS[d.key === 'v' ? 'side' : 'hist'][d.key === 'v' ? 'x' : 'y']); else return;
      e.preventDefault(); applyLayout(); placeSplits(); saveLayout(lay.rects);
    });
    return { d, n };
  });
  function placeSplits() {
    const g = splitGeom();
    for (const { d, n } of nodes) { n.hidden = lay.editing || !g[d.key]; if (!n.hidden) d.place(n.style); }
  }
  app.placeSplits = placeSplits;
  placeSplits();
}
setupSplits();

// ---------- 화면 전환 (메인 / 로그) ----------
function setView(name) {
  if (lay.editing) return;   // 레이아웃 편집 중에는 화면을 바꾸지 않는다 (완료/취소 먼저)
  const log = name === 'log';
  el('workspace').hidden = log;
  el('logView').hidden = !log;
  document.querySelectorAll('.menu-item[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  document.querySelectorAll('[data-only-view]').forEach((n) => { n.hidden = n.dataset.onlyView !== name; });
  if (log) startLogs(); else stopLogs();
  if (!log) resizeCanvas();
}
document.querySelectorAll('.menu-item[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

// ---------- 로그 화면 ----------
// 서버가 모아 둔 로그(프로세스 출력, GUI 서버)를 1초마다 '마지막 번호 이후' 만 받아 소스 탭, 레벨, 검색으로 거른다.
const logs = { entries: [], last: 0, epoch: null, src: 'all', sources: [], labels: { gui: 'GUI 서버' }, timer: null, paused: false, busy: false, shown: 0 };

function logFilter() { return { src: logs.src, level: el('logLevel').value, q: el('logSearch').value.trim() }; }

function logLine(e) {
  const d = document.createElement('span'); d.className = 'ln'; d.dataset.lvl = e.lvl;
  const t = document.createElement('span'); t.className = 't'; t.textContent = LogView.timeText(e.t) + ' ';
  d.appendChild(t);
  if (logs.src === 'all') {
    const s = document.createElement('span'); s.className = 's'; s.textContent = '[' + (logs.labels[e.src] || e.src) + '] ';
    d.appendChild(s);
  }
  d.appendChild(document.createTextNode(e.text));
  return d;
}

function followBottom() {
  const b = el('logBody');
  if (el('logFollow').checked) b.scrollTop = b.scrollHeight;
}

function renderLogAll() {
  const rows = LogView.filter(logs.entries, logFilter());
  const body = el('logBody');
  body.replaceChildren();
  if (!rows.length) {
    const p = document.createElement('span'); p.className = 'empty';
    p.textContent = logs.entries.length ? '조건에 맞는 로그가 없습니다.' : '아직 로그가 없습니다. 시스템 탭에서 프로세스를 시작하면 출력이 여기에 나옵니다.';
    body.appendChild(p);
  } else {
    const frag = document.createDocumentFragment();
    for (const e of rows) frag.appendChild(logLine(e));
    body.appendChild(frag);
  }
  logs.shown = rows.length;
  el('logInfo').textContent = rows.length + ' / ' + logs.entries.length + ' 줄';
  followBottom();
}

function renderLogTabs() {
  const bar = el('logTabs'), c = LogView.counts(logs.entries);
  const list = [{ id: 'all', label: '전체' }, ...logs.sources.filter((s) => s.id === 'gui' || s.configured || c[s.id])];
  bar.replaceChildren();
  for (const s of list) {
    const b = document.createElement('button'); b.type = 'button'; b.className = 'tab'; b.setAttribute('role', 'tab');
    b.setAttribute('aria-selected', String(logs.src === s.id)); b.dataset.src = s.id;
    b.appendChild(document.createTextNode(s.label));
    const n = c[s.id] || { n: 0, warn: 0, error: 0 };
    if (n.error) { const x = document.createElement('span'); x.className = 'cnt e'; x.textContent = 'E' + n.error; b.appendChild(x); }
    if (n.warn) { const x = document.createElement('span'); x.className = 'cnt w'; x.textContent = 'W' + n.warn; b.appendChild(x); }
    if (!n.n) b.dataset.off = '1';
    b.onclick = () => { logs.src = s.id; renderLogTabs(); renderLogAll(); };
    bar.appendChild(b);
  }
}

async function pollLogs() {
  if (logs.busy) return;
  logs.busy = true;
  try {
    const res = await fetch('/api/logs?since=' + logs.last);
    if (res.status === 401) { showLogin('세션이 만료됐습니다. 다시 로그인하세요'); return; }
    if (res.status === 403) { el('logNote').textContent = '로그는 operator 로 로그인해야 볼 수 있습니다.'; return; }
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const d = await res.json();
    el('logNote').textContent = '';
    if (logs.epoch && logs.epoch !== d.epoch) {   // 서버가 다시 시작됐다: 번호가 처음부터 다시 시작하므로 처음부터 받는다
      logs.entries = []; logs.last = 0; logs.epoch = d.epoch;
      el('logNote').textContent = '서버가 다시 시작돼 로그를 처음부터 받습니다.';
      return;
    }
    logs.epoch = d.epoch;
    logs.sources = d.sources;
    logs.labels = Object.fromEntries(d.sources.map((x) => [x.id, x.label]));
    if (d.entries.length) {
      logs.entries = LogView.append(logs.entries, d.entries);
      logs.last = d.last;
      if (!logs.paused) {
        const f = logFilter(), body = el('logBody');
        const add = LogView.filter(d.entries, f);
        if (logs.shown === 0 || logs.entries.length >= LogView.MAX_KEEP) renderLogAll();   // 빈 안내 문구를 치우거나, 상한에서 앞쪽이 잘렸을 때는 다시 그린다
        else {
          for (const e of add) body.appendChild(logLine(e));
          logs.shown += add.length;
          el('logInfo').textContent = logs.shown + ' / ' + logs.entries.length + ' 줄';
          followBottom();
        }
      }
    }
    renderLogTabs();
  } catch (err) {
    el('logNote').textContent = '로그를 받지 못했습니다: ' + err.message;
  } finally {
    logs.busy = false;
  }
}

function startLogs() {
  if (logs.timer) return;
  renderLogAll();
  pollLogs();
  logs.timer = setInterval(pollLogs, 1000);
}
function stopLogs() { clearInterval(logs.timer); logs.timer = null; }

el('logLevel').onchange = renderLogAll;
el('logSearch').oninput = renderLogAll;
el('logPause').onclick = () => {
  logs.paused = !logs.paused;
  el('logPause').setAttribute('aria-pressed', String(logs.paused));
  el('logPause').textContent = logs.paused ? '재개' : '일시정지';
  if (!logs.paused) renderLogAll();
};
el('logCopy').onclick = () => copyText(LogView.copyText(LogView.filter(logs.entries, logFilter()), logs.labels), el('logCopy'));
el('logClear').onclick = () => { logs.entries = []; renderLogTabs(); renderLogAll(); };
el('logBody').addEventListener('scroll', () => {   // 위로 올려 읽는 중이면 따라가기를 끄고, 맨 아래로 내리면 다시 켠다
  const b = el('logBody'), atEnd = b.scrollHeight - b.scrollTop - b.clientHeight < 24;
  el('logFollow').checked = atEnd;
});
el('logFollow').onchange = followBottom;

// ---------- 시작 ----------
window.addEventListener('resize', resizeCanvas);
new ResizeObserver(resizeCanvas).observe(canvas);
resizeCanvas();
renderClicks();
function boot() {
connect();
fetch('/api/commands/config').then((r) => { if (!r.ok) throw new Error('명령 설정을 받지 못했습니다 (' + r.status + ')'); return r.json(); })
  .then((cfg) => { app.cfg = cfg; setupRoutePanel(); app.dirtyCards = true; })
  .catch((err) => addAlarm('bad', String(err.message || err)));
fetch('/api/zone/config').then((r) => { if (!r.ok) throw new Error('구역 설정을 받지 못했습니다 (' + r.status + ')'); return r.json(); })
  .then((z) => { app.zoneCfg = z; el('zoneChip').title = z.confirmed ? '' : '구역 영역은 초안입니다 (config/zone.yaml 의 confirmed: false)'; })
  .catch((err) => addAlarm('bad', String(err.message || err)));
// 경로 선을 켰을 때만 경로 추정(/api/plans)을 받는다 (서버는 첫 요청에서 계산해 캐시한다)
function ensurePlans() {
  if (app.plans || app.plansLoading) return;
  app.plansLoading = true;
  fetch('/api/plans').then((r) => { if (!r.ok) throw new Error('경로 추정을 받지 못해 경로 선을 직선으로 그립니다 (' + r.status + ')'); return r.json(); })
    .then((d) => { app.plans = d.plans; })
    .catch((err) => addAlarm('warn', String(err.message || err)))
    .finally(() => { app.plansLoading = false; });
}
function setShowRoutes(on) {
  app.showRoutes = on;
  el('routeLinesBtn').setAttribute('aria-pressed', String(on));
  if (on) ensurePlans();
}
el('routeLinesBtn').onclick = () => setShowRoutes(!app.showRoutes);
fetch('/api/motion/config').then((r) => { if (!r.ok) throw new Error('모션 설정을 받지 못했습니다 (' + r.status + ')'); return r.json(); })
  .then((m) => { app.motion = m; app.dirtyCards = true; })
  .catch((err) => addAlarm('bad', String(err.message || err)));
loadMap().catch((err) => { el('stageNote').textContent = String(err.message || err); addAlarm('bad', String(err.message || err)); });
}
setInterval(() => { app.dirtyCards = true; renderZoneChip(); tickProcUptimes(); }, 500); // STARTING 5초 안내처럼 시간이 지나야 바뀌는 표시용
requestAnimationFrame(frame);
// 로그인 상태를 먼저 확인한다: 인증이 켜져 있는데 로그인하지 않았다면 로그인 후에 화면을 불러온다 (서버 API 가 전부 401 이라서)
fetch('/api/me').then((r) => r.json()).then((me) => {
  app.me = me; renderAuth();
  if (me.auth && !me.role) showLogin(); else boot();
}).catch((err) => { addAlarm('bad', '로그인 상태를 확인하지 못했습니다: ' + err.message); boot(); });
