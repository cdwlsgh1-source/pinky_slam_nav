// 로그 화면의 순수 함수들 (DOM 없음). app.js 가 쓰고 테스트에서도 불러 쓴다.
const LogView = (() => {
  const RANK = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 };
  const MAX_KEEP = 5000;   // 화면이 들고 있는 최대 줄 수

  // 레벨 필터: 'all' 은 전부, 'warn' 은 WARN 이상, 'error' 는 ERROR 만
  function minRank(level) { return level === 'error' ? 3 : level === 'warn' ? 2 : 0; }

  function match(e, f) {
    if (f.src && f.src !== 'all' && e.src !== f.src) return false;
    if ((RANK[e.lvl] ?? 1) < minRank(f.level)) return false;
    if (f.q && !e.text.toLowerCase().includes(f.q.toLowerCase())) return false;
    return true;
  }

  function filter(entries, f) { return entries.filter((e) => match(e, f)); }

  // 새 줄을 이어 붙이고 상한을 넘으면 오래된 줄부터 버린다
  function append(entries, rows) {
    const out = entries.concat(rows);
    return out.length > MAX_KEEP ? out.slice(out.length - MAX_KEEP) : out;
  }

  // 소스별 줄 수와 WARN/ERROR 수 (탭에 배지로 보여준다)
  function counts(entries) {
    const c = {};
    const bump = (k, e) => {
      const o = c[k] || (c[k] = { n: 0, warn: 0, error: 0 });
      o.n++; if (e.lvl === 'WARN') o.warn++; else if (e.lvl === 'ERROR') o.error++;
    };
    for (const e of entries) { bump(e.src, e); bump('all', e); }
    return c;
  }

  function timeText(t) {
    const d = new Date(t * 1000), p = (n) => String(n).padStart(2, '0');
    return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  function copyText(rows, labels) {
    return rows.map((e) => timeText(e.t) + ' ' + e.lvl.padEnd(5) + ' [' + (labels[e.src] || e.src) + '] ' + e.text).join('\n');
  }

  return { match, filter, append, counts, timeText, copyText, MAX_KEEP };
})();
if (typeof module !== 'undefined') module.exports = LogView;
