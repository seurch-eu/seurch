// Settings auto-save
(function () {
  'use strict';

  var i18n = window.SETTINGS_I18N || {};
  var savedLabel = i18n.saved || 'Saved';
  var toast, toastTimer;

  function flash(label) {
    if (!toast) {
      toast = document.createElement('div');
      toast.setAttribute('role', 'status');
      toast.setAttribute('aria-live', 'polite');
      toast.className = 'fixed bottom-5 right-5 z-50 px-4 py-2 rounded-md ' +
        'bg-slate-800 text-white text-[0.85rem] font-semibold shadow-lg ' +
        'pointer-events-none transition-opacity duration-200';
      document.body.appendChild(toast);
    }
    toast.textContent = label;
    toast.classList.remove('opacity-0');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.add('opacity-0'); }, 1600);
  }

  function save(form) {
    fetch(form.action, {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: new FormData(form),  // includes csrfmiddlewaretoken + the toggle/select state
      credentials: 'same-origin',
    }).then(function (resp) {
      if (!resp.ok) throw new Error('save failed');
      flash(savedLabel);
    }).catch(function () {
      // Background save lost (offline, server hiccup): fall back to a plain
      // submit so the change is committed via the no-JS path, never dropped.
      form.submit();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    var forms = document.querySelectorAll('form[data-autosave]');
    Array.prototype.forEach.call(forms, function (form) {
      form.addEventListener('change', function (event) {
        var t = event.target;
        if (t && (t.matches('input[type="checkbox"]') || t.matches('select'))) {
          save(form);
        }
      });
    });
  });
})();

// Settings pane deep-linking. The sidebar items are real links to
// /settings/<slug>/, so with no JS they just navigate (the server renders the
// chosen pane). With JS the panes are all already in the DOM, so switch in
// place — no reload — while keeping the URL, the active state and Back/Forward
// in sync via the History API. Mirrors the results tabs, which are also links.
(function () {
  'use strict';

  var nav = document.querySelector('.settings-nav');
  if (!nav || !window.history || !window.history.pushState) return;

  var links = Array.prototype.slice.call(nav.querySelectorAll('a[data-pane]'));
  if (!links.length) return;

  // pathname -> internal pane key, learned from the links themselves, so the
  // slug<->key mapping lives in one place (the server-rendered hrefs).
  var paneByPath = {};
  links.forEach(function (a) { paneByPath[new URL(a.href, location.href).pathname] = a.dataset.pane; });

  function currentPane() {
    var checked = document.querySelector('input[name="settings-tab"]:checked');
    return checked ? checked.id.replace(/^settings-/, '') : null;
  }

  // Show a pane: tick its (hidden) radio, which drives both pane visibility and
  // the active-link styling in CSS, and move aria-current for screen readers.
  function activate(pane) {
    var radio = document.getElementById('settings-' + pane);
    if (!radio) return false;
    radio.checked = true;
    links.forEach(function (a) {
      if (a.dataset.pane === pane) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    return true;
  }

  // Tag the initial history entry so a Back to it restores the right pane.
  var initial = currentPane();
  if (initial) history.replaceState({ settingsPane: initial }, '');

  nav.addEventListener('click', function (e) {
    var a = e.target.closest('a[data-pane]');
    // Let modified clicks (new tab/window), middle clicks etc. behave natively.
    if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (activate(a.dataset.pane)) {
      e.preventDefault();
      history.pushState({ settingsPane: a.dataset.pane }, '', a.href);
    }
  });

  window.addEventListener('popstate', function (e) {
    var pane = (e.state && e.state.settingsPane) || paneByPath[location.pathname];
    if (pane) activate(pane);
  });
})();
