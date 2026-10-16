'use strict';
// 지도 화면. 좌표 변환은 mapmath.js 의 MapMath.createTransform 만 쓴다 (여기서 식을 다시 쓰지 않는다).

const PALETTE = ['#e8590c', '#1c7ed6', '#2f9e44', '#ae3ec9', '#f08c00', '#0b7285']; // 로봇별 고정 색 (robots 순서)
const OFFLINE_COLOR = '#8a8a8a';
const MARKER_R = 9;          // 마커 반지름 (화면 px, 확대해도 일정)
const MIN_ZOOM_RATIO = 0.5;  // 맞춤 배율 대비 최소 배율
const MAX_SCALE = 60;
const ALARM_KEEP = 100;      // 알람바(모니터링 화면)에 두는 최대 행 수. 서버도 같은 100건으로 자르며, 잘린 알람은 서버의 이력 파일에 남아 있다
const ALARM_INFO_KEY = 'pinky.alarminfo.v1';  // info 행 표시 여부 (브라우저별 편의 설정)
const ALARM_LOCAL_KEY = 'pinky.alarmlocal.v1';  // 브라우저 전용 알람 (서버가 모르는 것이라 새로고침해도 남기려고 저장한다)
const ALARM_LOCAL_KEEP = 100;
const ALARM_H_KEY = 'pinky.alarmheight.v1';     // 알람 목록 높이(px)
const ALARM_H = { def: 132, min: 56 };          // 기본/최소 높이. 최대는 화면 높이의 60%

const el = (id) => document.getElementById(id);
document.addEventListener('themechange', () => { if (typeof draw === 'function') draw(); }); // theme.js: 지도 배경/글자색은 CSS 변수라 다시 그린다
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
  silenced: false,    // SIL: 이 브라우저의 알람 소리 끄기 (저장하지 않는다)
  alarms: [],         // 알람바 행. 서버가 만든 알람(snapshot.alarms, WS alarm)과 브라우저 전용 알람(local: true)을 함께 둔다
  showInfo: true,     // 알람바에 info 행(명령 전송 기록 등)을 보일지
  dirtyCards: true,
  fitted: false,
  showGrid: true,
  cfg: null,          // /api/commands/config (로봇별 허용 지점, 홈, 대기, 지점 좌표, 임계 시간)
  routes: {},         // 로봇 id -> 경로 이름 목록 (goto 로 보낼 지점들)
  routeTarget: null,  // 지도에서 포인트를 클릭하면 경로가 추가되는 로봇
  history: [],        // 명령 이력, 최신이 앞 (서버가 보낸 command 이벤트로 갱신). 알람바로 통합한 뒤에는 화면에 그리지 않고 검증 스크립트가 읽는다
  locks: {},          // 'id:cmd' -> 연타 방지 해제 시각(ms)
  me: null,           // /api/me: {auth, role, procs_allowed, procs_configured}. role 이 manager 일 때만 명령·수동 조작·프로세스 제어 버튼이 켜진다
  procs: null,        // 서버가 보낸 프로세스 상태 {enabled, procs:[...], sequence}
  procLogOpen: null,  // 로그를 펼친 프로세스 id
};
const POINT_HIT_R = 14;      // 포인트 클릭 판정 반경 (화면 px)
const BTN_LOCK_MS = 1000;    // FR3-7: 같은 버튼 연타 방지

// ---------- 유틸 ----------
const isManager = () => !!app.me && app.me.role === 'manager';
function normAngle(a) { while (a > Math.PI) a -= 2 * Math.PI; while (a < -Math.PI) a += 2 * Math.PI; return a; }

// ---------- 알람바 ----------
// 서버가 만든 알람과 서버가 알 수 없는 브라우저 전용 알람(연결 끊김, 로드 실패, 화면 안내)을 한 목록으로 보여 준다.
// 브라우저 전용 알람은 id 가 'local-N' 이고 시각은 이 브라우저의 시계다 (서버 알람은 서버 시각 epoch).
const clock = (t) => new Date(t * 1000).toTimeString().slice(0, 8);   // 24시간제 HH:MM:SS
const STATE_LABEL = { active: '활성', cleared: '해소', acked: '확인됨', event: '-' };
const ALARM_LEVELS = ['HH', 'H', 'L', 'LL', 'info'];
const LOCAL_LEVEL = { ok: 'info', warn: 'L', bad: 'H' };   // 옛 addAlarm 의 ok/warn/bad 를 알람 레벨로
let localAlarmSeq = 0;

function loadLocalAlarms() {
  try {
    const v = JSON.parse(localStorage.getItem(ALARM_LOCAL_KEY) || '[]');
    return Array.isArray(v) ? v.filter((a) => a && typeof a.id === 'string' && a.id.startsWith('local-') && typeof a.text === 'string' && Number.isFinite(a.t_last)) : [];
  } catch (e) { return []; }
}
function saveLocalAlarms() {
  try { localStorage.setItem(ALARM_LOCAL_KEY, JSON.stringify(app.alarms.filter((a) => a.local).slice(0, ALARM_LOCAL_KEEP))); } catch (e) { /* 저장을 못 해도 이번 화면에서는 보인다 */ }
}

function loadShowInfo() { try { return localStorage.getItem(ALARM_INFO_KEY) !== '0'; } catch (e) { return true; } }
function saveShowInfo(v) { try { localStorage.setItem(ALARM_INFO_KEY, v ? '1' : '0'); } catch (e) { /* 저장을 못 해도 이번 화면에서는 바뀐다 */ } }
app.showInfo = loadShowInfo();
// 새로고침 전에 있던 브라우저 전용 알람을 되살린다. 끊김(active)은 이 화면이 다시 열렸다는 것 자체가 재연결이므로 해소로 바꾼다
app.alarms = loadLocalAlarms().map((a) => (a.state === 'active' ? Object.assign(a, { state: 'cleared', t_cleared: a.t_cleared || Date.now() / 1000 }) : a));
localAlarmSeq = app.alarms.reduce((m, a) => Math.max(m, parseInt(a.id.slice(6), 10) || 0), 0);

// ACK 를 받은 해소 알람은 알람바에서 숨긴다 (서버 backend/alarms.py 의 is_hidden 과 같은 규칙. 이력 파일에는 남아 있다)
const hiddenAlarm = (a) => a.state === 'cleared' && a.acked_by != null && a.level !== 'info';
const stateText = (a) => {
  if (a.level === 'info') return '-';
  if (a.state === 'active' && a.acked_by != null) return STATE_LABEL.acked + ' ' + a.acked_by + ' ' + clock(a.acked_at);
  return STATE_LABEL[a.state] + (a.state === 'cleared' && a.t_cleared ? ' ' + clock(a.t_cleared) : '');
};
// ACK 대상: info 가 아니고 아직 ACK 안 된 서버 알람(활성·해소), 이 브라우저에서 해소된 브라우저 전용 알람. SIL 대상: ACK 안 된 활성 알람 (소리가 나는 알람)
const ackTargets = () => app.alarms.filter((a) => a.level !== 'info' && (a.local ? a.state !== 'active' && a.state !== 'event' : a.acked_by == null) );
const silTargets = () => app.alarms.filter((a) => a.level !== 'info' && a.state === 'active' && a.acked_by == null && AlarmView.soundOn(app.alarmPolicy, a.code));   // ACK 하면 그 알람의 소리는 멈춘다 (PRD FRA3-5)
app.alarmPolicy = {};   // 서버 카탈로그 정책 {code: {enabled, level, sound, sound_file, repeat}} (snapshot.alarm_policy, WS alarm_policy). 브라우저 알람의 Enable/레벨/소리와 알람별 소리 파일·주기
let sound = null;   // 알람 소리 컨트롤러 (아래 '알람 소리' 절에서 만든다). 알람/ACK/SIL 이 바뀔 때마다 refresh 해서 울림·멈춤을 다시 판단한다
function updateAlarmButtons() {
  const ack = el('alarmAckBtn'), sil = el('alarmSilBtn');
  ack.disabled = ackTargets().length === 0;
  if (silTargets().length === 0) app.silenced = false;   // 소리 낼 알람이 없으면 SIL 도 풀린다
  sil.disabled = silTargets().length === 0;
  sil.setAttribute('aria-pressed', String(app.silenced));
  if (sound) sound.refresh();
}

function setAlarmPolicy(policy) {
  app.alarmPolicy = policy || {};
  updateAlarmButtons();                         // 소리 대상(SIL)이 바뀔 수 있다
  if (!el('alarmView').hidden && !el('avCatalogPane').hidden) loadCatalog();   // 다른 화면에서 고친 내용을 열려 있는 카탈로그에도
}

