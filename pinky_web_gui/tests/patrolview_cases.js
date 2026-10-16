'use strict';
// frontend/patrolview.js, zoneview.js 의 대기열 v3 상태(QUEUEING, RETREATING) 시험. tests/test_patrolview_js.py 가 headless Chrome 으로 돌린다.
async function runCases(PV, ZV, check) {
  const cfg = { home: 'P1', start_route: ['P2', 'ZONE_ENTRY', 'P3', 'P6', 'ZONE_EXIT', 'P1'] };
  const P = (state, waypoint, detail) => ({ state, waypoint, detail });
  const startLast = { cmd: 'start' }, gotoLast = { cmd: 'goto', points: ['P3', 'P4'] };

  check('QUEUEING·RETREATING 은 작업 중 상태', ['QUEUEING', 'RETREATING'].every((s) => PV.WORKING.includes(s) && ZV.WORKING.includes(s)));
  check('작업 중이라 stop 은 켜지고 start 는 꺼진다', PV.canStop('QUEUEING', true) && PV.canStop('RETREATING', true) && !PV.canStart('QUEUEING', true) && !PV.canStart('RETREATING', true));
  check('LED: QUEUEING 은 대기(빨강), RETREATING 은 주행(초록)', PV.badgeLevel('QUEUEING') === 'wait' && PV.badgeLevel('RETREATING') === 'run');
  check('기존 LED 규칙은 그대로', PV.badgeLevel('WAITING_ZONE') === 'wait' && PV.badgeLevel('MOVING') === 'run' && PV.badgeLevel('STOPPED') === 'idle');

  check('start: QUEUEING 문구에 슬롯 번호', PV.progressText(cfg, startLast, P('QUEUEING', 1, 'slot1')).includes('대기 슬롯 1'));
  check('start: 슬롯 형식이 달라도 문구는 나온다', PV.progressText(cfg, startLast, P('QUEUEING', 1, '')).includes('대기 슬롯에서'));
  check('goto: QUEUEING 문구에 지점 순번과 슬롯', (() => { const t = PV.progressText(cfg, gotoLast, P('QUEUEING', 0, 'slot0')); return t.includes('지점 1/2') && t.includes('대기 슬롯 0'); })());
  check('RETREATING 문구 (start, goto 둘 다)', PV.progressText(cfg, startLast, P('RETREATING', -1, 'ZONE_STOP_EXIT')).includes('후퇴 중')
    && PV.progressText(cfg, gotoLast, P('RETREATING', -1, 'ZONE_STOP_EXIT')).includes('후퇴 중'));
  check('후퇴 끝 STOPPED(retreated) 문구', PV.progressText(cfg, startLast, P('STOPPED', -1, 'retreated')).includes('후퇴 완료')
    && PV.progressText(cfg, gotoLast, P('STOPPED', -1, 'retreated')).includes('후퇴 완료'));
  check('일반 STOPPED 문구는 그대로', PV.progressText(cfg, startLast, P('STOPPED', 3, '')) === '정지됨 (순찰 3/6)');
  check('명령 이력이 없어도 QUEUEING 은 원문으로 나온다', PV.progressText(cfg, null, P('QUEUEING', 1, 'slot0')) === 'QUEUEING #1 slot0');

  const pts = { P3: { x: 1, y: 1, in_zone: true }, P4: { x: 2, y: 1, in_zone: true }, P1: { x: 0, y: 0 } };
  const q = ZV.routeOverlay(cfg, null, pts, startLast, P('QUEUEING', 1, 'slot0'));
  check('경로 선: start 의 QUEUEING 은 진입 지점(waypoint)을 목표로 강조', q && q.target === 1 && q.names[1] === 'ZONE_ENTRY');
  const g = ZV.routeOverlay(cfg, null, pts, gotoLast, P('QUEUEING', 0, 'slot0'));
  check('경로 선: goto 의 QUEUEING 은 첫 구역 지점을 목표로 강조', g && g.target === 0);
  check('경로 선: RETREATING(waypoint -1)은 선은 그리되 강조하지 않는다', (() => { const o = ZV.routeOverlay(cfg, null, pts, startLast, P('RETREATING', -1, 'ZONE_STOP_EXIT')); return o && o.target === -1; })());
  check('작업이 끝난 STOPPED 는 경로 선이 없다', ZV.routeOverlay(cfg, null, pts, startLast, P('STOPPED', -1, 'retreated')) === null);
}
if (typeof module !== 'undefined' && module.exports) module.exports = { runCases };
