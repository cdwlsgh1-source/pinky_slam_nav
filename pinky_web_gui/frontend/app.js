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
  zone: null,
  wsUp: false,
  clicks: [],   // {x, y}
  hover: null,
  alarms: [],
  dirtyCards: true,
  fitted: false,
  showGrid: true,
};

// ---------- 유틸 ----------
const hhmmss = () => new Date().toTimeString().slice(0, 8);
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
  ctx.font = '10px ' + getComputedStyle(document.body).fontFamily;
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
  ctx.save();
  ctx.globalAlpha = app.wsUp ? 1 : 0.35;
  ctx.font = 'bold 12px ' + getComputedStyle(document.body).fontFamily;
  ctx.textAlign = 'center';
  ctx.lineWidth = 3; ctx.strokeStyle = 'rgba(0,0,0,.55)';
  ctx.strokeText(id, sx, sy - MARKER_R - 7);
  ctx.fillStyle = '#fff';
  ctx.fillText(id, sx, sy - MARKER_R - 7);
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
    ctx.font = 'bold 11px monospace'; ctx.fillStyle = '#f2a65a';
    ctx.fillText('C' + (i + 1), sx + 6, sy - 6);
  });
  for (const id of app.order) drawMarker(id, app.robots[id]);
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
  const head = document.createElement('div'); head.className = 'card-head';
  const name = document.createElement('span'); name.className = 'card-name'; name.textContent = id;
  const badge = document.createElement('span'); badge.className = 'badge';
  head.append(name, badge);
  const dl = document.createElement('dl');
  const f = {};
  for (const [key, label] of [['state', '상태'], ['batt', '배터리'], ['pos', '좌표']]) {
    const dt = document.createElement('dt'); dt.textContent = label;
    const dd = document.createElement('dd'); f[key] = dd;
    dl.append(dt, dd);
  }
  root.append(head, dl);
  el('robotCards').appendChild(root);
  return (cardEls[id] = { badge, ...f });
}

function renderCards() {
  for (const id of app.order) {
    const r = app.robots[id], c = ensureCard(id);
    c.badge.textContent = r.online ? 'online' : 'offline';
    c.badge.classList.toggle('on', r.online);
    const p = r.patrol;
    c.state.textContent = p && p.state ? p.state + (p.waypoint >= 0 ? ' #' + p.waypoint : '') + (p.detail ? ' ' + p.detail : '') : '-';
    const b = r.battery;
    c.batt.textContent = b && b.percentage != null ? b.percentage.toFixed(1) + ' %' + (b.voltage != null ? ' (' + b.voltage.toFixed(2) + ' V)' : '') : '-';
    c.pos.textContent = r.pose ? '(' + r.pose.x.toFixed(2) + ', ' + r.pose.y.toFixed(2) + ') m' : '-';
  }
}

// ---------- WebSocket ----------
function robotModel(id, i, data) {
  return { color: PALETTE[i % PALETTE.length], online: !!data.online, patrol: data.patrol, pose: data.pose,
           battery: data.battery, disp: null };
}

function applySnapshot(msg) {
  const ids = Object.keys(msg.robots);
  app.order = ids;
  ids.forEach((id, i) => {
    const prev = app.robots[id];
    const m = robotModel(id, i, msg.robots[id]);
    if (prev) m.disp = prev.disp;
    app.robots[id] = m;
  });
  for (const id of Object.keys(app.robots)) if (!ids.includes(id)) delete app.robots[id];
  setZone(msg.zone);
  app.dirtyCards = true;
}

function setZone(status) {
  app.zone = status;
  if (status == null) setChip('zoneChip', 'dim', '구역: -');
  else if (status === 'free') setChip('zoneChip', 'ok', '구역: 비어 있음');
  else setChip('zoneChip', 'warn', '구역: ' + status.replace('occupied_by:', '') + ' 점유');
}

function onMessage(msg) {
  if (msg.type === 'snapshot') { applySnapshot(msg); return; }
  if (msg.type === 'zone') { setZone(msg.status); return; }
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
    if (s !== prevState && (s === 'FAILED' || s === 'RETRY' || s === 'STOPPED')) {
      addAlarm(s === 'STOPPED' ? 'warn' : 'bad', msg.robot + ' ' + s + (msg.data.detail ? ': ' + msg.data.detail : ''));
    }
  } else if (msg.field === 'pose' || msg.field === 'battery') {
    r[msg.field] = msg.data;
  }
  app.dirtyCards = true;
}

let attempt = 0;
function connect() {
  const ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  ws.onopen = () => {
    if (attempt > 0) addAlarm('ok', '서버에 다시 연결됨');
    attempt = 0; app.wsUp = true; setChip('connChip', 'ok', '연결됨');
  };
  ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch (err) { console.error('메시지 처리 오류', err); } };
  ws.onclose = () => {
    if (app.wsUp || attempt === 0) addAlarm('bad', '서버 연결 끊김');
    app.wsUp = false; setChip('connChip', 'bad', '연결 끊김');
    attempt += 1;
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
    if (px >= 0 && py >= 0 && px < app.meta.width && py < app.meta.height) {
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

// ---------- 시작 ----------
window.addEventListener('resize', resizeCanvas);
new ResizeObserver(resizeCanvas).observe(canvas);
resizeCanvas();
renderClicks();
connect();
loadMap().catch((err) => { el('stageNote').textContent = String(err.message || err); addAlarm('bad', String(err.message || err)); });
requestAnimationFrame(frame);