function renderAlarms() {
  const body = el('alarmBody');
  body.replaceChildren();
  const rows = app.alarms.filter((a) => (app.showInfo || a.level !== 'info') && !hiddenAlarm(a));
  el('alarmEmpty').hidden = rows.length > 0;
  for (const a of rows) {
    const tr = document.createElement('tr');
    tr.className = 'lvl-' + a.level + ' st-' + a.state + (a.state === 'active' && a.acked_by != null ? ' acked' : '');
    tr.dataset.id = a.id; tr.dataset.code = a.code; tr.dataset.state = a.state;
    const state = stateText(a);
    const msg = a.text + (a.count > 1 ? ' ×' + a.count : '');
    for (const [text, cls] of [[clock(a.t_last), 'time'], [a.robot || '-', 'robot'], [msg, 'msg'], [state, 'state']]) {
      const td = document.createElement('td'); td.textContent = text; td.className = cls; tr.appendChild(td);
    }
    body.appendChild(tr);
  }
  const btn = el('alarmInfoBtn');
  btn.textContent = app.showInfo ? 'Hide Info' : 'Show Info';
  btn.setAttribute('aria-pressed', String(app.showInfo));
  updateAlarmButtons();
  updateAlarmBadge();
}

function sortAlarms() {
  app.alarms.sort((a, b) => b.t_last - a.t_last);   // 최근에 일어난 것이 위 (같으면 기존 순서 유지)
  if (app.alarms.length > ALARM_KEEP) app.alarms.length = ALARM_KEEP;
}

function upsertAlarm(a) {
  const i = app.alarms.findIndex((x) => x.id === a.id);
  // 새 서버 알람 행이 생기거나 같은 알람이 또 일어나면(count 증가, info 제외) SIL 을 푼다: 지금 울리는 소리만 끄는 것이고 이후 알람은 다시 울려야 한다
  if (!a.local && a.level !== 'info' && a.state === 'active' && (i < 0 || (a.count || 1) > (app.alarms[i].count || 1))) app.silenced = false;
  if (hiddenAlarm(a)) { if (i >= 0) app.alarms.splice(i, 1); }   // ACK 로 숨겨진 알람은 목록에서 뺀다 (이력 파일에는 남아 있다)
  else if (i >= 0) app.alarms[i] = a; else app.alarms.unshift(a);
  sortAlarms();
  renderAlarms();
  if (a.local) saveLocalAlarms(); else histOnAlarm(a);
}

// 브라우저 전용 알람. level 은 옛 호출처의 ok/warn/bad. extra 로 code/state 를 바꿀 수 있다.
function addAlarm(level, text, extra) {
  const t = Date.now() / 1000;
  const pol = AlarmView.applyPolicy(app.alarmPolicy, extra && extra.code, LOCAL_LEVEL[level] || (ALARM_LEVELS.includes(level) ? level : 'L'));
  if (pol.skip) return;   // 카탈로그에서 꺼진 알람은 만들지 않는다 (manager 가 Alarm 화면에서 끈 것)
  upsertAlarm(Object.assign({ id: 'local-' + (++localAlarmSeq), code: 'LOCAL', level: pol.level, robot: null, text, detail: '',
    state: 'event', t_raised: t, t_last: t, t_cleared: null, count: 1, acked_by: null, acked_at: null, local: true }, extra));
}

// ACK: 서버 알람은 서버가 기록하고(모든 화면에 반영), 이 브라우저에서 해소된 브라우저 전용 알람은 여기서 지운다
async function ackAlarms() {
  const hadLocal = app.alarms.some((a) => a.local && a.level !== 'info' && a.state === 'cleared');
  if (hadLocal) { app.alarms = app.alarms.filter((a) => !(a.local && a.level !== 'info' && a.state === 'cleared')); saveLocalAlarms(); renderAlarms(); }
  if (!app.alarms.some((a) => !a.local && a.level !== 'info' && a.acked_by == null)) return;
  try {
    const res = await fetch('/api/alarms/ack', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: null }) });
    if (!res.ok) addAlarm('bad', 'ACK 요청이 거절되었습니다 (HTTP ' + res.status + ')', { code: 'ACK_REQUEST_FAILED' });   // 성공하면 서버가 보낸 WS alarm 으로 화면이 바뀐다
  } catch (err) {
    addAlarm('bad', 'ACK 요청 오류: ' + err.message, { code: 'ACK_REQUEST_FAILED' });
  }
}

function clearLocalAlarms(code) {
  const t = Date.now() / 1000;
  for (const a of app.alarms) if (a.local && a.code === code && a.state === 'active') { a.state = 'cleared'; a.t_cleared = t; }
  renderAlarms(); saveLocalAlarms();
}

// 알람 목록 높이: 아래 손잡이를 끌어 늘리고 줄인다 (더블클릭: 기본, 방향키: 조금씩). 놓으면 저장하고, 내용이 넘치면 안에서 스크롤한다
const alarmMaxH = () => Math.max(ALARM_H.min, Math.round(window.innerHeight * 0.6));
function loadAlarmH() { try { const v = Number(localStorage.getItem(ALARM_H_KEY)); return Number.isFinite(v) && v >= ALARM_H.min ? v : ALARM_H.def; } catch (e) { return ALARM_H.def; } }
function setAlarmH(px, save) {
  const h = Math.max(ALARM_H.min, Math.min(alarmMaxH(), Math.round(px)));
  el('alarmScroll').style.maxHeight = h + 'px';
  el('alarmGrip').setAttribute('aria-valuenow', String(h));
  app.alarmH = h;
  if (save) { try { localStorage.setItem(ALARM_H_KEY, String(h)); } catch (e) { /* 무시 */ } }
}
(function setupAlarmGrip() {
  const grip = el('alarmGrip');
  let drag = null;
  const done = () => { if (!drag) return; drag = null; grip.classList.remove('active'); document.body.classList.remove('resizing', 'row'); setAlarmH(app.alarmH, true); };
  grip.addEventListener('pointerdown', (e) => {
    if (e.button > 0) return;
    e.preventDefault(); grip.setPointerCapture(e.pointerId);
    drag = { y: e.clientY, h: app.alarmH };
    grip.classList.add('active'); document.body.classList.add('resizing', 'row');
  });
  grip.addEventListener('pointermove', (e) => { if (drag) setAlarmH(drag.h + (e.clientY - drag.y), false); });
  for (const ev of ['pointerup', 'pointercancel', 'lostpointercapture']) grip.addEventListener(ev, done);
  grip.addEventListener('dblclick', () => setAlarmH(ALARM_H.def, true));
  grip.addEventListener('keydown', (e) => {
    const step = e.shiftKey ? 80 : 24;
    if (e.key === 'ArrowDown') setAlarmH(app.alarmH + step, true);
    else if (e.key === 'ArrowUp') setAlarmH(app.alarmH - step, true);
    else if (e.key === 'Home') setAlarmH(ALARM_H.def, true);
    else return;
    e.preventDefault();
  });
  window.addEventListener('resize', () => setAlarmH(app.alarmH, false));   // 창이 작아지면 최대(화면의 60%)에 맞춰 줄인다
  setAlarmH(loadAlarmH(), false);
})();
renderAlarms();   // 새로고침 전의 브라우저 전용 알람을 서버 스냅샷이 오기 전에도 보여 준다

el('alarmAckBtn').onclick = ackAlarms;
el('alarmSilBtn').onclick = () => { app.silenced = !app.silenced; updateAlarmButtons(); };   // 이 브라우저만 (서버 상태는 안 바뀐다). 알람 소리(아래)가 app.silenced 를 읽어 멈춘다
el('alarmInfoBtn').onclick = () => { app.showInfo = !app.showInfo; saveShowInfo(app.showInfo); renderAlarms(); };

// ---------- 알람 소리 (alarmsound.js: 판단은 순수 함수, 여기서는 설정 화면과 저장만) ----------
// 소리 낼 알람 = SIL 대상(ACK 안 된 활성 알람, info 제외). ACK / 해소 / SIL 이면 대상이 사라져 소리가 멈춘다 (FRA3-5).
// 설정은 이 브라우저의 localStorage 에만 둔다 (FRA3-7). 저장소를 못 써도 이번 화면에서는 바꾼 값대로 동작한다.
const safeStorage = () => { try { return window.localStorage; } catch (e) { return null; } };
const soundCfg = { s: AlarmSound.loadSettings(safeStorage()), files: [], filesErr: '' };
const saveSound = () => AlarmSound.saveSettings(safeStorage(), soundCfg.s);

