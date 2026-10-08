'use strict';
// 명령 패널의 화면 로직(순수 함수). DOM 에 의존하지 않아서 브라우저와 node 양쪽에서 불러올 수 있다 (mapmath.js 와 같은 방식).
(function (root) {
  // 작업 중 상태 (노드의 _running 이 True 인 동안). stop 은 이때만 의미가 있다.
  const WORKING = ['STARTING', 'MOVING', 'WAITING_ZONE', 'ARRIVED', 'LEAVING_ZONE', 'RETURNING', 'RETRY'];
  // 새 작업을 받을 수 있는 상태. 노드는 작업이 끝나도 별도 IDLE 을 발행하지 않으므로 마지막 상태가 남는다.
  const FREE = ['IDLE', 'DONE', 'STOPPED', 'FAILED'];

  const RESULT_LABEL = {
    sent: '전송됨', acknowledged: '응답 확인', no_response: '응답 없음', rejected: '거절됨', failed: '전송 실패',
  };

  // FR3-9: 로봇 LED 규칙. 주행 중 초록, WAITING_ZONE 빨강, ARRIVED(도착 후 대기) 노랑, FAILED 진한 빨강, 그 외 회색.
  function badgeLevel(state) {
    if (state === 'MOVING' || state === 'LEAVING_ZONE' || state === 'RETURNING' || state === 'RETRY') return 'run';
    if (state === 'WAITING_ZONE') return 'wait';
    if (state === 'ARRIVED') return 'arrived';
    if (state === 'FAILED') return 'failed';
    return 'idle';
  }

  // FR3-5: 버튼 활성 조건은 화면 힌트다 (최종 판단은 노드). 오프라인이면 전부 비활성.
  // 아직 상태를 못 받은 online 로봇(state 없음)은 IDLE 을 놓친 경우(늦은 접속)라서 막지 않는다.
  function canStart(state, online) {
    return !!online && (!state || FREE.includes(state));
  }
  function canStop(state, online) {
    return !!online && (!state || WORKING.includes(state));
  }

  // FR3-13: 노드가 FAILED 로 알려 주는 goto 검증 실패를 한글로. 해당 없으면 null.
  function failReason(detail) {
    const d = String(detail || '');
    if (d.startsWith('no point given')) return '이동할 지점이 비어 있어 로봇이 거절했습니다';
    if (d.startsWith('unknown point')) return '로봇이 모르는 지점이라 거절했습니다 (' + d + ')';
    if (d.startsWith('not allowed')) return '이동이 허용되지 않은 지점이라 로봇이 거절했습니다 (' + d + ')';
    return null;
  }

  const isDoor = (name) => typeof name === 'string' && name.toUpperCase().startsWith('RED');

  // FR3-12: 카드의 진행 문구. last 는 서버가 기억한 이 로봇의 마지막 명령({cmd, points}), cfg 는 로봇별 설정.
  function progressText(cfg, last, patrol) {
    if (!patrol || !patrol.state) return '-';
    const { state, waypoint: wp, detail } = patrol;
    if (state === 'FAILED') {
      const why = failReason(detail);
      if (why) return '실패: ' + why;
    }
    if (last && last.cmd === 'goto' && last.points && last.points.length) return gotoProgress(cfg, last.points, patrol);
    if (last && last.cmd === 'start' && cfg && cfg.start_route && cfg.start_route.length) return startProgress(cfg, patrol);
    return state + (wp >= 0 ? ' #' + wp : '') + (detail ? ' ' + detail : '');
  }

  function gotoProgress(cfg, points, patrol) {
    const { state, waypoint: wp, detail } = patrol;
    const n = points.length;
    const at = wp >= 0 && wp < n ? '지점 ' + (wp + 1) + '/' + n + ' · ' : '';
    switch (state) {
      case 'STARTING': return '시작 중 (경로: ' + (detail || points.join(', ')) + ')';
      case 'MOVING': return isDoor(detail) ? at + '구역 문 이동 중' : at + detail + ' 이동 중';
      case 'WAITING_ZONE': return at + detail + ' 진입 허가 대기 중 (다른 로봇이 구역 사용 중)';
      case 'ARRIVED': return at + detail + ' 도착, 대기 중';
      case 'LEAVING_ZONE': return '구역 문 이동 중 (구역 이탈)';
      case 'RETURNING': return '홈(' + detail + ') 복귀 중';
      case 'DONE': return '완료 (홈 복귀)';
      case 'STOPPED': return '정지됨' + (wp >= 0 ? ' (' + at + detail + ')' : ' (홈 복귀 중)');
      case 'FAILED': return '실패' + (wp >= 0 ? ' (' + at + detail + ')' : detail ? ' (' + detail + ')' : '');
      case 'RETRY': return '재시도 중';
      default: return state;
    }
  }

  // start 의 waypoint 는 start_route 의 인덱스다. MOVING 은 도착한 웨이포인트 '다음' 번호, 나머지는 현재 번호 (docs/interfaces.md 4-1).
  function startProgress(cfg, patrol) {
    const { state, waypoint: wp } = patrol;
    const route = cfg.start_route, n = route.length;
    const name = (i) => (i >= 0 && i < n ? route[i] : null);
    switch (state) {
      case 'STARTING': return '순찰 시작 중';
      case 'MOVING': {
        const next = name(wp);
        if (!next) return '순찰 ' + wp + '/' + n + ' 도착';
        return '순찰 ' + wp + '/' + n + ' 지점 통과 · ' + (isDoor(next) ? '구역 문 이동 중' : next + ' 이동 중');
      }
      case 'WAITING_ZONE': return '진입 허가 대기 중 (다른 로봇이 구역 사용 중)';
      case 'DONE': return '순찰 완료';
      case 'STOPPED': return '정지됨 (순찰 ' + wp + '/' + n + ')';
      case 'FAILED': return '실패 (순찰 ' + wp + '/' + n + ')';
      case 'RETRY': return '재시도 중';
      default: return state;
    }
  }

  // FR3-6: 확인 팝업의 경로 요약
  function routeSummary(rid, cfg, points) {
    const wait = cfg.wait_every_point
      ? '지점마다 ' + cfg.goto_wait_sec + '초 대기'
      : '마지막 지점에서만 ' + cfg.goto_wait_sec + '초 대기';
    return rid + ': ' + points.join(' → ') + ' → 홈(' + cfg.home + '), ' + wait;
  }

  function startSummary(rid, cfg) {
    const route = cfg.start_route && cfg.start_route.length ? cfg.start_route.join(' → ') : '(경로 설정 없음)';
    return rid + ': 순찰 시작 (' + route + ')';
  }

  // FR3-11: STARTING 이 오래 이어지면 첫 작업의 초기 위치 보정 안내를 낸다.
  function startingNotice(state, sinceMs, nowMs, thresholdSec) {
    return state === 'STARTING' && sinceMs != null && (nowMs - sinceMs) / 1000 > thresholdSec;
  }

  function resultLabel(result) { return RESULT_LABEL[result] || String(result); }

  const api = { WORKING, FREE, badgeLevel, canStart, canStop, failReason, progressText, routeSummary,
                startSummary, startingNotice, resultLabel, isDoor };
  root.PatrolView = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
