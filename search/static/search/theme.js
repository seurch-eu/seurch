// Theme controller
(function () {
  var COOKIE = 'seurch_prefs';
  var ONE_YEAR = 60 * 60 * 24 * 365;
  var THEMES = { system: 1, light: 1, dark: 1 };

  function readPrefs() {
    try {
      var m = document.cookie.match(/(?:^|; )seurch_prefs=([^;]*)/);
      return m ? JSON.parse(decodeURIComponent(m[1])) : {};
    } catch (e) {
      return {};
    }
  }

  function writePrefs(p) {
    document.cookie = COOKIE + '=' + encodeURIComponent(JSON.stringify(p)) +
      ';path=/;max-age=' + ONE_YEAR + ';samesite=Lax';
  }

  function prefersDark() {
    return window.matchMedia('(prefers-color-scheme: dark)').matches;
  }

  function current() {
    return readPrefs().theme || 'system';
  }

  // Set both class hooks the stylesheet understands: `.dark` forces dark, and
  // `.theme-system` lets the CSS follow the OS for the no-JS system case (here
  // we resolve it ourselves via matchMedia, so the two always agree).
  function apply(theme) {
    var el = document.documentElement;
    var sys = theme === 'system';
    el.classList.toggle('theme-system', sys);
    el.classList.toggle('dark', theme === 'dark' || (sys && prefersDark()));
  }

  function syncToServer(theme) {
    try {
      var m = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
      if (!m) return;
      fetch('/settings/theme', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-Requested-With': 'XMLHttpRequest',
          'X-CSRFToken': decodeURIComponent(m[1]),
        },
        body: 'theme=' + encodeURIComponent(theme),
        credentials: 'same-origin',
      }).catch(function () {});
    } catch (e) {}
  }

  function set(theme) {
    var p = readPrefs();        // preserve every other preference
    p.theme = theme;
    writePrefs(p);
    apply(theme);
    syncButtons();
    syncToServer(theme);
  }

  function toggle() {
    set(document.documentElement.classList.contains('dark') ? 'light' : 'dark');
  }

  // Highlight the active button in the Appearance pane (if present) and keep the
  // header toggle's no-JS fallback value (the theme it would POST) pointing at
  // the opposite of the current state.
  function syncButtons() {
    var active = ['border-indigo-500', 'text-indigo-600', 'dark:text-indigo-400', 'bg-indigo-50', 'dark:bg-indigo-950/40'];
    var inactive = ['border-slate-200', 'dark:border-slate-700', 'text-slate-600', 'dark:text-slate-400'];
    var ids = { system: 'btn-system', light: 'btn-light', dark: 'btn-dark' };
    ['btn-system', 'btn-light', 'btn-dark'].forEach(function (id) {
      var b = document.getElementById(id);
      if (!b) return;
      active.forEach(function (c) { b.classList.remove(c); });
      inactive.forEach(function (c) { b.classList.add(c); });
    });
    var activeBtn = document.getElementById(ids[current()]);
    if (activeBtn) {
      inactive.forEach(function (c) { activeBtn.classList.remove(c); });
      active.forEach(function (c) { activeBtn.classList.add(c); });
    }
    var darkBtn = document.getElementById('dark-btn');
    if (darkBtn) {
      darkBtn.value = document.documentElement.classList.contains('dark') ? 'light' : 'dark';
    }
  }

  // Intercept a theme <form> submit so the change applies instantly (and saves
  // in the background) instead of reloading. The clicked button's `value` is the
  // target theme. If the browser doesn't expose the submitter, or the value is
  // unexpected, fall through to the normal POST (which the server handles).
  function onThemeSubmit(e) {
    var btn = e.submitter;
    var theme = btn && btn.value;
    if (!THEMES[theme]) return;
    e.preventDefault();
    set(theme);
  }

  // Expose for any remaining callers / debugging.
  window.setTheme = set;
  window.SeurchTheme = { set: set, toggle: toggle, current: current, apply: apply };

  apply(current());  // re-assert (covers a cookie changed by import / cloud restore)

  document.addEventListener('DOMContentLoaded', function () {
    var forms = document.querySelectorAll('form[data-theme-form]');
    Array.prototype.forEach.call(forms, function (f) {
      f.addEventListener('submit', onThemeSubmit);
    });
    syncButtons();
  });
})();