sound = AlarmSound.createController({
  adapter: AlarmSound.createAdapter({ AudioContext: window.AudioContext || window.webkitAudioContext, Audio: window.Audio }),
  getSettings: () => soundCfg.s,
  getTargets: () => silTargets().map((a) => { const p = app.alarmPolicy[a.code] || {}; return { level: a.level, file: p.sound_file || '', interval: p.repeat || null }; }),   // 알람별 사운드 파일·반복 주기는 서버 카탈로그 정책 (Step A5)
  getSilenced: () => app.silenced,
  now: () => Date.now(),
  urlOf: (name) => '/sounds/' + encodeURIComponent(name),
  setTimer: (f, ms) => setInterval(f, ms),
  onUpdate: (info) => {
    el('soundHint').hidden = !(info.blocked && soundCfg.s.enabled);   // 막힌 동안만 안내한다 (소리가 필요해지기 전에 미리 한 번 누르게)
    el('sndStatus').textContent = info.status;
    for (const b of document.querySelectorAll('#sndRows [data-stop]')) b.disabled = info.preview !== b.dataset.stop;
  },
});
// 브라우저는 사용자 입력 전에는 소리를 막는다: 화면 어디든 처음 누르거나 키를 누르면 풀어 준다 (FRA3-8)
for (const ev of ['pointerdown', 'keydown']) document.addEventListener(ev, () => { if (sound.info().blocked) sound.unlock(); }, true);
el('soundHint').onclick = () => sound.unlock();

async function loadSoundFiles() {
  try {
    const r = await fetch('/api/alarms/sounds');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    soundCfg.files = j.files || [];
    soundCfg.filesErr = '';
    soundCfg.maxBytes = j.max_bytes; soundCfg.exts = j.extensions || [];
  } catch (e) {
    soundCfg.files = []; soundCfg.filesErr = '사운드 파일 목록을 받지 못했습니다';
  }
  sound.setFiles(soundCfg.files.map((f) => f.name));
  if (el('soundDlg').open) renderSoundDialog();
}

function renderSoundDialog() {
  const s = soundCfg.s;
  el('sndEnabled').checked = s.enabled;
  el('sndVolume').value = String(Math.round(s.volume * 100));
  el('sndVolumeText').textContent = Math.round(s.volume * 100) + '%';
  el('sndInterval').value = String(s.interval);
  const rows = el('sndRows');
  rows.replaceChildren();
  for (const lv of AlarmSound.SOUND_LEVELS) {
    const cfg = s.levels[lv];
    const tr = document.createElement('tr');
    const cell = (child) => { const td = document.createElement('td'); td.appendChild(child); tr.appendChild(td); return td; };
    const name = document.createElement('b'); name.textContent = lv; cell(name);
    const on = document.createElement('input'); on.type = 'checkbox'; on.checked = cfg.on; on.setAttribute('aria-label', lv + ' 소리 켜짐');
    on.onchange = () => { cfg.on = on.checked; saveSound(); sound.refresh(); };
    cell(on);
    const sel = document.createElement('select'); sel.setAttribute('aria-label', lv + ' 사운드 파일');
    sel.add(new Option('합성 비프음', ''));
    for (const f of soundCfg.files) sel.add(new Option(f.name, f.name));
    if (cfg.file && !soundCfg.files.some((f) => f.name === cfg.file)) sel.add(new Option(cfg.file + ' (파일 없음)', cfg.file));   // 파일이 지워졌어도 선택은 보이고, 소리는 합성음으로 난다
    sel.value = cfg.file;
    sel.onchange = () => {
      cfg.file = sel.value; saveSound(); sound.forgetFailures();
      if (sound.info().preview === lv) sound.startPreview(lv); else sound.refresh();   // 시험 중이면 바꾼 소리로 바로 다시 들려 준다
    };
    cell(sel);
    const test = document.createElement('button'); test.type = 'button'; test.textContent = 'Test'; test.dataset.test = lv;
    test.title = lv + ' 소리를 반복해서 들려 줍니다 (알람은 만들지 않습니다)';
    test.onclick = () => sound.startPreview(lv);
    const stop = document.createElement('button'); stop.type = 'button'; stop.textContent = 'Stop'; stop.dataset.stop = lv; stop.disabled = true;
    stop.onclick = () => sound.stopPreview();
    const td = cell(test); td.appendChild(document.createTextNode(' ')); td.appendChild(stop);
    rows.appendChild(tr);
  }
  const exts = (soundCfg.exts || []).join(' ') || '.mp3 .wav .ogg';
  el('sndFilesNote').textContent = soundCfg.filesErr
    || (soundCfg.files.length ? '사운드 파일 ' + soundCfg.files.length + '개를 찾았습니다. ' : '사운드 파일이 없어 합성 비프음을 씁니다. ')
       + '서버의 config/sounds/ 폴더에 ' + exts + ' 파일(파일당 ' + Math.round((soundCfg.maxBytes || 1048576) / 1048576) + 'MB 이하)을 두세요.';
  sound.refresh();
}

