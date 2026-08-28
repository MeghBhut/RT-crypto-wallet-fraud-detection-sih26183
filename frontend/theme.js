/* theme.js — Cyanotype light/dark toggle (persists + syncs icon). Vanilla. */
(function () {
  var STORAGE_KEY = 'cyanotype-theme';

  function stored() { try { return localStorage.getItem(STORAGE_KEY); } catch (e) { return null; } }
  function save(t) { try { localStorage.setItem(STORAGE_KEY, t); } catch (e) {} }
  function current() {
    return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  }
  function apply(t) {
    if (t === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    else document.documentElement.removeAttribute('data-theme');
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      b.setAttribute('aria-pressed', t === 'dark' ? 'true' : 'false');
    });
  }
  function toggle() {
    var next = current() === 'dark' ? 'light' : 'dark';
    apply(next); save(next);
  }

  document.addEventListener('DOMContentLoaded', function () {
    apply(current());
    document.querySelectorAll('[data-theme-toggle]').forEach(function (b) {
      b.addEventListener('click', toggle);
    });
  });

  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) {
      if (!stored()) apply(e.matches ? 'dark' : 'light');
    });
  }
})();
