// Alarm 화면의 순수 함수들 (DOM 없음). app.js 가 쓰고 테스트에서도 불러 쓴다.
// 이 단계(A4)는 Catalog 탭만이다. History 탭(A7)이 같은 파일에 순수 함수를 더한다.
// 고정 문구는 영문이고 한 곳(TEXT)에 모은다 (PRD 공통 원칙 4·7). 알람 이름·조건·메시지는 서버가 주는 값이라 한영 혼용이다.
const AlarmView = (() => {
  const TEXT = {
    levelAll: 'All', yes: 'Yes', no: 'No', server: 'Server', browser: 'Browser only', never: '-', protectedTip: 'E-STOP alarms cannot be disabled',
    note: (shown, total) => (shown === total ? total + ' alarms' : shown + ' of ' + total + ' alarms'),
    since: (clock) => 'Last raised / Count are for this server run (since ' + clock + '); browser-only alarms are not counted',
    readonly: 'Read-only. Edit config/alarms.yaml and restart the server to change an alarm.',
    loadFailed: (why) => 'Could not load the catalog (' + why + ')',
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
    return [r.code, r.name, r.condition, r.message, r.level, r.sound ? TEXT.yes : TEXT.no, r.enabled ? TEXT.yes : TEXT.no,
      r.origin === 'browser' ? TEXT.never : stamp(r.last_raised), r.origin === 'browser' ? TEXT.never : String(r.count)];
  }

  return { TEXT, match, filter, sort, stamp, clock, cells };
})();
if (typeof module !== 'undefined') module.exports = AlarmView;