el('sndEnabled').onchange = () => { soundCfg.s.enabled = el('sndEnabled').checked; saveSound(); sound.refresh(); };
el('sndVolume').oninput = () => { soundCfg.s.volume = Number(el('sndVolume').value) / 100; el('sndVolumeText').textContent = el('sndVolume').value + '%'; saveSound(); };
el('sndInterval').onchange = () => {
  const v = Number(el('sndInterval').value);
  soundCfg.s.interval = AlarmSound.normalizeSettings({ interval: Number.isFinite(v) ? v : soundCfg.s.interval }).interval;   // 범위 밖은 1~60 초로 맞춘다 (행의 cfg 참조를 유지하려고 제자리에서 바꾼다)
  el('sndInterval').value = String(soundCfg.s.interval);
  saveSound();
};
el('alarmSoundBtn').onclick = () => { renderSoundDialog(); el('soundDlg').showModal(); loadSoundFiles(); };
el('sndClose').onclick = () => el('soundDlg').close();
el('soundDlg').addEventListener('close', () => sound.stopPreview());   // 닫으면(Esc 포함) 시험 재생은 멈춘다
sound.start();
loadSoundFiles();

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
  const waiting = r.patrol && (r.patrol.state === 'WAITING_ZONE' || r.patrol.state === 'QUEUEING');
  if (waiting) {  // FR4-3: 구역 진입 대기 (v3 는 QUEUEING)
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
  for (const [cmd, label] of [['start', 'Start Patrol'], ['stop', 'Stop Patrol'], ['goto', 'Go to Route']]) {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = label;
    b.addEventListener('click', (e) => { e.stopPropagation(); onCommandClick(id, cmd); });
    btns[cmd] = b; row.appendChild(b);
  }
  // 비상 정지: 권한, online 여부, 연타 잠금과 상관없이 항상 누를 수 있다 (FR4-7). 카드 클릭(경로 대상 선택)은 막는다
  const estopRow = document.createElement('div'); estopRow.className = 'cmd-row estop-row';
  const estopBtn = document.createElement('button'); estopBtn.type = 'button'; estopBtn.className = 'danger';
  estopBtn.textContent = 'Emergency Stop'; estopBtn.dataset.robot = id;
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
    // 진단: offline 일 때 서버가 이 로봇 토픽을 한 번이라도 받았는지 (없으면 브릿지/도메인/발견 문제)
    c.badge.title = r.online ? '' : (r.lastSeen
      ? '마지막 수신: ' + (r.lastKind || '?') + ' ' + new Date(r.lastSeen * 1000).toLocaleTimeString()
      : '서버가 이 로봇의 토픽을 한 번도 받지 못했습니다 (브릿지, ROS_DOMAIN_ID, DDS 발견 확인)');
    c.root.classList.toggle('target', app.routeTarget === id);
    const p = r.patrol, state = p && p.state;
    c.stateBadge.textContent = state || '-';
    c.stateBadge.dataset.level = PatrolView.badgeLevel(state);
    if (state === 'WAITING_ZONE' || state === 'QUEUEING') {  // FR4-3
      const zi = app.zoneInfo, holder = zi && zi.state === 'occupied' && zi.holder !== id ? ' · ' + zi.holder + ' 사용 중' : '';
      const base = state === 'WAITING_ZONE' ? ZoneView.waitingText(cfg, r.lastTask, p) : PatrolView.progressText(cfg, r.lastTask, p);
      c.prog.textContent = base + holder;
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
    const ready = !!app.cfg && app.wsUp && isManager();   // 보기 전용(operator)은 명령 버튼이 꺼진다
    const route = app.routes[id] || [];
    c.btns.start.disabled = !ready || !PatrolView.canStart(state, r.online) || locked(id, 'start');
    c.btns.stop.disabled = !ready || !PatrolView.canStop(state, r.online) || locked(id, 'stop');
    c.btns.goto.disabled = !ready || !PatrolView.canStart(state, r.online) || !route.length || locked(id, 'goto');
    c.btns.goto.textContent = 'Go to Route' + (route.length ? ' (' + route.length + ')' : '');
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
    else if (!res.ok && res.status !== 400 && res.status !== 503) addAlarm('bad', id + ' ' + cmd + ' 실패: ' + (data.error || res.status), { code: 'CMD_REQUEST_FAILED' });  // 400(거절)/503(전송 실패)은 서버가 알람을 만든다
  } catch (err) {
    addAlarm('bad', id + ' ' + cmd + ' 전송 오류: ' + err.message, { code: 'CMD_REQUEST_FAILED' });
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
  if (!cfg.goto_allowed.includes(name)) { addAlarm('warn', id + ' 는 ' + name + ' 로 이동할 수 없습니다', { code: 'ROUTE_INVALID' }); return; }
  const route = app.routes[id] || (app.routes[id] = []);
  if (route.length >= app.cfg.max_goto_points) { addAlarm('warn', '경로는 최대 ' + app.cfg.max_goto_points + '개 지점까지입니다', { code: 'ROUTE_INVALID' }); return; }
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

// ---------- 명령 이력 (데이터만) ----------
// 알람바로 통합해서 화면에는 그리지 않는다. 서버의 command 이벤트는 검증 스크립트(verify_step3_ui.py)가 app.history 로 읽는다.
function trackCommand(entry) {
  const i = app.history.findIndex((e) => e.id === entry.id);
  if (i >= 0) app.history[i] = entry; else app.history.unshift(entry);
  app.history.sort((a, b) => b.id - a.id);
  const max = app.cfg ? app.cfg.history_size : 50;
  if (app.history.length > max) app.history.length = max;
}

// ---------- WebSocket ----------
function robotModel(id, i, data) {
  return { color: PALETTE[i % PALETTE.length], online: !!data.online, patrol: data.patrol, pose: data.pose,
           battery: data.battery, lastSeen: data.last_seen || null, lastKind: data.last_kind || null, disp: null, lastCommand: data.last_command || null, lastTask: data.last_task || null, noRespDismissed: null,
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
  if (msg.alarm_policy) setAlarmPolicy(msg.alarm_policy);
  loadSoundFiles();   // 접속(재접속)마다 파일 목록을 새로 읽는다. 로그인 전에는 401 이라 빈 목록이었을 수 있다
  if (msg.procs) setProcs(msg.procs);
  app.history = (msg.commands || []).slice().reverse();
  app.alarms = (msg.alarms || []).concat(app.alarms.filter((a) => a.local));   // 서버 알람은 접속할 때마다 통째로 바꾼다 (서버를 다시 켜면 id 가 처음부터 다시 매겨진다)
  sortAlarms();
  renderAlarms();
  histPinReset();
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
  if (msg.type === 'command') { trackCommand(msg.entry); return; }
  if (msg.type === 'alarm') { upsertAlarm(msg.alarm); return; }
  if (msg.type === 'alarm_policy') { setAlarmPolicy(msg.policy); return; }
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
    r.online = !!msg.data;   // 온라인/오프라인 알람은 서버가 만든다 (WS alarm)
  } else if (msg.field === 'patrol') {
    const prevState = r.patrol && r.patrol.state;
    r.patrol = msg.data;
    const s = msg.data && msg.data.state;
    if (s !== prevState) r.stateSince = Date.now();
    // "응답 없음" 이 표시된 뒤에 상태가 바뀌면(늦은 응답이거나 다른 작업의 진행) 그 안내는 더 이상 맞지 않는다
    if (r.lastCommand && r.lastCommand.result === 'no_response') r.noRespDismissed = r.lastCommand.id;
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
    if (attempt > 0) { clearLocalAlarms('SERVER_DISCONNECTED'); addAlarm('ok', '서버에 다시 연결됨', { code: 'SERVER_RECONNECTED' }); }
    attempt = 0; app.wsUp = true; setChip('connChip', 'ok', 'Connected');
    // 서버는 연결이 끊기면 이 화면의 LiDAR 구독을 지운다. 켜 둔 토글은 다시 요청한다
    for (const [rid, s] of Object.entries(app.scan)) if (s.on) sendWs({ type: 'scan', robot: rid, on: true });
  };
  ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch (err) { console.error('메시지 처리 오류', err); } };
  ws.onclose = () => {
    driveHalt();  // 연결이 끊기면 서버도 0 속도를 보내지만, 화면에서도 즉시 조작을 멈춘다
    if (app.wsUp || attempt === 0) addAlarm('bad', '서버 연결 끊김', { code: 'SERVER_DISCONNECTED', state: 'active' });
    app.wsUp = false; setChip('connChip', 'bad', 'Disconnected');
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
  if (btn) { const old = btn.textContent; btn.textContent = ok ? 'Copied' : 'Failed'; setTimeout(() => (btn.textContent = old), 900); }
}

function renderClicks() {
  const ul = el('pointsList');
  ul.replaceChildren();
  el('pointsEmpty').style.display = app.clicks.length ? 'none' : 'block';
  app.clicks.forEach((c, i) => {
    const li = document.createElement('li');
    const label = document.createElement('span'); label.textContent = 'C' + (i + 1) + '  ' + fmt(c);
    const box = document.createElement('span');
    const cp = document.createElement('button'); cp.type = 'button'; cp.textContent = 'Copy';
    cp.onclick = () => copyText(fmt(c), cp);
    const del = document.createElement('button'); del.type = 'button'; del.textContent = 'Delete';
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
    // 비상정지 결과 알람(E-STOP failed / partially sent)은 서버가 만든다 (WS alarm). 서버가 처리하지 못한 응답(401, 403 …)만 여기서 알린다
    if (!res.ok && res.status !== 503) addAlarm('HH', who + ' 비상정지 요청이 처리되지 않았습니다 (HTTP ' + res.status + ')', { code: 'ESTOP_REQUEST_FAILED' });
  } catch (err) {
    addAlarm('HH', who + ' 비상정지 전송 오류: ' + err.message, { code: 'ESTOP_REQUEST_FAILED' });
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
  if (!sendWs({ type: 'scan', robot: id, on })) addAlarm('warn', '서버에 연결되면 LiDAR 가 켜집니다', { code: 'SCAN_PENDING' });
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
  if (!isManager()) return '보기 전용 계정은 수동 조작할 수 없습니다';
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
  addAlarm('warn', id + ' 수동 조작 거절: ' + reason, { code: 'DRIVE_DENIED' });
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
// 지도와 사이드바 사이의 분할선을 끌어서 사이드바 너비를 바꾼다 (아래 '분할선'). 값은 브라우저에 저장한다.
// 지도 캔버스는 ResizeObserver 로 따라간다. 사이드바의 각 섹션은 제목을 눌러 접고 펼친다.
// ---------- 로그인 ----------
function renderAuth() {
  const me = app.me, box = el('authBox');
  box.hidden = !(me && me.auth && me.role);
  el('authRole').textContent = me && me.role ? (me.user || me.role) + (me.role === 'manager' ? '\n(명령 가능)' : '\n(보기 전용)') : '';
  app.dirtyCards = true;
  renderProcs();
}

// switching: 이미 로그인한 상태에서 다른 계정으로 바꾸는 중 (취소할 수 있다. 틀려도 지금 계정은 그대로)
function showLogin(message, switching) {
  const dlg = el('loginDlg');
  app.switching = !!switching;
  el('loginCancel').hidden = !switching;
  el('loginTitle').textContent = switching ? '계정 전환 (현재: ' + (app.me && (app.me.user || app.me.role)) + ')' : 'Pinky 관제 로그인';
  el('loginPw').value = '';
  if (switching) el('loginId').value = '';
  el('loginErr').textContent = message || '';
  if (!dlg.open) dlg.showModal();
  setTimeout(() => (el('loginId').value ? el('loginPw') : el('loginId')).focus(), 0);
}

async function checkSession() {
  if (!app.me || !app.me.auth) return;
  try {
    const me = await (await fetch('/api/me')).json();
    if (!me.role) { app.me = me; renderAuth(); showLogin('세션이 만료됐습니다. 다시 로그인하세요'); }
  } catch (e) { /* 서버가 내려가 있으면 다시 연결될 때까지 기다린다 */ }
}

el('loginDlg').addEventListener('cancel', (e) => { if (!app.switching) e.preventDefault(); });  // 로그인 전에는 닫을 수 없다 (계정 전환 중에는 Esc 로 취소)
el('loginCancel').onclick = () => { app.switching = false; el('loginDlg').close(); };
el('switchBtn').onclick = () => { driveHalt(); showLogin('', true); };
el('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = el('loginBtn');
  btn.disabled = true; el('loginErr').textContent = '';
  try {
    const res = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: el('loginId').value.trim(), password: el('loginPw').value }) });
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
    else if (!res.ok && data.alarmed) { /* 서버가 PROC_FAILED 알람으로 이미 올렸다 */ }
    else if (!res.ok) addAlarm('bad', (data.error || '요청 실패 (' + res.status + ')'), { code: 'PROC_REQUEST_FAILED' });
    return res.ok;
  } catch (err) {
    addAlarm('bad', '프로세스 제어 요청 오류: ' + err.message, { code: 'PROC_REQUEST_FAILED' });
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
  const note = !pp.enabled ? '프로세스 제어가 꺼져 있습니다. 로그인이 꺼진 서버(--no-auth)에서는 쓸 수 없습니다.'
    : ctx.role !== 'manager' ? '보기 전용 계정입니다. 상태만 볼 수 있습니다.' : '';
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
        '<span class="proc-actions"><button type="button" data-act="start">Start</button><button type="button" data-act="stop">Stop</button><button type="button" data-act="log">Log</button></span></div>' +
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
    if (open && pp.enabled && ctx.role === 'manager' && procLogN[p.id] !== p.log_n) { procLogN[p.id] = p.log_n; loadProcLog(p.id); }
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
// 지도와 사이드바 두 패널을 작업 영역 안의 24x20 격자에 놓는다. 좌표는 격자 칸 수(정수)라서 창 크기가 바뀌어도 비율이 유지된다.
// 알람바(비상 정지)와 메뉴바는 항상 보여야 해서 배치 대상이 아니다.
const LAYOUT_KEY = 'pinky.layout.v3';   // v3: 하단 명령 이력 패널을 없앴다 (v2 저장값은 읽지 않고 기본 배치로 돌아간다)
const GRID = { cols: 24, rows: 20 };
const MIN_SIZE = { w: 4, h: 3 };
const DEFAULT_RECTS = {
  map: { x: 0, y: 0, w: 18, h: 20 },
  side: { x: 18, y: 0, w: 6, h: 20 },
};
const PANEL_ELS = { map: 'panelMap', side: 'panelSide' };
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
// 지도|사이드바 사이(세로선). 두 패널이 맞닿아 있을 때만 보인다 (자유 배치로 떨어뜨렸다면 숨김).
// 더블클릭은 기본 배치, 방향키는 한 칸의 1/2 씩(Shift 는 2칸). 놓을 때 저장한다.
function splitGeom() {
  const m = lay.rects.map, sd = lay.rects.side, E = 0.01;
  const v = Math.abs(m.x + m.w - sd.x) < E && Math.abs(m.y - sd.y) < E && Math.abs(m.h - sd.h) < E;
  return { v };
}
function moveSplitV(b) {   // b: 새 경계 x (칸)
  const m = lay.rects.map, sd = lay.rects.side, right = sd.x + sd.w;
  b = clamp(b, m.x + MIN_SIZE.w, right - MIN_SIZE.w);
  m.w = b - m.x; sd.x = b; sd.w = right - b;
}
function setupSplits() {
  const ws = el('workspace');
  const defs = [
    { id: 'splitV', cls: 'split-v', key: 'v', label: '지도와 사이드바 경계 (끌어서 너비 조절, 더블클릭: 초기화)', axis: 'x',
      get: () => lay.rects.side.x, set: moveSplitV,
      place: (st) => { const sd = lay.rects.side; st.left = `calc(${sd.x / GRID.cols * 100}% - 5px)`; st.top = `${sd.y / GRID.rows * 100}%`; st.height = `${sd.h / GRID.rows * 100}%`; } },
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
      d.set(DEFAULT_RECTS.side.x);
      applyLayout(); placeSplits(); saveLayout(lay.rects);
    });
    n.addEventListener('keydown', (e) => {
      const step = e.shiftKey ? 2 : 0.5;
      const dec = d.axis === 'x' ? 'ArrowLeft' : 'ArrowUp', inc = d.axis === 'x' ? 'ArrowRight' : 'ArrowDown';
      if (e.key === dec) d.set(d.get() - step); else if (e.key === inc) d.set(d.get() + step);
      else if (e.key === 'Home') d.set(DEFAULT_RECTS.side.x); else return;
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

const lsGet = (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } };   // 저장소를 못 써도 화면은 동작한다 (비공개 창 등)
const lsSet = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* 무시 */ } };
// ---------- 표 열 너비 조절 (Alarm 화면의 History·Catalog 표): 머리글 경계를 끌면 그 열만 넓어지고 표는 가로로 스크롤된다 ----------
const AV_WIDTH_KEY = 'pinky.avColWidths', AH_WIDTH_KEY = 'pinky.ahColWidths';
function makeResizable(table, key) {
  const st = { w: null };   // null 이면 자동 배치, 배열이면 열 번호별 px (고정 배치)
  const heads = () => Array.from(table.querySelectorAll('thead th'));
  function apply() {
    if (!st.w) { table.classList.remove('fixed'); table.style.width = ''; heads().forEach((h) => { h.style.width = ''; }); return; }
    let sum = 0;
    heads().forEach((h, i) => {
      if (h.hidden) return;
      const w = st.w[i] || 70;
      h.style.width = w + 'px'; sum += w;
    });
    table.classList.add('fixed'); table.style.width = sum + 'px';
  }
  const save = () => lsSet(key, JSON.stringify(st.w));
  function freeze() {   // 처음 끌 때 현재 자동 배치 너비를 그대로 고정값으로 옮긴다
    if (st.w) return;
    st.w = heads().map((h) => (h.hidden ? 70 : Math.max(40, Math.round(h.getBoundingClientRect().width))));
    apply();
  }
  try { const v = JSON.parse(lsGet(key) || 'null'); if (Array.isArray(v) && v.length === heads().length && v.every((n) => Number.isFinite(n) && n >= 40 && n <= 2000)) st.w = v; } catch (e) { /* 무시 */ }
  heads().forEach((h, i) => {
    const g = document.createElement('span');
    g.className = 'av-rz'; g.title = '끌어서 열 너비 조절 (더블클릭: 자동)';
    g.addEventListener('pointerdown', (e) => {
      e.preventDefault(); freeze();
      g.classList.add('drag');
      const x0 = e.clientX, w0 = st.w[i];
      const move = (m) => { st.w[i] = Math.max(40, Math.min(2000, Math.round(w0 + m.clientX - x0))); apply(); };
      const done = () => {   // 놓았을 때(창 밖 포함) 리스너를 걷고 저장한다
        g.classList.remove('drag');
        window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', done); window.removeEventListener('pointercancel', done);
        save();
      };
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', done); window.addEventListener('pointercancel', done);
    });
    g.addEventListener('dblclick', () => { st.w = null; apply(); try { localStorage.removeItem(key); } catch (e) { /* 무시 */ } });
    h.appendChild(g);
  });
  apply();
  return { st, apply };
}
const avResize = makeResizable(el('avTable'), AV_WIDTH_KEY);

// ---------- 화면 전환 (메인 / 로그 / Alarm) ----------
function setView(name) {
  if (lay.editing) return;   // 레이아웃 편집 중에는 화면을 바꾸지 않는다 (완료/취소 먼저)
  const log = name === 'log', alarm = name === 'alarm';
  el('workspace').hidden = log || alarm;
  el('logView').hidden = !log;
  el('alarmView').hidden = !alarm;
  document.querySelectorAll('.menu-item[data-view]').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  document.querySelectorAll('[data-only-view]').forEach((n) => { n.hidden = n.dataset.onlyView !== name; });
  if (log) startLogs(); else stopLogs();
  if (alarm) setAlarmTab(lsGet(AV_TAB_KEY) === 'catalog' ? 'catalog' : 'history'); else stopCatalog();
  if (!log && !alarm) resizeCanvas();
  lsSet(VIEW_KEY, name);   // 새로고침해도 보던 화면으로 돌아온다
}
document.querySelectorAll('.menu-item[data-view]').forEach((b) => b.addEventListener('click', () => setView(b.dataset.view)));

// ---------- Alarm 화면: Catalog 탭 (읽기 전용) ----------
// 서버가 GET /api/alarms/catalog 로 주는 표를 거르고 그린다. 발생 횟수가 바뀌므로 화면이 열려 있는 동안 5초마다 다시 받는다.
const catalog = { rows: [], since: null, err: null, timer: null, busy: false, canEdit: false, localErr: '', editing: null };

function catalogFilter() { return { level: el('avLevel').value, origin: el('avOrigin').value, q: el('avSearch').value }; }

function renderCatalog() {
  const body = el('avBody');
  body.replaceChildren();
  const rows = AlarmView.sort(AlarmView.filter(catalog.rows, catalogFilter()));
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.className = 'lvl-' + r.level + (r.enabled ? '' : ' off') + (r.modified ? ' modified' : '');
    tr.dataset.code = r.code; tr.dataset.origin = r.origin;
    tr.title = '근거: ' + r.source + (r.modified ? ' | 기본값에서 바뀜' : '');
    AlarmView.cells(r).forEach((text, i) => {
      const td = document.createElement('td'); td.textContent = text;
      td.className = ['code', 'name', 'cond', 'msg', 'lvl', 'snd', 'en', 'last', 'cnt'][i];
      if (i === 6 && r.protected) td.title = AlarmView.TEXT.protectedTip;
      tr.appendChild(td);
    });
    if (catalog.canEdit) {   // 편집 버튼은 manager 에게만 보인다 (서버가 can_edit 로 알려 준다. 눌러도 서버가 다시 권한을 확인한다)
      const td = document.createElement('td'), b = document.createElement('button');
      td.className = 'edit'; b.type = 'button'; b.textContent = 'Edit'; b.dataset.code = r.code;
      b.onclick = () => openCatalogEdit(r.code);
      td.appendChild(b); tr.appendChild(td);
    }
    body.appendChild(tr);
  }
  el('avEditTh').hidden = !catalog.canEdit;
  avResize.apply();
  el('avLocalErr').hidden = !catalog.localErr;
  el('avLocalErr').textContent = catalog.localErr ? AlarmView.TEXT.localError(catalog.localErr) : '';
  el('avEmpty').hidden = rows.length > 0;
  el('avNote').textContent = catalog.err ? (catalog.err === AlarmView.TEXT.oldServer ? catalog.err : AlarmView.TEXT.loadFailed(catalog.err)) : AlarmView.TEXT.note(rows.length, catalog.rows.length);
  el('avNote').classList.toggle('bad', !!catalog.err);
  el('avFoot').textContent = (catalog.since ? AlarmView.TEXT.since(AlarmView.clock(catalog.since)) + '. ' : '') + AlarmView.TEXT.footer(catalog.canEdit);
}

async function loadCatalog() {
  if (catalog.busy) { catalog.again = true; return; }   // 받는 중에 바뀌었을 수 있다: 끝나면 한 번 더 받는다 (저장 직후의 방송과 겹쳐도 낡은 표가 남지 않게)
  catalog.busy = true;
  try {
    const res = await fetch('/api/alarms/catalog');
    if (res.status === 401) { showLogin('세션이 만료됐습니다. 다시 로그인하세요'); return; }
    if (res.status === 404) throw new Error(AlarmView.TEXT.oldServer);   // 화면(정적 파일)은 새 것인데 서버가 옛 코드로 떠 있는 경우
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const d = await res.json();
    catalog.rows = d.alarms; catalog.since = d.counted_since; catalog.err = null; catalog.canEdit = !!d.can_edit; catalog.localErr = d.local_error || '';
    const sel = el('avLevel');
    if (sel.options.length <= 1) for (const lv of d.levels) sel.add(new Option(lv, lv));
  } catch (err) {
    catalog.err = String(err.message || err);   // 알람바에는 올리지 않는다: 이 화면 안에서만 알린다
  } finally {
    catalog.busy = false;
    renderCatalog();
    if (catalog.again) { catalog.again = false; loadCatalog(); }
  }
}
// ---- 편집 (manager) ----
const avEd = (id) => el('avEd' + id);
function fillFileSelect(current) {
  const sel = avEd('File');
  sel.replaceChildren(new Option(AlarmView.TEXT.defaultFile, ''));
  const names = soundCfg.files.map((f) => f.name);
  if (current && !names.includes(current)) names.push(current);   // 저장된 파일이 지금 폴더에 없어도 보이게 (고르면 서버가 거절한다)
  for (const n of names) sel.add(new Option(n, n));
  sel.value = current || '';
}
function syncEditControls() {
  const r = catalog.editing;
  if (!r) return;
  const info = avEd('Level').value === 'info';
  if (info) avEd('Sound').checked = false;
  const quiet = info || !r.soundable;   // info 와 '한 번 알리고 끝나는' 브라우저 알람은 소리가 없다
  if (!r.soundable) avEd('Sound').checked = false;
  avEd('Sound').disabled = quiet || r.protected;
  const off = quiet || !avEd('Sound').checked;   // 소리를 끈 알람은 파일·반복 주기를 고를 필요가 없다
  avEd('File').disabled = off; avEd('Repeat').disabled = off;
}
async function openCatalogEdit(code) {
  const r = catalog.rows.find((x) => x.code === code);
  if (!r) return;
  catalog.editing = r;
  await loadSoundFiles();
  avEd('Title').textContent = r.code + ' \u2013 ' + r.name;
  avEd('Cond').textContent = r.condition;
  avEd('Enabled').checked = r.enabled; avEd('Enabled').disabled = r.protected;
  const lv = avEd('Level');
  lv.replaceChildren(...['HH', 'H', 'L', 'LL', 'info'].map((l) => new Option(l, l)));
  lv.value = r.level; lv.disabled = r.protected;
  avEd('Msg').value = r.message; avEd('Msg').disabled = r.origin !== 'server';
  avEd('Ph').textContent = r.origin === 'server' ? '자리표시자: ' + r.placeholders.map((n) => '{' + n + '}').join(' ') : '';
  avEd('Sound').checked = r.sound;
  fillFileSelect(r.sound_file);
  avEd('Repeat').value = r.repeat == null ? '' : String(r.repeat); avEd('Repeat').placeholder = AlarmView.TEXT.browserSetting;
  avEd('Note').textContent = [r.protected ? AlarmView.TEXT.protectedNote : '', r.origin === 'browser' ? AlarmView.TEXT.browserNote : '', r.soundable ? '' : AlarmView.TEXT.noSoundNote].filter(Boolean).join(' ');
  avEd('Err').textContent = '';
  avEd('Reset').disabled = !r.modified;
  syncEditControls();
  el('avEditDlg').showModal();
}
function editForm() {
  const rep = AlarmView.parseRepeat(avEd('Repeat').value);
  return { form: { enabled: avEd('Enabled').checked, level: avEd('Level').value, message: avEd('Msg').value, sound: avEd('Sound').checked, sound_file: avEd('File').value, repeat: rep.value },
           error: rep.error || (catalog.editing.origin === 'server' ? AlarmView.validateMessage(avEd('Msg').value, catalog.editing.placeholders) : '') };
}
async function sendCatalog(method, url, body) {
  avEd('Err').textContent = '';
  try {
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401) { el('avEditDlg').close(); showLogin('세션이 만료됐습니다. 다시 로그인하세요'); return; }
    if (!res.ok) { avEd('Err').textContent = data.error || AlarmView.TEXT.failed('HTTP ' + res.status); return; }   // 서버 오류 문구는 한국어다 (FRA5-7)
    el('avEditDlg').close();
    await loadCatalog();
  } catch (err) {
    avEd('Err').textContent = AlarmView.TEXT.failed(String(err.message || err));
  }
}
avEd('Sound').onchange = syncEditControls;
avEd('Level').onchange = () => { if (avEd('Level').value !== 'info') avEd('Sound').checked = true; syncEditControls(); };
avEd('Save').onclick = () => {
  const { form, error } = editForm();
  if (error) { avEd('Err').textContent = error; return; }
  const patch = AlarmView.diffPatch(catalog.editing, form);
  if (!Object.keys(patch).length) { avEd('Err').textContent = AlarmView.TEXT.nothing; return; }
  sendCatalog('PUT', '/api/alarms/catalog/' + encodeURIComponent(catalog.editing.code), patch);
};
avEd('Reset').onclick = () => sendCatalog('POST', '/api/alarms/catalog/' + encodeURIComponent(catalog.editing.code) + '/reset');
avEd('Cancel').onclick = () => el('avEditDlg').close();
el('avEditDlg').addEventListener('close', () => { catalog.editing = null; });

