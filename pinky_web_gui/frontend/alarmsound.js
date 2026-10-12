'use strict';
// 알람 소리. 위쪽(순수 함수)은 DOM 과 Audio 에 의존하지 않아서 브라우저와 node/headless Chrome 양쪽에서 같은 코드를 시험한다 (mapmath.js 와 같은 방식).
//   설정 정규화, 어떤 레벨 소리를 낼지, 언제 반복할지(step), 파일 대신 합성음을 쓸지(chooseSource) 가 순수 함수다.
// 아래쪽(createAdapter)은 Web Audio 합성 비프음과 파일 재생만 하는 얇은 어댑터이고, createController 가 둘을 이어 준다.
// PRD: 알람바 개선 Step A3 (FRA3-1~10), 결정 9(레벨 순서), 10(파일은 config/sounds/), 11(레벨별 파일, Step A5 에서 알람별 파일·반복 주기로 확장).
(function (root) {
  const SOUND_LEVELS = ['HH', 'H', 'L', 'LL'];            // info 는 무음이라 없다 (FRA3-1). 심각도 순서 (결정 9)
  const RANK = { HH: 4, H: 3, L: 2, LL: 1 };
  const STORAGE_KEY = 'pinky.alarmsound.v1';
  const INTERVAL_MIN = 1, INTERVAL_MAX = 60;               // 반복 주기(초) 범위
  const PREVIEW_MAX_MS = 30000;                            // 시험 재생을 정지하지 않고 두면 이 시간 뒤 스스로 멈춘다

  // 레벨마다 다르게 들리는 합성 비프음: 급할수록 높고 빠르고 많다. [{f: Hz, d: 길이(초), type: 파형}], 사이 간격은 BEEP_GAP
  const BEEP_GAP = 0.09;
  const BEEPS = {
    HH: [1, 2, 3, 4].map(() => ({ f: 1400, d: 0.12, type: 'square' })),
    H: [1, 2].map(() => ({ f: 1000, d: 0.2, type: 'square' })),
    L: [{ f: 700, d: 0.3, type: 'triangle' }],
    LL: [{ f: 480, d: 0.25, type: 'sine' }],
  };
  const beepPattern = (level) => (BEEPS[level] || []).map((b) => Object.assign({}, b));

  // ---------- 설정 (저장소 접근은 try/catch, 저장소를 못 써도 동작한다: FRA3-7) ----------
  function defaultSettings() {
    const levels = {};
    for (const lv of SOUND_LEVELS) levels[lv] = { on: true, file: '' };
    return { enabled: true, volume: 0.7, interval: 5, levels };
  }

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // 저장된 값이 깨졌거나 옛 형식이어도 항상 쓸 수 있는 설정을 돌려준다
  function normalizeSettings(raw) {
    const d = defaultSettings();
    if (!raw || typeof raw !== 'object') return d;
    const num = (v, dflt, lo, hi) => (typeof v === 'number' && isFinite(v) ? clamp(v, lo, hi) : dflt);
    const out = {
      enabled: typeof raw.enabled === 'boolean' ? raw.enabled : d.enabled,
      volume: num(raw.volume, d.volume, 0, 1),
      interval: num(raw.interval, d.interval, INTERVAL_MIN, INTERVAL_MAX),
      levels: {},
    };
    for (const lv of SOUND_LEVELS) {
      const r = raw.levels && typeof raw.levels === 'object' ? raw.levels[lv] : null;
      out.levels[lv] = {
        on: r && typeof r.on === 'boolean' ? r.on : true,
        file: r && typeof r.file === 'string' && r.file.length <= 200 ? r.file : '',
      };
    }
    return out;
  }

  function loadSettings(storage) {
    try { return normalizeSettings(JSON.parse(storage.getItem(STORAGE_KEY))); } catch (e) { return defaultSettings(); }
  }

  function saveSettings(storage, settings) {
    try { storage.setItem(STORAGE_KEY, JSON.stringify(settings)); return true; } catch (e) { return false; }
  }

  // ---------- 무엇을 울릴까 ----------
  // 소리를 낼 알람(target)은 레벨 문자열(A3) 또는 {level, file, interval}(A5: 알람별 사운드 파일과 반복 주기, 서버 카탈로그 정책)이다
  const normTarget = (t) => (typeof t === 'string' ? { level: t, file: '', interval: null } : { level: t && t.level, file: (t && t.file) || '', interval: (t && t.interval) || null });

  // 가장 심각하고 레벨 설정이 켜진 알람. 같은 레벨이면 앞선 것(알람바에서 가장 최근 것). 없으면 null
  function pickTarget(targets, settings) {
    let best = null;
    for (const raw of targets || []) {
      const t = normTarget(raw), cfg = settings.levels[t.level];
      if (RANK[t.level] && cfg && cfg.on && (best === null || RANK[t.level] > RANK[best.level])) best = t;
    }
    return best;
  }

  // 소리 낼 알람 레벨 목록(levels)에서 가장 심각하고 레벨 설정이 켜진 것. 없으면 null. info 와 모르는 레벨은 무시한다.
  function pickLevel(levels, settings) {
    const t = pickTarget(levels, settings);
    return t ? t.level : null;
  }

  // 소리 원천 (FRA3-2: 소리 없이 넘어가지 않는다). 알람 파일(file) > 레벨 파일 > 합성음. 고른 파일이 목록에 있고 재생에 실패한 적 없으면 파일.
  // 알람 파일을 못 쓰면 레벨 파일로, 그것도 못 쓰면 합성음으로 내려간다.
  function chooseSource(level, settings, files, failed, file) {
    const cfg = settings.levels[level];
    const names = [file || '', cfg ? cfg.file : ''].filter((n, i, a) => n && a.indexOf(n) === i);
    if (!names.length) return { kind: 'beep', why: 'no file selected' };
    let first = null;
    for (const name of names) {
      if (!(files || []).includes(name)) { first = first || { kind: 'beep', why: 'file missing', file: name }; continue; }
      if (failed && failed.has(name)) { first = first || { kind: 'beep', why: 'file failed', file: name }; continue; }
      return { kind: 'file', name };
    }
    return first;
  }

  // 한 번 재생을 한국어 한 줄로 (소리 설정 화면 상태줄). why 는 chooseSource 의 내부 식별자
  const WHY_TEXT = { 'file missing': '파일 없음', 'file failed': '재생 실패' };
  function describeSource(level, src) {
    if (src.kind === 'file') return level + ': ' + src.name + ' 재생 중';
    return level + ': 합성 비프음' + (src.why === 'no file selected' ? '' : ' (' + (WHY_TEXT[src.why] || src.why) + (src.file ? ': ' + src.file : '') + ')');
  }

  // 반복·중단 판단. 시계와 타이머를 모르는 순수 함수라서 now 를 받는다.
  //   state: {lastAt, lastLevel} (마지막으로 울린 시각 ms 와 레벨, 안 울렸으면 null)
  //   input: {now, settings, targets(소리 낼 알람: 레벨 문자열 또는 {level, file, interval}), silenced(SIL), blocked(자동재생 차단), preview({level, since}|null)}
  //   반환: {state, action: 'play' | 'stop' | 'none', level, file(그 알람의 사운드 파일 이름 또는 ''), endPreview, wanted(울려야 하는 레벨 또는 null)}
  // 규칙: 시험 재생 > 전체 끔/SIL > 레벨별 끔. 레벨이 바뀌면 바로 울리고, 같은 레벨은 반복 주기마다 울린다. 울릴 게 없어지면(ACK/해소/SIL) 바로 멈춘다 (FRA3-5).
  function step(state, input) {
    const idle = { lastAt: null, lastLevel: null };
    let level = null, file = '', interval = null, endPreview = false;
    const pv = input.preview;
    if (pv && input.now - pv.since > PREVIEW_MAX_MS) endPreview = true;
    if (pv && !endPreview) level = pv.level;                  // 시험 재생은 켜짐/끔/SIL 과 상관없이 들려 준다 (실제 알람을 만들지 않는다: FRA3-4)
    else if (input.settings.enabled && !input.silenced) {
      const t = pickTarget(input.targets, input.settings);
      if (t) { level = t.level; file = t.file; interval = t.interval; }   // 이기는 알람의 사운드 파일과 반복 주기 (없으면 레벨 파일과 설정 주기)
    }
    if (level === null) return { state: idle, action: state.lastAt === null ? 'none' : 'stop', level: null, file: '', endPreview, wanted: null };
    if (input.blocked) return { state: idle, action: 'none', level: null, file: '', endPreview, wanted: level };   // 차단이 풀리면 바로 울리도록 마지막 시각을 비워 둔다
    const due = state.lastAt === null || level !== state.lastLevel || input.now - state.lastAt >= (interval || input.settings.interval) * 1000;
    if (!due) return { state, action: 'none', level: null, file: '', endPreview, wanted: level };
    return { state: { lastAt: input.now, lastLevel: level }, action: 'play', level, file, endPreview, wanted: level };
  }

  // ---------- 어댑터: Web Audio 합성음과 파일 재생만 한다 ----------
  // env: {AudioContext, Audio} (브라우저 전역). 시험할 때는 가짜 env 를 넘긴다.
  function createAdapter(env) {
    let ctx = null, nodes = [], audio = null, fileBlocked = false;
    const context = () => {
      if (!ctx && env.AudioContext) { try { ctx = new env.AudioContext(); } catch (e) { ctx = null; } }
      return ctx;
    };
    return {
      // 사용자 클릭·키 입력 안에서 불러야 한다. 브라우저가 첫 입력 전에는 소리를 막는다 (FRA3-8)
      unlock() {
        fileBlocked = false;
        const c = context();
        if (c && c.state === 'suspended') { try { c.resume().catch(() => {}); } catch (e) { /* 다음 클릭에 다시 시도 */ } }
      },
      isBlocked() {
        const c = context();
        return fileBlocked || (c ? c.state !== 'running' : false);
      },
      playBeep(level, volume) {
        const c = context();
        if (!c || c.state !== 'running') return false;
        let t = c.currentTime + 0.02;
        for (const b of beepPattern(level)) {
          const osc = c.createOscillator(), gain = c.createGain();
          osc.type = b.type; osc.frequency.value = b.f;
          gain.gain.setValueAtTime(0.0001, t);
          gain.gain.linearRampToValueAtTime(Math.max(0.0001, volume * 0.3), t + 0.01);   // 합성음은 파일보다 크게 들려서 0.3 배
          gain.gain.setValueAtTime(Math.max(0.0001, volume * 0.3), t + b.d - 0.02);
          gain.gain.linearRampToValueAtTime(0.0001, t + b.d);
          osc.connect(gain); gain.connect(c.destination);
          osc.start(t); osc.stop(t + b.d + 0.02);
          nodes.push(osc, gain);
          t += b.d + BEEP_GAP;
        }
        return true;
      },
      // 'ok' | 'blocked'(자동재생 차단) | 'failed'(없음, 디코딩 실패 등)
      async playFile(url, volume) {
        try {
          if (!audio) audio = new env.Audio();
          audio.pause();
          audio.src = url;
          audio.volume = clamp(volume, 0, 1);
          audio.currentTime = 0;
          await audio.play();
          return 'ok';
        } catch (e) {
          if (e && e.name === 'NotAllowedError') { fileBlocked = true; return 'blocked'; }
          return 'failed';
        }
      },
      stop() {
        for (const n of nodes) { try { if (n.stop) n.stop(); n.disconnect(); } catch (e) { /* 이미 끝난 노드 */ } }
        nodes = [];
        if (audio) { try { audio.pause(); } catch (e) { /* 정지 실패는 무시 */ } }
      },
    };
  }

  // ---------- 컨트롤러: step 의 결과를 어댑터로 옮긴다 ----------
  // opts: {adapter, getSettings(), getTargets(), getSilenced(), now(), urlOf(name), onUpdate(info), setTimer, clearTimer}
  function createController(opts) {
    const adapter = opts.adapter;
    let st = { lastAt: null, lastLevel: null };
    let preview = null;            // {level, since}
    let files = [];                // 서버에 있는 파일 이름
    let failed = new Set();        // 재생에 실패한 파일 (선택을 바꾸거나 목록을 다시 읽으면 비운다)
    let status = '', playing = null, playToken = 0, timer = null, last = { blocked: false, wanted: null, playing: null, status: '', preview: null };

    function notify() {
      const info = { blocked: adapter.isBlocked(), wanted: last.wanted, playing, status, preview: preview ? preview.level : null };
      last = info;
      if (opts.onUpdate) opts.onUpdate(info);
    }

    function playNow(level, file) {
      const settings = opts.getSettings();
      const src = chooseSource(level, settings, files, failed, file);
      const token = ++playToken;
      status = describeSource(level, src);
      const beep = () => adapter.playBeep(level, settings.volume);
      if (src.kind === 'beep') { beep(); return; }
      adapter.playFile(opts.urlOf(src.name), settings.volume).then((res) => {
        if (token !== playToken) return;                      // 그 사이 멈췄거나 다시 울렸다
        if (res === 'failed') {
          failed.add(src.name);
          status = describeSource(level, chooseSource(level, settings, files, failed, file));
          beep();                                            // 이번 발생도 소리 없이 넘기지 않는다
        } else if (res === 'blocked') {
          st = { lastAt: null, lastLevel: null };            // 클릭 뒤에 다시 시도한다
        }
        if (res === 'blocked') playing = null;
        notify();
      });
    }

    function refresh() {
      const r = step(st, { now: opts.now(), settings: opts.getSettings(), targets: opts.getTargets(), silenced: opts.getSilenced(),
                           blocked: adapter.isBlocked(), preview });
      st = r.state;
      if (r.endPreview) preview = null;
      if (r.action === 'stop') { playToken++; adapter.stop(); status = ''; }
      if (r.action === 'play') { playNow(r.level, r.file); playing = r.level; }
      if (r.wanted === null || adapter.isBlocked()) playing = null;
      last.wanted = r.wanted;
      notify();
    }

    return {
      refresh,
      start() { if (!timer) timer = opts.setTimer(refresh, 500); refresh(); },
      unlock() { adapter.unlock(); refresh(); },
      setFiles(names) { files = names.slice(); failed = new Set(); refresh(); },
      forgetFailures() { failed = new Set(); },
      // 시험 재생: 이 레벨 소리를 반복 주기대로 계속 낸다. 알람은 만들지 않는다. stopPreview 나 30 초 뒤 멈춘다
      startPreview(level) {
        if (!RANK[level]) return;
        adapter.unlock();
        preview = { level, since: opts.now() };
        st = { lastAt: null, lastLevel: null };
        refresh();
      },
      stopPreview() { preview = null; playToken++; adapter.stop(); playing = null; st = { lastAt: null, lastLevel: null }; status = ''; refresh(); },
      info: () => last,
    };
  }

  const api = { SOUND_LEVELS, STORAGE_KEY, INTERVAL_MIN, INTERVAL_MAX, PREVIEW_MAX_MS, defaultSettings, normalizeSettings, loadSettings, saveSettings,
                pickLevel, pickTarget, chooseSource, describeSource, step, beepPattern, createAdapter, createController };
  root.AlarmSound = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
