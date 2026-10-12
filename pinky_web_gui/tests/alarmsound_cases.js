'use strict';
// frontend/alarmsound.js 의 순수 함수와 컨트롤러 시험. DOM, 실제 Audio, 실제 시계를 쓰지 않는다 (가짜 어댑터와 가짜 시계).
// tests/test_alarmsound_js.py 가 headless Chrome 으로 돌린다 (이 PC 에는 node 가 없다). node 가 있으면 `node tests/alarmsound_cases.js` 로도 돈다.
async function runCases(AS, check) {
  const S = () => AS.defaultSettings();
  const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const idle = { lastAt: null, lastLevel: null };
  // step 을 여러 번 부르는 도우미: 입력을 덧씌워 상태를 이어 간다
  const mk = () => {
    let st = idle;
    return (now, over) => {
      const r = AS.step(st, Object.assign({ now, settings: S(), targets: [], silenced: false, blocked: false, preview: null }, over));
      st = r.state;
      return r;
    };
  };

  // ---- 설정 ----
  check('기본 설정: 켜짐, 4레벨 모두 켜짐, 파일 없음', (() => { const d = S(); return d.enabled && AS.SOUND_LEVELS.every((l) => d.levels[l].on && d.levels[l].file === ''); })());
  check('SOUND_LEVELS 에 info 가 없다 (info 는 무음)', eq(AS.SOUND_LEVELS, ['HH', 'H', 'L', 'LL']));
  for (const bad of [null, undefined, 5, 'x', [], true]) check('깨진 설정(' + JSON.stringify(bad) + ')은 기본값', eq(AS.normalizeSettings(bad), S()));
  const n = AS.normalizeSettings({ enabled: 'yes', volume: 5, interval: 0, levels: { HH: { on: 'no', file: 7 }, H: { on: false, file: 'a.wav' }, X: { on: false } } });
  check('값 범위와 타입을 바로잡는다', n.enabled === true && n.volume === 1 && n.interval === AS.INTERVAL_MIN && n.levels.HH.on === true && n.levels.HH.file === '' && n.levels.H.on === false && n.levels.H.file === 'a.wav' && !('X' in n.levels));
  check('음량 음수는 0, 주기 큰 값은 상한', AS.normalizeSettings({ volume: -1, interval: 999 }).volume === 0 && AS.normalizeSettings({ interval: 999 }).interval === AS.INTERVAL_MAX);
  check('NaN, Infinity 는 기본값', AS.normalizeSettings({ volume: NaN, interval: Infinity }).volume === 0.7 && AS.normalizeSettings({ interval: NaN }).interval === 5);
  check('파일 이름이 너무 길면 버린다', AS.normalizeSettings({ levels: { HH: { file: 'a'.repeat(201) } } }).levels.HH.file === '');

  const mem = (init) => { const m = Object.assign({}, init); return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, m }; };
  const boom = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('quota'); } };
  check('저장소 키는 pinky.alarmsound.v1', AS.STORAGE_KEY === 'pinky.alarmsound.v1');
  const store = mem();
  const mine = S(); mine.volume = 0.2; mine.levels.L.file = 'x.mp3'; mine.levels.LL.on = false;
  check('저장 후 읽으면 같은 값', AS.saveSettings(store, mine) === true && eq(AS.loadSettings(store), mine) && 'pinky.alarmsound.v1' in store.m);
  check('저장소 값이 JSON 이 아니어도 기본값', eq(AS.loadSettings(mem({ 'pinky.alarmsound.v1': '{oops' })), S()));
  check('저장소가 읽기 예외를 내도 기본값', eq(AS.loadSettings(boom), S()));
  check('저장소가 쓰기 예외를 내면 false (예외는 밖으로 안 나온다)', AS.saveSettings(boom, S()) === false);
  check('저장소가 없어도(null) 동작', eq(AS.loadSettings(null), S()) && AS.saveSettings(null, S()) === false);

  // ---- 어떤 레벨을 울릴까 ----
  const s1 = S();
  check('가장 심각한 레벨을 고른다', AS.pickLevel(['LL', 'H', 'L'], s1) === 'H' && AS.pickLevel(['L', 'HH', 'H'], s1) === 'HH');
  check('info 와 모르는 레벨은 무시', AS.pickLevel(['info'], s1) === null && AS.pickLevel(['info', 'LL'], s1) === 'LL' && AS.pickLevel(['zz'], s1) === null);
  check('대상이 없으면 null', AS.pickLevel([], s1) === null && AS.pickLevel(null, s1) === null);
  s1.levels.HH.on = false;
  check('꺼진 레벨은 건너뛰고 다음 레벨', AS.pickLevel(['HH', 'H'], s1) === 'H' && AS.pickLevel(['HH'], s1) === null);

  // ---- 파일 실패 시 합성음 대체 ----
  const sf = S(); sf.levels.HH.file = 'siren.wav';
  check('파일을 안 골랐으면 합성음', AS.chooseSource('H', sf, ['siren.wav'], new Set()).kind === 'beep' && AS.chooseSource('H', sf, [], new Set()).why === 'no file selected');
  check('고른 파일이 목록에 있으면 파일', eq(AS.chooseSource('HH', sf, ['siren.wav'], new Set()), { kind: 'file', name: 'siren.wav' }));
  check('파일이 지워졌으면 합성음 (missing)', (() => { const r = AS.chooseSource('HH', sf, ['other.wav'], new Set()); return r.kind === 'beep' && r.why === 'file missing' && r.file === 'siren.wav'; })());
  check('재생에 실패한 파일은 합성음 (failed)', (() => { const r = AS.chooseSource('HH', sf, ['siren.wav'], new Set(['siren.wav'])); return r.kind === 'beep' && r.why === 'file failed'; })());
  check('상태줄 문구(영문)', AS.describeSource('HH', { kind: 'file', name: 'siren.wav' }) === 'HH: playing siren.wav'
    && AS.describeSource('H', { kind: 'beep', why: 'no file selected' }) === 'H: fallback beep'
    && AS.describeSource('HH', { kind: 'beep', why: 'file missing', file: 'a.wav' }) === 'HH: fallback beep (file missing: a.wav)');

  // ---- 합성음은 레벨마다 다르다 ----
  const sig = (l) => JSON.stringify(AS.beepPattern(l));
  check('HH/H/L/LL 합성음이 모두 다르다', new Set(AS.SOUND_LEVELS.map(sig)).size === 4);
  check('급할수록 높고 많다 (HH > H > L > LL)', (() => { const p = AS.SOUND_LEVELS.map((l) => AS.beepPattern(l)); return p[0].length > p[1].length && p[1].length > p[2].length && p[0][0].f > p[1][0].f && p[1][0].f > p[2][0].f && p[2][0].f > p[3][0].f; })());
  check('info 는 합성음이 없다', AS.beepPattern('info').length === 0 && AS.beepPattern('zz').length === 0);
  check('beepPattern 은 복사본 (바꿔도 원본 유지)', (() => { AS.beepPattern('HH')[0].f = 1; return AS.beepPattern('HH')[0].f === 1400; })());

  // ---- 반복·중단 (step) ----
  let t = mk();
  check('알람이 없으면 아무것도 안 한다', t(0).action === 'none');
  let r = t(1000, { targets: ['H'] });
  check('알람이 생기면 바로 울린다', r.action === 'play' && r.level === 'H');
  check('주기 전에는 다시 안 울린다', t(1000 + 4999, { targets: ['H'] }).action === 'none');
  check('주기(5초)가 되면 다시 울린다', t(1000 + 5000, { targets: ['H'] }).action === 'play');
  check('주기를 바꾸면 따른다 (2초)', (() => { const u = mk(); const s = S(); s.interval = 2; u(0, { targets: ['L'], settings: s }); return u(1999, { targets: ['L'], settings: s }).action === 'none' && u(2000, { targets: ['L'], settings: s }).action === 'play'; })());
  r = t(6000, { targets: ['H'], silenced: true });
  check('SIL 이면 멈춘다', r.action === 'stop' && r.wanted === null);
  check('SIL 중에는 계속 조용하다', t(7000, { targets: ['H'], silenced: true }).action === 'none');
  check('SIL 이 풀리면 주기를 기다리지 않고 바로 울린다', t(7500, { targets: ['H'] }).action === 'play');
  check('ACK/해소로 대상이 사라지면 멈춘다', t(8000, { targets: [] }).action === 'stop');
  check('멈춘 뒤 다시 멈추라고 하지 않는다', t(8500, { targets: [] }).action === 'none');
  check('더 심각한 알람이 생기면 바로 울린다', (() => { const u = mk(); u(0, { targets: ['H'] }); const x = u(100, { targets: ['H', 'HH'] }); return x.action === 'play' && x.level === 'HH'; })());
  check('심각한 알람만 ACK 하면 남은 알람 소리로 바로 바뀐다', (() => { const u = mk(); u(0, { targets: ['H', 'HH'] }); const x = u(100, { targets: ['H'] }); return x.action === 'play' && x.level === 'H'; })());
  check('전체 끔이면 멈춘다', (() => { const u = mk(); u(0, { targets: ['HH'] }); const s = S(); s.enabled = false; return u(100, { targets: ['HH'], settings: s }).action === 'stop'; })());
  check('레벨을 끄면 그 레벨은 안 울린다', (() => { const s = S(); s.levels.L.on = false; return mk()(0, { targets: ['L'], settings: s }).action === 'none'; })());
  check('info 만 있으면 안 울린다 (무음)', mk()(0, { targets: ['info'] }).action === 'none');
  check('자동재생 차단 중에는 울리지 않고 울릴 레벨을 알린다', (() => { const x = mk()(0, { targets: ['HH'], blocked: true }); return x.action === 'none' && x.wanted === 'HH'; })());
  check('차단이 풀리면 바로 울린다', (() => { const u = mk(); u(0, { targets: ['HH'], blocked: true }); return u(300, { targets: ['HH'] }).action === 'play'; })());
  check('차단 중 알람이 사라지면 멈춤 명령도 없다', (() => { const u = mk(); u(0, { targets: ['HH'], blocked: true }); return u(1, { targets: [] }).action === 'none'; })());

  // ---- 시험 재생 (알람 없이, 반복 주기 적용) ----
  check('시험 재생은 알람이 없어도 울린다', (() => { const x = mk()(0, { preview: { level: 'HH', since: 0 } }); return x.action === 'play' && x.level === 'HH'; })());
  check('시험 재생은 전체 끔·레벨 끔·SIL 이어도 들려 준다', (() => { const s = S(); s.enabled = false; s.levels.L.on = false; const x = mk()(0, { settings: s, silenced: true, preview: { level: 'L', since: 0 } }); return x.action === 'play' && x.level === 'L'; })());
  check('시험 재생도 반복 주기를 따른다', (() => { const u = mk(); const pv = { level: 'H', since: 0 }; u(0, { preview: pv }); return u(4999, { preview: pv }).action === 'none' && u(5000, { preview: pv }).action === 'play'; })());
  check('시험 재생은 실제 알람보다 우선', (() => { const x = mk()(0, { targets: ['HH'], preview: { level: 'LL', since: 0 } }); return x.level === 'LL'; })());
  check('시험 재생을 방치하면 30초 뒤 끝난다', (() => { const u = mk(); const pv = { level: 'H', since: 0 }; u(0, { preview: pv }); const x = u(AS.PREVIEW_MAX_MS + 1, { preview: pv }); return x.endPreview === true && x.action === 'stop'; })());
  check('시험이 끝나면 실제 알람 소리로 돌아간다', (() => { const u = mk(); const pv = { level: 'LL', since: 0 }; u(0, { targets: ['HH'], preview: pv }); const x = u(AS.PREVIEW_MAX_MS + 1, { targets: ['HH'], preview: pv }); return x.endPreview && x.action === 'play' && x.level === 'HH'; })());
  check('시험 재생을 멈추면(preview 없음) 소리도 멈춘다', (() => { const u = mk(); u(0, { preview: { level: 'H', since: 0 } }); return u(100, {}).action === 'stop'; })());

  // ---- 컨트롤러 (가짜 어댑터) ----
  const flush = () => new Promise((res) => setTimeout(res, 0));
  function rig(over) {
    const calls = [];
    const o = Object.assign({ blocked: false, fileResult: 'ok' }, over);
    const adapter = {
      unlock() { o.blocked = false; calls.push(['unlock']); },
      isBlocked: () => o.blocked,
      playBeep(level, vol) { calls.push(['beep', level, vol]); return true; },
      playFile(url, vol) { calls.push(['file', url, vol]); return Promise.resolve(typeof o.fileResult === 'function' ? o.fileResult(url) : o.fileResult); },
      stop() { calls.push(['stop']); },
    };
    const env = { targets: [], silenced: false, now: 0, settings: S(), updates: [] };
    const c = AS.createController({ adapter, getSettings: () => env.settings, getTargets: () => env.targets, getSilenced: () => env.silenced, now: () => env.now,
      urlOf: (n) => '/sounds/' + n, setTimer: () => 1, onUpdate: (i) => env.updates.push(i) });
    return { c, calls, env, o, kinds: () => calls.map((x) => x[0]) };
  }
  let g = rig();
  g.env.targets = ['H']; g.c.refresh();
  check('컨트롤러: 알람이 생기면 합성음 (파일 미선택)', eq(g.calls[0], ['beep', 'H', 0.7]));
  g.env.now = 1000; g.c.refresh();
  check('컨트롤러: 주기 전에는 다시 안 울린다', g.calls.length === 1);
  g.env.now = 5000; g.c.refresh();
  check('컨트롤러: 주기가 되면 다시 울린다', g.kinds().filter((k) => k === 'beep').length === 2);
  g.env.targets = []; g.c.refresh();
  check('컨트롤러: 대상이 없어지면 어댑터를 멈춘다', g.calls[g.calls.length - 1][0] === 'stop');

  g = rig();
  g.env.settings.levels.HH.file = 'siren.wav'; g.c.setFiles(['siren.wav']);
  g.env.targets = ['HH']; g.c.refresh(); await flush();
  check('컨트롤러: 파일을 고르면 파일 URL 과 음량으로 재생', eq(g.calls.find((x) => x[0] === 'file'), ['file', '/sounds/siren.wav', 0.7]) && !g.kinds().includes('beep'));
  check('컨트롤러: 상태줄에 파일 이름', g.c.info().status === 'HH: playing siren.wav');

  g = rig({ fileResult: 'failed' });
  g.env.settings.levels.HH.file = 'siren.wav'; g.c.setFiles(['siren.wav']);
  g.env.targets = ['HH']; g.c.refresh(); await flush();
  check('컨트롤러: 파일 재생이 실패하면 같은 발생에서 합성음으로 대체', g.kinds().join() === 'file,beep' && g.c.info().status === 'HH: fallback beep (file failed: siren.wav)');
  g.env.now = 5000; g.c.refresh(); await flush();
  check('컨트롤러: 실패한 파일은 다음 반복부터 다시 시도하지 않는다', g.kinds().join() === 'file,beep,beep');
  g.c.setFiles(['siren.wav']); g.env.now = 10000; g.c.refresh(); await flush();
  check('컨트롤러: 파일 목록을 다시 읽으면 다시 시도한다', g.kinds().filter((k) => k === 'file').length === 2);

  g = rig();
  g.env.settings.levels.HH.file = 'gone.wav'; g.c.setFiles(['other.wav']);
  g.env.targets = ['HH']; g.c.refresh(); await flush();
  check('컨트롤러: 파일이 지워졌으면 파일을 시도하지 않고 합성음', g.kinds().join() === 'beep' && g.c.info().status === 'HH: fallback beep (file missing: gone.wav)');

  g = rig({ fileResult: 'blocked' });
  g.env.settings.levels.H.file = 'a.wav'; g.c.setFiles(['a.wav']);
  g.env.targets = ['H']; g.c.refresh(); await flush();
  check('컨트롤러: 파일이 자동재생 차단되면 합성음으로 넘기지 않고 사용자 클릭을 기다린다', !g.kinds().includes('beep') && g.c.info().playing === null);
  g.o.fileResult = 'ok'; g.c.unlock(); await flush();
  check('컨트롤러: 클릭(unlock) 뒤 바로 다시 재생', g.kinds().filter((k) => k === 'file').length === 2 && g.kinds().includes('unlock'));

  g = rig({ blocked: true });
  g.env.targets = ['HH']; g.c.refresh();
  check('컨트롤러: 차단 중에는 소리 없이 blocked 와 wanted 를 알린다', g.kinds().length === 0 && g.c.info().blocked === true && g.c.info().wanted === 'HH');
  g.c.unlock();
  check('컨트롤러: unlock 하면 바로 울린다', g.kinds().join() === 'unlock,beep' && g.c.info().blocked === false);

  g = rig({ fileResult: () => 'ok' });
  g.env.settings.levels.H.file = 'a.wav'; g.c.setFiles(['a.wav']);
  const pending = []; // 재생 결과가 늦게 오는 상황
  g.o.fileResult = () => new Promise((res) => pending.push(res));
  g.env.targets = ['H']; g.c.refresh();
  g.env.targets = []; g.c.refresh();
  pending[0]('failed'); await flush();
  check('컨트롤러: 멈춘 뒤 늦게 도착한 파일 실패는 합성음을 울리지 않는다', !g.kinds().includes('beep'));

  g = rig();
  g.c.startPreview('HH');
  check('컨트롤러: 시험 재생은 알람 없이 울리고 unlock 한다', g.kinds().join() === 'unlock,beep' && g.c.info().preview === 'HH');
  g.env.now = 5000; g.c.refresh();
  check('컨트롤러: 시험 재생은 반복 주기를 따른다', g.kinds().filter((k) => k === 'beep').length === 2);
  g.c.stopPreview();
  check('컨트롤러: 정지하면 어댑터를 멈추고 시험 상태를 지운다', g.calls[g.calls.length - 1][0] === 'stop' && g.c.info().preview === null);
  g.c.startPreview('info');
  check('컨트롤러: info 는 시험 재생 대상이 아니다', g.c.info().preview === null);

  // ---- 어댑터 (가짜 AudioContext / Audio) ----
  function fakeEnv(o) {
    const log = [];
    class Ctx {
      constructor() { this.state = o.ctxState; this.currentTime = 10; this.destination = {}; }
      resume() { log.push('resume'); this.state = 'running'; return Promise.resolve(); }
      createOscillator() { const n = { frequency: {}, connect() {}, disconnect() { log.push('disc'); }, start() { log.push('osc.start'); }, stop() { log.push('osc.stop'); } }; return n; }
      createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {} }, connect() {}, disconnect() {} }; }
    }
    class Aud {
      constructor() { this.volume = 1; this.currentTime = 0; }
      pause() { log.push('pause'); }
      play() { log.push('play ' + this.src + ' ' + this.volume); return o.playError ? Promise.reject(Object.assign(new Error('x'), { name: o.playError })) : Promise.resolve(); }
    }
    return { log, env: { AudioContext: o.noCtx ? undefined : Ctx, Audio: Aud } };
  }
  let f = fakeEnv({ ctxState: 'suspended' }); let ad = AS.createAdapter(f.env);
  check('어댑터: 첫 입력 전(suspended)에는 차단 상태', ad.isBlocked() === true);
  check('어댑터: 차단 중에는 합성음을 내지 않는다', ad.playBeep('HH', 0.5) === false && !f.log.includes('osc.start'));
  ad.unlock(); await flush();
  check('어댑터: unlock 하면 resume 해서 차단이 풀린다', f.log.includes('resume') && ad.isBlocked() === false);
  check('어댑터: HH 합성음은 패턴의 비프 수만큼 발진기를 켠다', ad.playBeep('HH', 0.5) === true && f.log.filter((x) => x === 'osc.start').length === AS.beepPattern('HH').length);
  await ad.playFile('/sounds/a.wav', 1);
  ad.stop();
  check('어댑터: stop 은 발진기를 멈추고 파일을 일시정지', f.log.includes('osc.stop') && f.log.includes('pause'));
  check('어댑터: AudioContext 가 없는 환경은 차단으로 보지 않는다 (풀 방법이 없다)', AS.createAdapter(fakeEnv({ noCtx: true }).env).isBlocked() === false);
  f = fakeEnv({ ctxState: 'running' }); ad = AS.createAdapter(f.env);
  check('어댑터: 파일 재생 성공 시 ok, URL 과 음량 전달', await ad.playFile('/sounds/a.wav', 0.4) === 'ok' && f.log.includes('play /sounds/a.wav 0.4'));
  f = fakeEnv({ ctxState: 'running', playError: 'NotAllowedError' }); ad = AS.createAdapter(f.env);
  check('어댑터: NotAllowedError 는 blocked', await ad.playFile('/sounds/a.wav', 1) === 'blocked' && ad.isBlocked() === true);
  ad.unlock();
  check('어댑터: unlock 하면 파일 차단 표시도 풀린다', ad.isBlocked() === false);
  f = fakeEnv({ ctxState: 'running', playError: 'NotSupportedError' }); ad = AS.createAdapter(f.env);
  check('어댑터: 파일 없음/디코딩 실패는 failed', await ad.playFile('/sounds/none.wav', 1) === 'failed');
  check('어댑터: 음량은 0~1 로 제한', await (async () => { f = fakeEnv({ ctxState: 'running' }); ad = AS.createAdapter(f.env); await ad.playFile('/s/a.wav', 5); return f.log.includes('play /s/a.wav 1'); })());
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = runCases;
  if (require.main === module) {
    let bad = 0;
    runCases(require('../frontend/alarmsound.js'), (n, ok) => { console.log((ok ? 'PASS ' : 'FAIL ') + n); if (!ok) bad++; }).then(() => process.exit(bad ? 1 : 0));
  }
}