function startCatalog() { loadCatalog(); clearInterval(catalog.timer); catalog.timer = setInterval(loadCatalog, 5000); }
function stopCatalog() { clearInterval(catalog.timer); catalog.timer = null; }
['avLevel', 'avOrigin'].forEach((id) => { el(id).onchange = renderCatalog; });
el('avSearch').oninput = renderCatalog;
el('avRefresh').onclick = loadCatalog;
el('avReset').onclick = () => { el('avLevel').value = 'all'; el('avOrigin').value = 'all'; el('avSearch').value = ''; renderCatalog(); };

// ---------- Alarm 화면: History 탭 (Step A7) ----------
// 저장된 이력(data/alarms/)에서 알람 한 건당 한 행을 서버가 묶어 주는 대로 보여 준다. 필터는 서버가 적용하고, 지금 활성인 알람은 WS 로 받는 메모리 알람을 맨 위에 고정한다.
const AV_TAB_KEY = 'pinky.alarmTab', VIEW_KEY = 'pinky.view';
const hist = { rows: [], next: null, pinned: new Map(), filter: null, summary: null, err: '', busy: false, gen: 0, open: new Set(), details: new Map(), filesOpen: false, codesLoaded: false };

function histFilter() {
  return { from: el('ahFrom').value, to: el('ahTo').value, robot: el('ahRobot').value, levels: Array.from(document.querySelectorAll('input[name="ahLevel"]:checked')).map((i) => i.value),
    state: el('ahState').value, code: el('ahCode').value, q: el('ahSearch').value };
}
function histSetDefaults() {
  const t = AlarmView.today();
  el('ahFrom').value = t; el('ahTo').value = t; el('ahRobot').value = ''; el('ahState').value = ''; el('ahCode').value = ''; el('ahSearch').value = '';
  document.querySelectorAll('input[name="ahLevel"]').forEach((i) => { i.checked = false; });
}
function histFillRobots(extra) {
  const sel = el('ahRobot'), keep = sel.value;
  const ids = Array.from(new Set(app.order.concat(extra || [], keep ? [keep] : [])));
  sel.replaceChildren(new Option(AlarmView.H.allRobots, ''), ...ids.map((r) => new Option(r, r)));
  sel.value = keep;
}
function setupHistory() {
  const H = AlarmView.H;
  document.querySelectorAll('#avHistoryPane [data-t]').forEach((n) => { n.textContent = H[n.dataset.t]; });
  el('ahSearch').placeholder = H.search;
  el('ahHead').replaceChildren(...H.head.map((t) => { const th = document.createElement('th'); th.textContent = t; return th; }));
  makeResizable(el('ahTable'), AH_WIDTH_KEY);   // 머리글을 만든 뒤에 손잡이를 붙인다
  el('ahState').replaceChildren(new Option(H.allStates, ''), ...Object.entries(H.states).map(([k, v]) => new Option(v, k)));
  histSetDefaults(); histFillRobots();
  el('ahForm').onsubmit = (e) => { e.preventDefault(); loadHistory(true); };
  el('ahReset').onclick = () => { histSetDefaults(); loadHistory(true); };
  el('ahMore').onclick = () => loadHistory(false);
  el('ahExport').onclick = exportHistory;
  el('ahFilesBtn').onclick = () => { hist.filesOpen = !hist.filesOpen; renderHistorySummary(); };
  el('ahBody').addEventListener('click', (e) => {   // 행을 누르면 상세(타임라인)를 펼치고, 다시 누르면 접는다
    const tr = e.target.closest('tr[data-key]');
    if (!tr) return;
    if (hist.open.has(tr.dataset.key)) hist.open.delete(tr.dataset.key); else hist.open.add(tr.dataset.key);
    renderHistory();
  });
}

