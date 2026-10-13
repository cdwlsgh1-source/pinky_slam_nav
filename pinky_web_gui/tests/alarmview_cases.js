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
  check('셀 9개: ID, 이름, 발생 조건, 메시지, 레벨, 소리, 사용, 최근 발생, 횟수', c.length === 9 && c[0] === 'X' && c[4] === 'H' && c[5] === '예' && c[6] === '예' && c[8] === '3' && c[7] !== '-');
  const b = AV.cells(R('Y', 'info', 'browser', { enabled: false }));
  check('브라우저 전용은 발생·횟수 -, info 는 소리 아니오, 꺼짐은 사용 아니오', b[5] === '아니오' && b[6] === '아니오' && b[7] === '-' && b[8] === '-');
  check('서버 알람 0회는 0', AV.cells(R('Z', 'L', 'server'))[8] === '0' && AV.cells(R('Z', 'L', 'server'))[7] === '-');
  check('고정 문구는 한국어다 (영문 문장 없음, 파일 이름·약어 제외)', JSON.stringify(Object.values(AV.TEXT).map((v) => (typeof v === 'function' ? v(['robot'], ['robot', 'cmd']) : v))).replace(/config\/alarms(\.local)?\.yaml|manager|info|robot|cmd|HH|ID|GUI/g, '').search(/[A-Za-z]{3,}/) < 0);
  check('안내 문구', AV.TEXT.note(2, 2) === '알람 2개' && AV.TEXT.note(1, 2) === '알람 2개 중 1개');

  // ---- Step A5: 편집 검증 (서버 backend/alarmcatalog.py 와 같은 규칙·문구) ----
  const AL = ['cmd', 'reason', 'robot'];
  const bad = (t) => AV.validateMessage(t, AL);
  check('메시지: 정상 문구와 자리표시자 통과', bad('Rejected {cmd} for {robot}: {reason}') === '' && bad('{robot} 명령 {cmd} 거절') === '' && bad('no placeholders') === '');
  check('메시지: 길이 1~120 글자', bad('') !== '' && bad('x'.repeat(120)) === '' && bad('x'.repeat(121)) === AV.TEXT.msgRange);
  check('메시지: 길이는 바이트가 아니라 글자 수 (한글 120자 통과)', bad('가'.repeat(120)) === '' && bad('가'.repeat(121)) !== '');
  check('메시지: 이모지 하나도 한 글자', bad('\u{1F6A8}'.repeat(120)) === '' && bad('\u{1F6A8}'.repeat(121)) !== '');
  check('메시지: 공백뿐이거나 줄바꿈이면 거절', bad('   ') === AV.TEXT.msgRange && bad('a\nb') === AV.TEXT.msgRange && bad('a\tb') === AV.TEXT.msgRange);
  check('메시지: 허용 밖 자리표시자는 이름을 알려 준다', bad('x {resource}') === '알 수 없는 자리표시자 {resource}. 허용: {cmd}, {reason}, {robot}');
  check('메시지: 속성·인덱스·형식 지정·빈 자리표시자 거절', ['{robot.x}', '{robot[0]}', '{robot:>5}', '{robot!r}', '{}'].every((t) => bad(t) === AV.TEXT.plain));
  check('메시지: 짝 없는 중괄호 거절, 이중 중괄호는 글자로 통과', ['open {', 'close }', '{robot'].every((t) => bad(t) === AV.TEXT.unbalanced) && bad('{{robot}}') === '');
  check('반복 주기: 빈 칸은 null(브라우저 설정)', (() => { const r = AV.parseRepeat('  '); return r.value === null && r.error === ''; })());
  check('반복 주기: 1~60 정수만', AV.parseRepeat('1').value === 1 && AV.parseRepeat('60').value === 60 && ['0', '61', '2.5', 'abc', '-3', '1e1'].every((t) => AV.parseRepeat(t).error === AV.TEXT.repeatRange));
  check('소리 칸 문구', AV.soundText(R('X', 'H', 'server', { sound: false })) === '아니오' && AV.soundText(R('X', 'H', 'server')) === '예'
    && AV.soundText(R('X', 'H', 'server', { sound_file: 'a.wav', repeat: 3 })) === '예 \u00b7 a.wav \u00b7 3초마다' && AV.soundText(R('X', 'H', 'server', { repeat: 2 })) === '예 \u00b7 2초마다');
  const base = R('X', 'H', 'server', { sound_file: '', repeat: null });
  check('diffPatch: 같은 값이면 빈 변경분', Object.keys(AV.diffPatch(base, { enabled: true, level: 'H', message: base.message, sound: true, sound_file: '', repeat: null })).length === 0);
  check('diffPatch: 바뀐 항목만', JSON.stringify(AV.diffPatch(base, { enabled: false, level: 'L', message: 'new', sound: true, sound_file: 'a.wav', repeat: 5 })) === JSON.stringify({ enabled: false, level: 'L', sound_file: 'a.wav', repeat: 5, message: 'new' }));
  check('diffPatch: 브라우저 알람은 메시지를 보내지 않는다', !('message' in AV.diffPatch(R('B', 'H', 'browser'), { message: 'zzz', enabled: false })) && AV.diffPatch(R('B', 'H', 'browser'), { message: 'zzz', enabled: false }).enabled === false);
  check('diffPatch: 반복 주기를 지우면 null', AV.diffPatch(R('X', 'H', 'server', { repeat: 4 }), { repeat: null }).repeat === null);
  const pol = { A: { enabled: false, level: 'LL', sound: false }, B: { enabled: true, level: 'HH', sound: true } };
  check('applyPolicy: 꺼진 알람은 건너뛴다', AV.applyPolicy(pol, 'A', 'H').skip === true);
  check('applyPolicy: 정책의 레벨을 쓴다', AV.applyPolicy(pol, 'B', 'H').level === 'HH' && AV.applyPolicy(pol, 'B', 'H').skip === false);
  check('applyPolicy: 정책이 없거나 코드가 없으면 호출부 값 그대로', AV.applyPolicy({}, 'A', 'H').level === 'H' && AV.applyPolicy(null, 'A', 'L').level === 'L' && AV.applyPolicy(pol, undefined, 'L').skip === false);
  check('soundOn: sound:false 만 소리 대상에서 빠진다', AV.soundOn(pol, 'A') === false && AV.soundOn(pol, 'B') === true && AV.soundOn({}, 'A') === true && AV.soundOn(null, 'A') === true);

  // ---- History 탭 (Step A7) ----
  const A = (id, tr, extra) => Object.assign({ id, t_raised: tr, code: 'CMD_REJECTED', level: 'L', robot: 'pinky1', text: '명령 거절', detail: '사유', state: 'active', count: 1, acked_by: null, acked_at: null, t_last: tr, t_cleared: null }, extra);
  check('keyOf: (id, t_raised) 쌍이 이름이다 (서버를 다시 켜면 id 가 겹친다)', AV.keyOf(A(1, 100.5)) !== AV.keyOf(A(1, 200.5)) && AV.keyOf(A(1, 100.5)) === AV.keyOf(A(1, 100.5)));
  check('liveState: active / acknowledged / cleared', AV.liveState(A(1, 1)) === 'active' && AV.liveState(A(1, 1, { acked_by: 'oper' })) === 'acknowledged' && AV.liveState(A(1, 1, { state: 'cleared', t_cleared: 5 })) === 'cleared');
  check('pinnable: 서버의 active 알람만 (브라우저 전용·해소·info 기록은 아니다)', AV.pinnable(A(1, 1)) && !AV.pinnable(A(1, 1, { local: true })) && !AV.pinnable(A(1, 1, { state: 'cleared' })) && !AV.pinnable(A(1, 1, { state: 'event', local: true })));
  const lr = AV.liveRow(A(2, 10, { state: 'cleared', t_cleared: 14.26 }));
  check('liveRow: 해소 알람은 지속 시간, 활성은 null', lr.state === 'cleared' && lr.duration === 4.3 && AV.liveRow(A(2, 10)).duration === null && AV.liveRow(A(2, 10)).live === true);
  const hf = { robot: 'pinky1', levels: ['L', 'H'], state: 'active', code: 'CMD_REJECTED', q: '거절' };
  check('matchRow: 모든 조건이 AND', AV.matchRow(AV.liveRow(A(1, 1)), hf) && !AV.matchRow(AV.liveRow(A(1, 1, { robot: 'pinky2' })), hf) && !AV.matchRow(AV.liveRow(A(1, 1, { level: 'LL' })), hf)
    && !AV.matchRow(AV.liveRow(A(1, 1, { acked_by: 'x' })), hf) && !AV.matchRow(AV.liveRow(A(1, 1, { code: 'X' })), hf) && !AV.matchRow(AV.liveRow(A(1, 1, { text: '다른', detail: '' })), hf));
  check('matchRow: 검색은 메시지와 detail, 대소문자 무시, 조건 없으면 전부', AV.matchRow(AV.liveRow(A(1, 1, { text: 'ABC', detail: 'xYz' })), { q: 'xyz' }) && AV.matchRow(AV.liveRow(A(1, 1)), {}) && AV.matchRow(AV.liveRow(A(1, 1)), { levels: [] }));
  const mg = AV.mergeRows([AV.liveRow(A(1, 10)), AV.liveRow(A(3, 30))], [AV.liveRow(A(3, 30)), AV.liveRow(A(1, 5)), AV.liveRow(A(2, 20))]);
  check('mergeRows: 고정은 최신 발생이 위, 같은 알람은 한 번만 (id 만 같고 시각이 다르면 다른 알람)', mg.pinned.map((r) => r.id).join() === '3,1' && mg.rows.map((r) => r.id + '@' + r.t_raised).join() === '1@5,2@20');
  const q = AV.historyQuery({ from: '2026-10-12', to: '2026-10-12', robot: 'pinky1', levels: ['HH', 'H'], state: 'cleared', code: ' cmd_sent ', q: ' 거절 ' }, { view: 'alarms', limit: 200, before: '5.5:3' });
  const p = new URLSearchParams(q);
  check('historyQuery: 값을 서버 파라미터로 (코드는 대문자, 검색어는 공백 제거, 빈 값은 생략)', p.get('from') === '2026-10-12' && p.get('level') === 'HH,H' && p.get('code') === 'CMD_SENT' && p.get('q') === '거절' && p.get('view') === 'alarms' && p.get('limit') === '200' && p.get('before') === '5.5:3');
  check('historyQuery: 빈 조건은 파라미터가 없다', AV.historyQuery({ from: '', robot: '', levels: [], state: '', code: '', q: '' }, { before: null }) === '');
  check('today: YYYY-MM-DD', AV.today(new Date(2026, 9, 1)) === '2026-10-01' && /^\d{4}-\d\d-\d\d$/.test(AV.today()));
  check('durationText', AV.durationText(null) === '-' && AV.durationText(4.26) === '4.3초' && AV.durationText(75) === '1분 15초' && AV.durationText(3720) === '1시간 02분');
  const hc = AV.historyCells({ t_raised: 1790000000, robot: null, level: 'H', text: 'm', state: 'cleared', count: 3, acked_by: 'oper', acked_at: 1790000100, t_cleared: 1790000200, duration: 200 });
  check('historyCells: 9칸 (발생, 로봇, 레벨, 메시지, 상태, 횟수, 확인, 해소, 지속 시간)', hc.length === 9 && AV.H.head.length === 9 && hc[1] === '-' && hc[4] === '해소' && hc[5] === '3' && hc[6].startsWith('oper ') && hc[8] === '3분 20초');
  const hu = AV.historyCells({ t_raised: 1790000000, robot: 'a', level: 'H', text: 'm', state: 'uncleared', count: 1, acked_by: null, acked_at: null, t_cleared: null, duration: null });
  check('historyCells: 해소 기록 없음은 해소·지속 시간이 -', hu[4] === '해소 기록 없음' && hu[7] === '-' && hu[8] === '-' && AV.historyCells(AV.liveRow(A(1, 1790000000)))[8] === AV.H.ongoing);
  check('상태 문구(PRD 8-5)', AV.H.states.active === '활성' && AV.H.states.cleared === '해소' && AV.H.states.acknowledged === '확인됨' && AV.H.states.uncleared === '해소 기록 없음');
  check('타임라인 줄: 발생·반복·확인·해소', AV.timelineLine({ t: 1790000000, event: 'repeated', count: 3 }).includes('반복') && AV.timelineLine({ t: 1790000000, event: 'acked', acked_by: 'oper' }).includes('(oper)')
    && AV.timelineLine({ t: 1790000000, event: 'cleared' }).includes('해소') && AV.timelineLine({ t: 1790000000, event: 'raised', count: 1 }).includes('발생'));
  check('History 고정 문구는 한국어 (버튼 이름 Show files/Hide files 제외)', JSON.stringify(Object.entries(AV.H).filter(([k]) => !/Files$/.test(k)).map(([, v]) => (typeof v === 'function' ? v(Object.assign(new String('x'), { dir: '/d', total_files: 1, total_alarms: 2, name: 'n', alarms: 1, records: 2 }), 3) : (v && typeof v === 'object' && !Array.isArray(v) ? Object.values(v) : v))))
    .replace(/\/d|n\.|n /g, '').search(/[A-Za-z]{3,}/) < 0);
  check('안내 문구: 최신 N건 (전체 M건)', AV.H.note(200, 669) === '최신 200건 (전체 669건)' && AV.H.note(3, null) === '최신 3건' && AV.H.exportCap(10000) === '내보내기는 10000행까지입니다');
  check('버튼 이름은 영문', AV.H.showFiles === 'Show files' && AV.H.hideFiles === 'Hide files');
}
if (typeof module !== 'undefined' && module.exports) {
  module.exports = runCases;
  if (require.main === module) {
    let bad = 0;
    runCases(require('../frontend/alarmview.js'), (n, ok) => { console.log((ok ? 'PASS ' : 'FAIL ') + n); if (!ok) bad++; }).then(() => process.exit(bad ? 1 : 0));
  }
}
