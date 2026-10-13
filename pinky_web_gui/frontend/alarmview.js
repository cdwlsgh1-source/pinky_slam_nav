// Alarm 화면의 순수 함수들 (DOM 없음). app.js 가 쓰고 테스트에서도 불러 쓴다.
// Catalog 탭(A4 표시, A5 편집)과 History 탭(A7, 저장된 전체 알람 이력)의 순수 함수.
// 편집 검증은 서버(backend/alarmcatalog.py)와 같은 규칙·같은 한국어 문구다. 화면의 검사는 빨리 알려 주려는 것이고 최종 판단은 서버가 한다.
// 고정 문구는 한국어이고 한 곳(TEXT)에 모은다 (PRD 공통 원칙 7). 알람 이름·조건·메시지는 서버가 주는 값이다.
const AlarmView = (() => {
  const TEXT = {
    levelAll: '전체', yes: '예', no: '아니오', server: '서버', browser: '브라우저 전용', never: '-', protectedTip: '비상정지 알람은 끌 수 없습니다',
    note: (shown, total) => (shown === total ? '알람 ' + total + '개' : '알람 ' + total + '개 중 ' + shown + '개'),
    since: (clock) => '최근 발생·횟수는 이번 서버 실행(' + clock + ' 시작) 기준이며, 브라우저 전용 알람은 세지 않습니다',
    footer: (canEdit) => (canEdit ? '여기서 바꾼 내용은 config/alarms.local.yaml 에 저장되고 모든 화면에 적용됩니다. ' : '보기 전용: manager 만 알람을 바꿀 수 있습니다. ')
      + '이름과 발생 조건은 config/alarms.yaml 에서 고칩니다 (고친 뒤 서버를 다시 켭니다).',
    msgRange: '메시지는 줄바꿈 없이 1~120글자여야 합니다', unbalanced: '메시지의 { } 짝이 맞지 않습니다', plain: '{이름} 형태의 자리표시자만 쓸 수 있습니다',
    unknownPh: (name, allowed) => '알 수 없는 자리표시자 {' + name + '}. 허용: ' + allowed.map((n) => '{' + n + '}').join(', '),
    repeatRange: '반복 주기는 1~60초여야 합니다',
    saved: '저장했습니다', nothing: '바뀐 것이 없습니다', failed: (why) => '저장하지 못했습니다 (' + why + ')',
    protectedNote: '비상정지 알람은 끄거나 레벨을 바꾸거나 소리를 끌 수 없습니다.', browserNote: '이 알람은 브라우저가 만듭니다: 메시지는 바꿀 수 없습니다.',
    noSoundNote: '이 알람은 한 번 알리고 끝나는 안내라 소리가 없습니다.',
    defaultFile: '레벨 기본', browserSetting: '소리 설정 따름', every: (n) => n + '초마다',
    localError: (why) => '저장된 변경을 읽지 못해 기본값을 쓰는 중입니다 (' + why + ')',
    oldServer: '서버가 알람 카탈로그를 지원하지 않습니다. 서버가 이전 버전으로 떠 있으니 GUI 서버를 다시 시작하세요',
    loadFailed: (why) => '카탈로그를 받지 못했습니다 (' + why + ')',
  };
  // History 탭의 고정 문구 (PRD 8-5절). 버튼 이름(Apply, Reset, Load more, Export CSV, Show files, Hide files)만 영문이다. 알람 문구는 서버가 만든 값이다.
  const H = {
    from: '시작', to: '끝', robot: '로봇', level: '레벨', state: '상태', code: '코드', search: '검색', allRobots: '전체', allStates: '전체',
    head: ['발생', '로봇', '레벨', '메시지', '상태', '횟수', '확인', '해소', '지속 시간'],
    states: { active: '활성', acknowledged: '확인됨', cleared: '해소', uncleared: '해소 기록 없음' },
    empty: '알람이 없습니다', off: '이력 저장이 꺼져 있습니다',
    note: (n, total) => '최신 ' + n + '건' + (total == null ? '' : ' (전체 ' + total + '건)'),
    exportCap: (n) => '내보내기는 ' + n + '행까지입니다',
    exported: (n) => n + '행을 내려받았습니다',
    summary: (s) => '저장 폴더 ' + s.dir + ' · 파일 ' + s.total_files + '개 · 알람 ' + s.total_alarms + '건',
    fileLine: (f) => f.name + ' · 알람 ' + f.alarms + '건 · 이벤트 ' + f.records + '줄',
    showFiles: 'Show files', hideFiles: 'Hide files',
    timeline: '타임라인', raised: '발생', repeated: '반복', acked: '확인', cleared: '해소', detail: '상세', file: '파일', code2: '코드',
    loading: '불러오는 중입니다', failed: (why) => '이력을 받지 못했습니다 (' + why + ')', detailFailed: (why) => '상세를 받지 못했습니다 (' + why + ')',
    pinTip: '지금 활성인 알람 (맨 위에 고정)', unclearedTip: '서버가 꺼져 해소 기록이 남지 않았습니다. 지금은 서버 메모리에 없는 알람입니다',
    ongoing: '진행 중',
  };
  const LEVEL_RANK = { HH: 0, H: 1, L: 2, LL: 3, info: 4 };

  // 필터: level('all' 이면 전체), origin('all'|'server'|'browser'), q(ID·이름·조건·메시지에서 부분 일치, 대소문자 무시)
  function match(row, f) {
    if (f.level && f.level !== 'all' && row.level !== f.level) return false;
    if (f.origin && f.origin !== 'all' && row.origin !== f.origin) return false;
    const q = (f.q || '').trim().toLowerCase();
    if (q && ![row.code, row.name, row.condition, row.message].some((t) => String(t).toLowerCase().includes(q))) return false;
    return true;
  }
  function filter(rows, f) { return rows.filter((r) => match(r, f)); }

  // 화면에 보이는 순서: 서버가 준 순서(심각한 것부터)를 유지한다. 같은 레벨 안에서는 서버 알람이 브라우저 전용보다 앞.
  function sort(rows) {
    return rows.map((r, i) => [r, i]).sort((a, b) => (LEVEL_RANK[a[0].level] - LEVEL_RANK[b[0].level]) || (a[0].origin === b[0].origin ? 0 : a[0].origin === 'server' ? -1 : 1) || (a[1] - b[1])).map((x) => x[0]);
  }

  const two = (n) => String(n).padStart(2, '0');
  function stamp(t) {   // YYYY-MM-DD HH:MM:SS (이 브라우저의 시계)
    if (t == null) return TEXT.never;
    const d = new Date(t * 1000);
    return d.getFullYear() + '-' + two(d.getMonth() + 1) + '-' + two(d.getDate()) + ' ' + two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds());
  }
  const clock = (t) => stamp(t).slice(11);

  // 표 한 줄에 쓸 값 (열 순서: ID, Name, Condition, Message, Level, Sound, Enabled, Last raised, Count)
  function cells(r) {
    return [r.code, r.name, r.condition, r.message, r.level, soundText(r), r.enabled ? TEXT.yes : TEXT.no,
      r.origin === 'browser' ? TEXT.never : stamp(r.last_raised), r.origin === 'browser' ? TEXT.never : String(r.count)];
  }

  // ---- 편집 검증 ----
  const REPEAT_MIN = 1, REPEAT_MAX = 60, MESSAGE_MAX = 120;
  // 자리표시자 이름 목록 또는 오류 문구 한 줄 (서버 alarmcatalog._fields_of 와 같은 규칙)
  function placeholders(text) {
    const names = [];
    let i = 0;
    while (i < text.length) {
      const c = text[i];
      if (c === '{') {
        if (text[i + 1] === '{') { i += 2; continue; }
        const j = text.indexOf('}', i + 1);
        const inner = j < 0 ? null : text.slice(i + 1, j);
        if (inner === null || inner.includes('{')) return { error: TEXT.unbalanced };
        if (inner === '' || /[.\[!:]/.test(inner)) return { error: TEXT.plain };
        names.push(inner);
        i = j + 1;
      } else if (c === '}') {
        if (text[i + 1] === '}') { i += 2; continue; }
        return { error: TEXT.unbalanced };
      } else i++;
    }
    return { names };
  }
  // 오류 문구(없으면 ''). 길이는 글자 수(UTF-16 단위가 아니라 코드포인트)로 센다
  function validateMessage(text, allowed) {
    const len = Array.from(String(text)).length;
    if (len < 1 || len > MESSAGE_MAX || !String(text).trim() || /[\u0000-\u001f\u007f]/.test(text)) return TEXT.msgRange;
    const p = placeholders(String(text));
    if (p.error) return p.error;
    const bad = p.names.find((n) => !allowed.includes(n));
    return bad === undefined ? '' : TEXT.unknownPh(bad, allowed);
  }
  // 빈 칸 = null(브라우저 설정 따름). 숫자로 읽을 수 없거나 범위 밖이면 오류 문구. 반환 {value, error}
  function parseRepeat(text) {
    const t = String(text == null ? '' : text).trim();
    if (t === '') return { value: null, error: '' };
    const n = Number(t);
    if (!/^\d+$/.test(t) || n < REPEAT_MIN || n > REPEAT_MAX) return { value: null, error: TEXT.repeatRange };
    return { value: n, error: '' };
  }

  // 표의 소리 칸: 아니오 | 예 | 예 · siren.wav · 3초마다
  function soundText(r) {
    if (!r.sound) return TEXT.no;
    return [TEXT.yes].concat(r.sound_file ? [r.sound_file] : [], r.repeat ? [TEXT.every(r.repeat)] : []).join(' \u00b7 ');
  }

  // 편집 대화상자가 서버로 보낼 변경분: 현재 행과 다른 항목만. form: {enabled, message, level, sound, sound_file, repeat(null|number)}
  function diffPatch(row, form) {
    const patch = {};
    for (const k of ['enabled', 'level', 'sound', 'sound_file', 'repeat']) if (form[k] !== undefined && form[k] !== row[k]) patch[k] = form[k];
    if (row.origin === 'server' && form.message !== undefined && form.message !== row.message) patch.message = form.message;
    return patch;
  }

  // 브라우저 알람에 서버 정책을 적용: 꺼진 알람은 만들지 않고(skip) 레벨은 정책의 레벨. 정책을 아직 못 받았으면 호출부의 값 그대로
  function applyPolicy(policy, code, level) {
    const p = policy && policy[code];
    if (!p) return { skip: false, level };
    return { skip: p.enabled === false, level: p.level || level };
  }
  // 소리 낼 알람인가 (정책이 sound:false 이면 소리 대상이 아니다)
  const soundOn = (policy, code) => !(policy && policy[code] && policy[code].sound === false);

  // ---- History 탭 ----
  const keyOf = (a) => a.id + '@' + a.t_raised;   // 서버를 다시 켜면 id 가 겹치므로 (id, t_raised) 쌍이 알람의 이름이다
  // 서버 메모리의 알람(app.alarms 의 서버 알람)을 이력 행의 상태 이름으로
  const liveState = (a) => (a.state === 'active' ? (a.acked_by != null ? 'acknowledged' : 'active') : 'cleared');
  // 메모리 알람 -> 이력 표의 행 모양 (이력 쪽 값은 서버가 만든다: events 와 file 은 모른다)
  function liveRow(a) {
    return { id: a.id, t_raised: a.t_raised, code: a.code, level: a.level, robot: a.robot, text: a.text, detail: a.detail || '', state: liveState(a), count: a.count || 1,
      acked_by: a.acked_by, acked_at: a.acked_at, t_last: a.t_last, t_cleared: a.t_cleared, duration: a.state === 'cleared' && a.level !== 'info' && a.t_cleared != null ? Math.round((a.t_cleared - a.t_raised) * 10) / 10 : null, live: true };
  }
  // 고정해 보일 알람인가: 서버 알람이고 (브라우저 전용 알람은 서버가 모른다) info 가 아닌 active. info 는 해소 개념이 없는 기록이다
  const pinnable = (a) => !a.local && a.state === 'active';
  // 필터 f: {robot, levels:[], state, code, q}. 기간은 서버가 이력에 건다. 고정 알람에는 기간을 걸지 않는다 (아직 살아 있는 알람은 며칠 전 것이어도 보여야 한다)
  function matchRow(r, f) {
    if (f.robot && r.robot !== f.robot) return false;
    if (f.levels && f.levels.length && !f.levels.includes(r.level)) return false;
    if (f.state && r.state !== f.state) return false;
    if (f.code && r.code !== f.code) return false;
    const q = (f.q || '').trim().toLowerCase();
    if (q && !((r.text || '') + '\n' + (r.detail || '')).toLowerCase().includes(q)) return false;
    return true;
  }
  // 고정 알람 + 이력 -> 표에 보일 행. 같은 알람은 한 번만 (고정 쪽이 실시간이라 우선). 고정 알람은 최신 발생이 위
  function mergeRows(pinned, rows) {
    const seen = new Set(pinned.map(keyOf));
    return { pinned: pinned.slice().sort((a, b) => b.t_raised - a.t_raised), rows: rows.filter((r) => !seen.has(keyOf(r))) };
  }
  // 서버 조회 쿼리 문자열. f: {from, to, robot, levels:[], state, code, q}, extra: {limit, before}
  function historyQuery(f, extra) {
    const p = new URLSearchParams();
    for (const [k, v] of [['from', f.from], ['to', f.to], ['robot', f.robot], ['state', f.state], ['code', (f.code || '').trim().toUpperCase()], ['q', (f.q || '').trim()]]) if (v) p.set(k, v);
    if (f.levels && f.levels.length) p.set('level', f.levels.join(','));
    for (const [k, v] of Object.entries(extra || {})) if (v != null && v !== '') p.set(k, String(v));
    return p.toString();
  }
  const today = (d) => { d = d || new Date(); return d.getFullYear() + '-' + two(d.getMonth() + 1) + '-' + two(d.getDate()); };
  function durationText(sec) {
    if (sec == null) return TEXT.never;
    const s = Math.round(sec);
    if (s < 60) return (Math.round(sec * 10) / 10) + '초';
    const m = Math.floor(s / 60), h = Math.floor(m / 60);
    return h ? h + '시간 ' + two(m % 60) + '분' : m + '분 ' + two(s % 60) + '초';
  }
  // 표 한 줄의 텍스트 (열 순서: H.head). 해소 기록이 없으면 해소·지속 시간은 비워 둔다
  function historyCells(r) {
    return [stamp(r.t_raised), r.robot || TEXT.never, r.level, r.text, H.states[r.state] || r.state, String(r.count),
      r.acked_by ? r.acked_by + ' ' + clock(r.acked_at) : TEXT.never, r.state === 'cleared' ? clock(r.t_cleared) : TEXT.never,
      r.state === 'cleared' ? durationText(r.duration) : (r.state === 'active' || r.state === 'acknowledged' ? H.ongoing : TEXT.never)];
  }
  // 상세 타임라인 한 줄: '10:01:02  반복  (oper)  문구'
  function timelineLine(e) {
    return [stamp(e.t), H[e.event] || e.event, e.acked_by && e.event === 'acked' ? '(' + e.acked_by + ')' : '', e.event === 'acked' ? '' : (e.count > 1 && e.event === 'repeated' ? '×' + e.count : '')].filter(Boolean).join('  ');
  }

  return { TEXT, H, keyOf, liveState, liveRow, pinnable, matchRow, mergeRows, historyQuery, today, durationText, historyCells, timelineLine, match, filter, sort, stamp, clock, cells, validateMessage, parseRepeat, soundText, diffPatch, applyPolicy, soundOn, REPEAT_MIN, REPEAT_MAX, MESSAGE_MAX };
})();
if (typeof module !== 'undefined') module.exports = AlarmView;