// 고정 알람 (메모리의 서버 알람). 해소되어도 이번 조회 동안은 그 자리에서 해소로 바뀌어 보인다 (다시 조회하면 이력으로 내려간다)
function histPinReset() {
  hist.pinned.clear();
  for (const a of app.alarms) if (AlarmView.pinnable(a)) hist.pinned.set(AlarmView.keyOf(a), a);
  if (histVisible()) renderHistory();
}
function histOnAlarm(a) {   // upsertAlarm 이 서버 알람마다 부른다
  const k = AlarmView.keyOf(a);
  if (AlarmView.pinnable(a) || hist.pinned.has(k)) { hist.pinned.set(k, a); hist.details.delete(k); }
  if (histVisible()) renderHistory();
}
const histVisible = () => !el('alarmView').hidden && !el('avHistoryPane').hidden;

function histRows() {
  const f = hist.filter || histFilter();
  const pinned = Array.from(hist.pinned.values()).map(AlarmView.liveRow).filter((r) => AlarmView.matchRow(r, f));
  return AlarmView.mergeRows(pinned, hist.rows);
}

function renderHistorySummary() {
  const s = hist.summary, H = AlarmView.H;
  el('ahOff').hidden = !(s && s.enabled === false);
  el('ahMain').hidden = !!(s && s.enabled === false);
  el('ahSummary').textContent = s && s.enabled ? H.summary(s) : '';
  el('ahFilesBtn').hidden = !(s && s.enabled);
  el('ahFilesBtn').textContent = hist.filesOpen ? H.hideFiles : H.showFiles;
  el('ahFilesBtn').setAttribute('aria-expanded', String(hist.filesOpen));
  const box = el('ahFiles');
  box.hidden = !(hist.filesOpen && s && s.enabled);   // 읽기 전용 목록: 삭제는 GUI 에 두지 않는다
  box.replaceChildren(...((s && s.files) || []).map((f) => { const d = document.createElement('div'); d.textContent = H.fileLine(f); return d; }));
}

