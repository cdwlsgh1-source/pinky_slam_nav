'use strict';
// frontend/alarmview.js (Alarm 화면 Catalog 탭의 순수 함수) 시험. tests/test_alarmview_js.py 가 headless Chrome 으로 돌린다. node 가 있으면 `node tests/alarmview_cases.js` 로도 돈다.
async function runCases(AV, check) {
  const R = (code, level, origin, extra) => Object.assign({ code, name: 'Name ' + code, condition: 'cond', message: 'msg {robot}', level, sound: level !== 'info', enabled: true, protected: false, origin, source: 's', last_raised: null, count: origin === 'server' ? 0 : null }, extra);
  const rows = [R('A_INFO', 'info', 'server'), R('B_LL', 'LL', 'browser'), R('C_H', 'H', 'browser'), R('D_H', 'H', 'server', { name: 'Robot offline' }), R('E_HH', 'HH', 'server'), R('F_L', 'L', 'server')];

  check('필터 없음은 전부', AV.filter(rows, {}).length === rows.length && AV.filter(rows, { level: 'all', origin: 'all', q: '' }).length === rows.length);
  check('레벨 필터', AV.filter(rows, { level: 'H' }).map((r) => r.code).join() === 'C_H,D_H');
  check('출처 필터', AV.filter(rows, { origin: 'browser' }).map((r) => r.code).join() === 'B_LL,C_H');
  check('검색은 이름 부분 일치, 대소문자 무시', AV.filter(rows, { q: 'ROBOT OFF' }).map((r) => r.code).join() === 'D_H');
  check('검색은 ID 도 본다', AV.filter(rows, { q: 'e_hh' }).length === 1);
  check('조건·메시지도 본다', AV.filter(rows, { q: 'msg {ro' }).length === rows.length && AV.filter(rows, { q: 'zzz' }).length === 0);
  check('필터는 AND', AV.filter(rows, { level: 'H', origin: 'server' }).map((r) => r.code).join() === 'D_H');
  check('정렬: 심각한 레벨 먼저, 같은 레벨은 서버 알람이 먼저', AV.sort(rows).map((r) => r.code).join() === 'E_HH,D_H,C_H,F_L,B_LL,A_INFO');
  check('정렬은 원본을 바꾸지 않는다', rows[0].code === 'A_INFO');
  check('시각 없음은 -', AV.stamp(null) === '-');
  check('시각 형식 YYYY-MM-DD HH:MM:SS', /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(AV.stamp(1790000000)) && AV.clock(1790000000).length === 8);
  const c = AV.cells(R('X', 'H', 'server', { last_raised: 1790000000, count: 3 }));
  check('셀 9개: ID, Name, Condition, Message, Level, Sound, Enabled, Last raised, Count', c.length === 9 && c[0] === 'X' && c[4] === 'H' && c[5] === 'Yes' && c[6] === 'Yes' && c[8] === '3' && c[7] !== '-');
  const b = AV.cells(R('Y', 'info', 'browser', { enabled: false }));
  check('브라우저 전용은 발생·횟수 -, info 는 Sound No, 꺼짐은 Enabled No', b[5] === 'No' && b[6] === 'No' && b[7] === '-' && b[8] === '-');
  check('서버 알람 0회는 0', AV.cells(R('Z', 'L', 'server'))[8] === '0' && AV.cells(R('Z', 'L', 'server'))[7] === '-');
  check('고정 문구에 한글이 없다', JSON.stringify(Object.values(AV.TEXT).map((v) => (typeof v === 'function' ? v(1, 2) : v))).search(/[가-힣]/) < 0);
  check('안내 문구', AV.TEXT.note(2, 2) === '2 alarms' && AV.TEXT.note(1, 2) === '1 of 2 alarms');
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = runCases;
  if (require.main === module) {
    let bad = 0;
    runCases(require('../frontend/alarmview.js'), (n, ok) => { console.log((ok ? 'PASS ' : 'FAIL ') + n); if (!ok) bad++; }).then(() => process.exit(bad ? 1 : 0));
  }
}
