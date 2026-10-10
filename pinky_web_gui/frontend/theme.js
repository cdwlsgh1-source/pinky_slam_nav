'use strict';
// 라이트/다크 전환. 선택은 이 브라우저의 localStorage 에만 저장한다 (없으면 OS 설정을 따른다).
// <head> 에서 먼저 불러와 첫 화면이 깜빡이지 않게 data-theme 를 바로 정한다. 색은 style.css 의 변수로 바뀐다.
(function () {
  const KEY = 'pinky.theme';
  const root = document.documentElement;
  const saved = () => { try { const v = localStorage.getItem(KEY); return v === 'light' || v === 'dark' ? v : null; } catch (e) { return null; } };
  const system = () => (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const current = () => root.dataset.theme || saved() || system();

  function apply(theme) {
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    const btn = document.getElementById('themeBtn');
    if (btn) {
      btn.textContent = theme === 'dark' ? '라이트\n모드' : '다크\n모드';
      btn.title = theme === 'dark' ? '밝은 화면으로 전환합니다' : '어두운 화면으로 전환합니다';
    }
    document.dispatchEvent(new CustomEvent('themechange', { detail: theme }));
  }

  apply(current());
  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('themeBtn');
    apply(current());
    if (btn) btn.onclick = () => {
      const next = current() === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(KEY, next); } catch (e) { /* 저장이 막혀도 이번 화면에서는 바뀐다 */ }
      apply(next);
    };
  });
})();