function renderHistory() {
  const H = AlarmView.H, { pinned, rows } = histRows();
  const body = el('ahBody');
  body.replaceChildren();
  const all = pinned.map((r) => [r, true]).concat(rows.map((r) => [r, false]));
  for (const [r, pin] of all) {
    const key = AlarmView.keyOf(r);
    const tr = document.createElement('tr');
    tr.className = 'lvl-' + r.level + ' st-' + r.state + (pin ? ' pin' : '') + (hist.open.has(key) ? ' open' : '');
    tr.dataset.key = key; tr.dataset.id = r.id; tr.dataset.state = r.state; tr.dataset.code = r.code;
    if (pin) tr.title = H.pinTip; else if (r.state === 'uncleared') tr.title = H.unclearedTip;
    AlarmView.historyCells(r).forEach((text, i) => {
      const td = document.createElement('td'); td.textContent = text;
      td.className = ['time', 'robot', 'lvl', 'msg', 'state', 'cnt', 'acked', 'clr', 'dur'][i];
      tr.appendChild(td);
    });
    body.appendChild(tr);
    if (hist.open.has(key)) body.appendChild(detailRow(r, key));
  }
  const n = all.length;
  el('ahEmpty').hidden = n > 0 || hist.busy || !!hist.err;
  el('ahMore').hidden = !hist.next;
  el('ahMore').disabled = hist.busy;
  const note = el('ahNote');
  note.textContent = hist.err ? H.failed(hist.err) : (hist.busy && !n ? H.loading : H.note(n, hist.summary && hist.summary.enabled ? hist.summary.total_alarms : null));
  note.classList.toggle('bad', !!hist.err);
}

// 상세 행: 타임라인(이벤트 줄 전부), 전체 문구, detail 원문, 코드, 파일. 서버 이력 파일의 줄을 그대로 보여 준다
function detailRow(r, key) {
  const H = AlarmView.H, ver = r.count + '|' + r.state + '|' + r.acked_by;
  const tr = document.createElement('tr'); tr.className = 'ah-detail'; tr.dataset.for = key;
  const td = document.createElement('td'); td.colSpan = H.head.length; tr.appendChild(td);
  const box = document.createElement('div'); box.className = 'ah-detail-box'; td.appendChild(box);
  const cached = hist.details.get(key);
  if (cached && cached.ver === ver) { fillDetail(box, r, cached.data); return tr; }
  box.textContent = H.loading;
  fetch('/api/alarms/history/' + encodeURIComponent(r.id) + '/events?t_raised=' + encodeURIComponent(String(r.t_raised)))
    .then(async (res) => { const d = await res.json().catch(() => ({})); if (!res.ok) throw new Error(d.error || 'HTTP ' + res.status); return d; })
    .then((d) => { hist.details.set(key, { ver, data: d }); if (box.isConnected) fillDetail(box, r, d); })
    .catch((e) => { if (box.isConnected) box.textContent = H.detailFailed(String(e.message || e)); });
  return tr;
}
function fillDetail(box, r, data) {
  const H = AlarmView.H;
  box.replaceChildren();
  const add = (cls, label, text) => {
    const row = document.createElement('div'); row.className = cls;
    const l = document.createElement('b'); l.textContent = label; row.appendChild(l);
    row.appendChild(document.createTextNode(' ' + text));
    box.appendChild(row);
  };
  const tl = document.createElement('div'); tl.className = 'ah-tl'; tl.appendChild(Object.assign(document.createElement('b'), { textContent: H.timeline }));
  const ul = document.createElement('ul');
  for (const e of data.events) {
    const li = document.createElement('li'); li.dataset.event = e.event; li.textContent = AlarmView.timelineLine(e) + (e.event === 'raised' || e.event === 'repeated' ? '  ' + e.text : '');
    ul.appendChild(li);
  }
  tl.appendChild(ul); box.appendChild(tl);
  add('ah-msg', H.head[3], r.text);
  const last = data.events[data.events.length - 1];
  const dt = document.createElement('div'); dt.className = 'ah-dt';
  dt.appendChild(Object.assign(document.createElement('b'), { textContent: H.detail })); dt.appendChild(document.createElement('pre')).textContent = (last && last.detail) || AlarmView.TEXT.never;   // 긴 사유는 줄바꿈
  box.appendChild(dt);
  add('ah-code', H.code2, r.code);
  add('ah-file', H.file, Array.from(new Set(data.events.map((e) => e.file))).join(', '));
}

