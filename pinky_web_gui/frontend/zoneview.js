'use strict';
// Step 4 화면 로직(순수 함수): 구역 배지, 구역 진입 대기 문구, 경로 오버레이 계산. DOM 에 의존하지 않는다 (patrolview.js 와 같은 방식).
(function (root) {
  // 작업 중 상태. 경로 선은 이때만 그린다 (끝난 작업의 경로를 계속 보여 주면 현재 상황으로 오해한다).
  const WORKING = ['STARTING', 'MOVING', 'WAITING_ZONE', 'ARRIVED', 'LEAVING_ZONE', 'RETURNING', 'RETRY'];
  const isDoor = (n) => typeof n === 'string' && n.toUpperCase().startsWith('RED');

  // FR4-2: 상단 구역 배지. info 는 서버의 zone_info/zone 메시지({state, holder, held_sec}), recvMs 는 그것을 받은 시각(클라이언트 시계).
  // 점유 시간은 서버가 센 held_sec 에 '받은 뒤 지난 시간' 을 더한다 (클라이언트와 서버 시계가 달라도 맞다).
  function chip(info, nowMs, recvMs) {
    if (!info || info.state === 'unknown' || !info.state) return { level: 'dim', text: '구역: 상태 수신 전' };
    if (info.state === 'free') return { level: 'ok', text: '구역: 비어 있음' };
    const held = info.held_sec == null ? null : Math.max(0, Math.floor(info.held_sec + (nowMs - recvMs) / 1000));
    return { level: 'bad', text: '구역: ' + info.holder + ' 점유 중' + (held == null ? '' : ' (' + held + '초)') };
  }

  // FR4-3: WAITING_ZONE 로봇이 가려는 구역 안 지점. goto 는 detail 이 곧 그 지점, start 는 waypoint 가 진입 문 인덱스라서
  // start_route 에서 문 다음의 첫 일반 지점을 찾는다. 알 수 없으면 지점 없이 문구만.
  function waitingTarget(cfg, lastTask, patrol) {
    if (!patrol || patrol.state !== 'WAITING_ZONE') return null;
    if (lastTask && lastTask.cmd === 'goto') return patrol.detail && !isDoor(patrol.detail) ? patrol.detail : null;
    const route = (cfg && cfg.start_route) || [];
    if (lastTask && lastTask.cmd === 'start' && patrol.waypoint >= 0) {
      for (let i = patrol.waypoint; i < route.length; i++) if (!isDoor(route[i])) return route[i];
    }
    return null;
  }
  function waitingText(cfg, lastTask, patrol) {
    if (!patrol || patrol.state !== 'WAITING_ZONE') return '';
    const t = waitingTarget(cfg, lastTask, patrol);
    return '구역 진입 대기' + (t ? ' (' + t + ')' : '');
  }

  // goto 가 노드에서 실제로 지나는 순서: 구역 밖->안이면 문 IN, 안->밖이면 문 OUT 을 끼우고, 끝이 구역 안이면 OUT, 마지막은 홈 (_run_route).
  // 반환: { names: [...], idx: 명령 지점 i 가 names 의 몇 번째인지 }
  function expandGoto(points, doors, home, pts) {
    const names = [], idx = [];
    let inZone = false;
    for (const n of points) {
      const into = !!(pts[n] && pts[n].in_zone);
      if (!inZone && into) { if (doors) names.push(doors.in); inZone = true; }
      else if (inZone && !into) { if (doors) names.push(doors.out); inZone = false; }
      idx.push(names.length);
      names.push(n);
    }
    if (inZone && doors) names.push(doors.out);
    names.push(home);
    return { names, idx };
  }

  // FR4-4: 로봇의 경로 오버레이. 반환 null 이면 그리지 않는다.
  //   { names: [지점 이름...], target: 현재 목표의 names 인덱스 | -1 (강조 없음) }
  // goto: lastTask.points + patrol_status 의 waypoint(명령 지점 순번) 와 detail.   start: robot_settings.start_route + waypoint 번호.
  // waypoint 가 -1 이면 강조하지 않는다. 예외는 RETURNING 하나뿐이다 (detail 이 홈 이름이라 대상이 명확하다).
  function routeOverlay(cfg, doors, pts, lastTask, patrol) {
    if (!cfg || !lastTask || !patrol || !WORKING.includes(patrol.state)) return null;
    const { state, waypoint: wp, detail } = patrol;
    if (lastTask.cmd === 'goto' && lastTask.points && lastTask.points.length) {
      const { names, idx } = expandGoto(lastTask.points, doors, cfg.home, pts);
      let target = -1;
      if (state === 'RETURNING') target = names.length - 1;
      else if (wp >= 0 && wp < idx.length) {
        const at = idx[wp];
        if (state === 'ARRIVED') target = at;
        else if (state === 'WAITING_ZONE') target = at > 0 && names[at - 1] === (doors && doors.in) ? at - 1 : at;
        else if (state === 'MOVING') target = isDoor(detail) && at > 0 && names[at - 1] === detail ? at - 1 : at;
        else if (state === 'LEAVING_ZONE') {
          // 구역 안 -> 밖 도중이면 waypoint 는 '다음(밖) 지점' 순번이라 문이 그 앞에 있고, 마지막 지점에서 나오면 문이 그 뒤에 있다
          if (at > 0 && names[at - 1] === detail) target = at - 1;
          else { const j = names.indexOf(detail, at); target = j >= 0 ? j : -1; }
        }
      }
      return { names, target };
    }
    if (lastTask.cmd === 'start' && cfg.start_route && cfg.start_route.length) {
      const names = cfg.start_route.slice();
      let target = -1;
      if ((state === 'MOVING' || state === 'RETRY') && wp >= 0 && wp < names.length) target = wp;  // 방금 도착한 다음 번호가 다음 목표
      else if (state === 'WAITING_ZONE' && wp >= 0 && wp < names.length) target = wp;               // 진입 문 인덱스
      return { names, target };
    }
    return null;
  }

  // 선분: names 의 연속한 두 지점. state 는 지난 구간('done'), 현재 목표로 가는 구간('active'), 남은 구간('todo')
  function segments(overlay, pts) {
    const out = [];
    if (!overlay) return out;
    const { names, target } = overlay;
    for (let i = 0; i + 1 < names.length; i++) {
      const a = pts[names[i]], b = pts[names[i + 1]];
      if (!a || !b || (a.x === b.x && a.y === b.y)) continue;  // 문 IN/OUT 처럼 같은 위치는 선이 없다
      const state = target < 0 ? 'todo' : i + 1 < target ? 'done' : i + 1 === target ? 'active' : 'todo';
      out.push({ from: names[i], to: names[i + 1], a, b, state });
    }
    return out;
  }

  const api = { WORKING, chip, waitingTarget, waitingText, expandGoto, routeOverlay, segments, isDoor };
  root.ZoneView = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
