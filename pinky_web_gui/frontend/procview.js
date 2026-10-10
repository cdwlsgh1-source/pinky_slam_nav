'use strict';
// 시스템 패널(프로세스 제어) 화면 로직(순수 함수). DOM 에 의존하지 않는다 (zoneview.js 와 같은 방식).
(function (root) {
  const LABEL = { stopped: '정지', starting: '시작 중', running: '실행 중', stopping: '정지 중', failed: '실패', exited: '종료됨' };
  const ACTIVE = ['starting', 'running', 'stopping'];

  // 배지 색: 실행 중이어도 로봇 토픽이 안 오면(health === false) 노랑. 프로세스가 떠 있다는 것과 실제로 되는 것은 다르다.
  function badge(p) {
    if (!p.configured) return { level: 'dim', text: '명령 미설정' };
    const text = LABEL[p.state] || p.state;
    if (p.state === 'running') return p.health === false ? { level: 'warn', text: text + ' · 토픽 없음' } : { level: 'ok', text };
    if (p.state === 'starting' || p.state === 'stopping') return { level: 'warn', text };
    if (p.state === 'failed') return { level: 'bad', text };
    if (p.state === 'exited') return { level: 'warn', text };
    return { level: 'dim', text };
  }

  // ctx: { enabled(서버가 프로세스 제어를 켰는지), role, seqRunning }
  // 반환: null 이면 가능, 문자열이면 불가 사유(버튼 title 로 보여준다)
  function blocked(ctx) {
    if (!ctx.enabled) return '프로세스 제어가 꺼져 있습니다 (로그인이 꺼진 --no-auth 서버)';
    if (ctx.role !== 'manager') return '보기 전용 계정은 사용할 수 없습니다';
    return null;
  }
  function startBlocked(p, ctx) {
    const b = blocked(ctx);
    if (b) return b;
    if (!p.configured) return '명령이 설정되지 않았습니다 (config/processes.yaml)';
    if (ctx.seqRunning) return '전체 시작/정지가 진행 중입니다';
    if (ACTIVE.includes(p.state)) return '이미 ' + (LABEL[p.state] || p.state) + ' 상태입니다';
    return null;
  }
  function stopBlocked(p, ctx) {
    const b = blocked(ctx);
    if (b) return b;
    if (ctx.seqRunning) return '전체 시작/정지가 진행 중입니다';
    if (p.state !== 'running' && p.state !== 'starting') return '실행 중이 아닙니다';
    return null;
  }
  function startAllBlocked(procs, ctx) {
    const b = blocked(ctx);
    if (b) return b;
    if (ctx.seqRunning) return '전체 시작/정지가 진행 중입니다';
    if (!procs.some((p) => p.configured && p.state !== 'running')) return '시작할 프로세스가 없습니다';
    return null;
  }
  function stopAllBlocked(procs, ctx) {
    const b = blocked(ctx);
    if (b) return b;
    if (ctx.seqRunning) return '전체 시작/정지가 진행 중입니다';
    if (!procs.some((p) => p.state === 'running' || p.state === 'starting')) return '실행 중인 프로세스가 없습니다';
    return null;
  }

  // 서버는 실행 시간(uptime_sec)을 상태가 바뀔 때만 보낸다. 화면이 받은 시각(recvMs)부터 지난 시간을 더해서 1초마다 흐르게 한다.
  function uptimeNow(p, nowMs, recvMs) {
    return p.uptime_sec == null ? null : p.uptime_sec + Math.max(0, nowMs - recvMs) / 1000;
  }
  function uptimeText(sec) {
    if (sec == null) return '';
    const s = Math.floor(sec), pad = (n) => String(n).padStart(2, '0');
    return s < 60 ? s + '초' : s < 3600 ? Math.floor(s / 60) + '분 ' + pad(s % 60) + '초'
      : Math.floor(s / 3600) + '시간 ' + pad(Math.floor((s % 3600) / 60)) + '분 ' + pad(s % 60) + '초';
  }

  const RESULT = { started: '시작함', already: '이미 실행 중', external: '이미 다른 곳에서 실행 중 (건너뜀)', unset: '명령 미설정 (건너뜀)', stopped: '정지함', failed: '실패' };

  // 전체 시작/정지 진행 문구
  function sequenceText(seq, labelOf) {
    if (!seq) return '';
    const name = seq.kind === 'start_all' ? '전체 시작' : '전체 정지';
    if (seq.state === 'running') return name + ' 진행 중' + (seq.step ? ': ' + labelOf(seq.step) : '') + (seq.message ? ' — ' + seq.message : '');
    if (seq.state === 'failed') return name + ' 중단: ' + (seq.message || '실패');
    const skipped = seq.results.filter((r) => r.result === 'external' || r.result === 'unset').length;
    return name + ' 완료' + (skipped ? ' (' + skipped + '개 건너뜀)' : '');
  }

  // 시작 전 확인 문구: 어떤 순서로 무엇을 켜는지
  function startAllSummary(procs) {
    const list = procs.filter((p) => p.configured && p.state !== 'running').map((p, i) => (i + 1) + '. ' + p.label);
    const skipped = procs.filter((p) => !p.configured).length;
    return '다음 순서로 시작합니다.\n' + list.join('\n') + (skipped ? '\n(명령 미설정 ' + skipped + '개는 건너뜁니다)' : '');
  }
  function stopSummary(p) { return p.label + ' 을(를) 정지합니다. 로봇이 주행 중이면 영향을 줄 수 있습니다. 계속할까요?'; }
  function stopAllSummary(procs) {
    const list = procs.slice().reverse().filter((p) => p.state === 'running' || p.state === 'starting').map((p, i) => (i + 1) + '. ' + p.label);
    return '다음 순서로 정지합니다.\n' + list.join('\n') + (procs.some((p) => p.confirm_stop && (p.state === 'running' || p.state === 'starting'))
      ? '\n로봇에서 실행 중인 프로세스가 포함되어 있습니다. 주행 중이면 영향을 줄 수 있습니다.' : '');
  }

  const api = { LABEL, badge, startBlocked, stopBlocked, startAllBlocked, stopAllBlocked, uptimeNow, uptimeText, sequenceText, startAllSummary, stopSummary, stopAllSummary, RESULT };
  root.ProcView = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