async function loadHistorySummary() {
  try {
    const res = await fetch('/api/alarms/history/summary');
    if (res.status === 401) return;
    hist.summary = await res.json();
  } catch (e) { /* 요약을 못 받아도 목록은 본다 */ }
  renderHistorySummary();
}
async function loadHistoryCodes() {   // 코드 입력의 자동 완성 목록 (서버 알람 코드)
  if (hist.codesLoaded) return;
  try {
    const res = await fetch('/api/alarms/catalog');
    if (!res.ok) return;
    const d = await res.json();
    el('ahCodes').replaceChildren(...d.alarms.filter((a) => a.origin === 'server').map((a) => new Option(a.code, a.code)));
    hist.codesLoaded = true;
  } catch (e) { /* 자동 완성만 없다 */ }
}

async function loadHistory(reset) {
  if (!reset && (hist.busy || !hist.next)) return;
  if (reset) { hist.gen++; hist.rows = []; hist.next = null; hist.filter = histFilter(); hist.details.clear(); loadHistorySummary(); }
  const gen = hist.gen;
  hist.busy = true; hist.err = '';
  renderHistory();
  try {
    const res = await fetch('/api/alarms/history?' + AlarmView.historyQuery(hist.filter, { view: 'alarms', limit: 200, before: reset ? null : hist.next }));
    const data = await res.json().catch(() => ({}));
    if (gen !== hist.gen) return;   // 그사이 조건이 바뀌었다: 낡은 응답은 버린다
    if (res.status === 401) { showLogin('세션이 만료됐습니다. 다시 로그인하세요'); return; }
    if (res.status === 404 && hist.summary && hist.summary.enabled === false) return;
    if (!res.ok) throw new Error(data.error || 'HTTP ' + res.status);
    hist.rows = hist.rows.concat(data.alarms); hist.next = data.next;
    histFillRobots(data.alarms.map((a) => a.robot).filter(Boolean));
  } catch (err) {
    if (gen === hist.gen) hist.err = String(err.message || err);
  } finally {
    if (gen === hist.gen) { hist.busy = false; renderHistory(); }
  }
}

// CSV: 지금 적용된 조건 그대로. 상한을 넘으면 서버가 잘라서 주고 헤더로 알린다
async function exportHistory() {
  const H = AlarmView.H, note = el('ahNote');
  try {
    const f = hist.filter || histFilter();
    const res = await fetch('/api/alarms/history/export?' + AlarmView.historyQuery(f));
    if (res.status === 401) { showLogin('세션이 만료됐습니다. 다시 로그인하세요'); return; }
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.error || 'HTTP ' + res.status); }
    const blob = await res.blob();
    const name = ((res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/) || [])[1] || 'alarms.csv';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    const rows = Number(res.headers.get('X-Export-Rows') || 0);
    note.textContent = res.headers.get('X-Export-Truncated') === '1' ? H.exportCap(rows) : H.exported(rows);
    note.classList.remove('bad'); note.dataset.export = res.headers.get('X-Export-Truncated') === '1' ? 'cut' : 'ok';
  } catch (err) {
    note.textContent = H.failed(String(err.message || err)); note.classList.add('bad');
  }
}

function openHistory() {
  histFillRobots();
  histPinReset();
  loadHistoryCodes();
  loadHistory(true);
}

// 탭 전환 (History / Catalog): 마지막으로 본 탭을 기억한다. Catalog 는 열려 있는 동안만 5초마다 다시 받는다
function setAlarmTab(name) {
  const cat = name === 'catalog';
  el('avHistoryPane').hidden = cat; el('avCatalogPane').hidden = !cat;
  el('avTabHistory').setAttribute('aria-selected', String(!cat)); el('avTabCatalog').setAttribute('aria-selected', String(cat));
  lsSet(AV_TAB_KEY, cat ? 'catalog' : 'history');
  if (cat) startCatalog(); else { stopCatalog(); openHistory(); }
}
el('avTabHistory').onclick = () => setAlarmTab('history');
el('avTabCatalog').onclick = () => setAlarmTab('catalog');
setupHistory();

// 메뉴의 Alarm 버튼 배지: 지금 활성인 서버 알람 수. HH/H 가 있으면 빨강, 그 밖에는 주황. 활성이 없으면 숨긴다 (다른 화면에서도 알람을 놓치지 않게)
function updateAlarmBadge() {
  const act = app.alarms.filter((a) => !a.local && a.state === 'active'), b = el('alarmBadge');
  b.hidden = act.length === 0;
  b.textContent = String(act.length);
  b.classList.toggle('hot', act.some((a) => a.level === 'HH' || a.level === 'H'));
}

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
    if (res.status === 403) { el('logNote').textContent = '로그는 manager 로 로그인해야 볼 수 있습니다.'; return; }
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
  el('logPause').textContent = logs.paused ? 'Resume' : 'Pause';
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
  .catch((err) => addAlarm('LL', String(err.message || err), { code: 'LOAD_FAILED' }));
fetch('/api/zone/config').then((r) => { if (!r.ok) throw new Error('구역 설정을 받지 못했습니다 (' + r.status + ')'); return r.json(); })
  .then((z) => { app.zoneCfg = z; el('zoneChip').title = z.confirmed ? '' : '구역 영역은 초안입니다 (config/zone.yaml 의 confirmed: false)'; })
  .catch((err) => addAlarm('LL', String(err.message || err), { code: 'LOAD_FAILED' }));
// 경로 선을 켰을 때만 경로 추정(/api/plans)을 받는다 (서버는 첫 요청에서 계산해 캐시한다)
function ensurePlans() {
  if (app.plans || app.plansLoading) return;
  app.plansLoading = true;
  fetch('/api/plans').then((r) => { if (!r.ok) throw new Error('경로 추정을 받지 못해 경로 선을 직선으로 그립니다 (' + r.status + ')'); return r.json(); })
    .then((d) => { app.plans = d.plans; })
    .catch((err) => addAlarm('LL', String(err.message || err), { code: 'LOAD_FAILED' }))
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
  .catch((err) => addAlarm('LL', String(err.message || err), { code: 'LOAD_FAILED' }));
loadMap().catch((err) => { el('stageNote').textContent = String(err.message || err); addAlarm('LL', String(err.message || err), { code: 'LOAD_FAILED' }); });
const saved = lsGet(VIEW_KEY);   // 새로고침 전에 보던 화면으로 (로그인 뒤에 불러오므로 API 가 401 이 아니다)
if (saved === 'log' || saved === 'alarm') setView(saved);
}
setInterval(() => { app.dirtyCards = true; renderZoneChip(); tickProcUptimes(); }, 500); // STARTING 5초 안내처럼 시간이 지나야 바뀌는 표시용
requestAnimationFrame(frame);
// 로그인 상태를 먼저 확인한다: 인증이 켜져 있는데 로그인하지 않았다면 로그인 후에 화면을 불러온다 (서버 API 가 전부 401 이라서)
fetch('/api/me').then((r) => r.json()).then((me) => {
  app.me = me; renderAuth();
  if (me.auth && !me.role) showLogin(); else boot();
}).catch((err) => { addAlarm('LL', '로그인 상태를 확인하지 못했습니다: ' + err.message, { code: 'LOAD_FAILED' }); boot(); });
