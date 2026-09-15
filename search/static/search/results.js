// Results-page shell controls. The quick-filter handlers rewrite the current
// URL's query params and reload, so the server fetches the right data. (The
// search-type tabs are plain links now, so they need no JS.)

// Quick-filter selects, navigate to the same URL with updated param
(function () {
  function updateParam(key, value) {
    var url = new URL(window.location.href);
    if (value) { url.searchParams.set(key, value); }
    else { url.searchParams.delete(key); }
    url.searchParams.delete('page');
    window.location.href = url.toString();
  }
  var langSel = document.getElementById('qs-lang');
  var safeSel = document.getElementById('qs-safe');
  var dateSel = document.getElementById('qs-date');
  if (langSel) langSel.addEventListener('change', function () { updateParam('lang', this.value); });
  if (safeSel) safeSel.addEventListener('change', function () { updateParam('safe', this.value); });
  if (dateSel) dateSel.addEventListener('change', function () { updateParam('date', this.value); });

  // The translate selects live inside the translate form (a POST), unlike the
  // quick-filter selects above. Re-submitting the form, rather than rewriting
  // the URL, keeps the text in the textarea, which the URL never carries.
  var sourceSel = document.getElementById('translate-source');
  var targetSel = document.getElementById('translate-target');
  var swapBtn = document.getElementById('translate-swap');
  var translateForm = sourceSel && sourceSel.form;
  if (sourceSel) sourceSel.addEventListener('change', function () { if (translateForm) translateForm.submit(); });
  if (targetSel) targetSel.addEventListener('change', function () { if (translateForm) translateForm.submit(); });
  if (swapBtn) swapBtn.addEventListener('click', function (e) {
    // The button is a real submit (the no-JS path posts ``swap`` for the server
    // to act on); with JS we own the click, swapping client-side and submitting
    // without that flag (and doing nothing when the source is auto-detect).
    e.preventDefault();
    if (!sourceSel || !targetSel || !translateForm || sourceSel.value === 'auto') return;
    var tmp = sourceSel.value;
    sourceSel.value = targetSel.value;
    targetSel.value = tmp;
    translateForm.submit();
  });
})();

// Search-scope picker (<details id="scope-menu">). A multi-select dropdown, so
// (unlike the single-select filters above) the user can toggle several providers
// before anything happens; the new scope is applied — `scope` params rewritten
// and the page reloaded — only when the dropdown closes, and only if something
// actually changed. The `toggle` event is async and coalesces (closing it can
// fire late or not at all), so close is detected synchronously instead: a
// summary click while open, an outside click, or Escape. Without JS the
// checkboxes ride the filter form's no-JS "Apply" button instead.
(function () {
  var menu = document.getElementById('scope-menu');
  if (!menu) return;
  var summary = menu.querySelector('summary');
  var dirty = false;  // a provider was toggled since the dropdown last opened
  function apply() {
    if (!dirty) return;
    var url = new URL(window.location.href);
    url.searchParams.delete('scope');
    url.searchParams.delete('page');
    menu.querySelectorAll('input[name="scope"]:checked').forEach(function (b) {
      url.searchParams.append('scope', b.value);
    });
    window.location.href = url.toString();
  }
  function close() { if (menu.open) menu.removeAttribute('open'); apply(); }
  menu.querySelectorAll('input[name="scope"]').forEach(function (b) {
    b.addEventListener('change', function () { dirty = true; });
  });
  // A summary click toggles the dropdown; at click time `menu.open` is still the
  // pre-toggle state, so `open` here means it's about to close → apply.
  if (summary) summary.addEventListener('click', function () {
    if (menu.open) apply(); else dirty = false;
  });
  // Outside click / Escape also close the dropdown, mirroring the per-result menu.
  document.addEventListener('click', function (e) {
    if (menu.open && !menu.contains(e.target)) close();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && menu.open) close();
  });
})();

// Keyboard navigation for web results. j / ArrowDown move to the next result,
// k / ArrowUp to the previous; Enter opens the active result's link (honouring
// the user's new-tab preference, since it just clicks the real anchor). The
// active result is marked with aria-current="true", which the template styles.
// Pure progressive enhancement, the page is fully usable without it.
(function () {
  var results = Array.prototype.slice.call(document.querySelectorAll('.js-result'));
  var active = -1;
  var searchInput = document.querySelector('input[type="search"][name="q"]');

  function isTyping(el) {
    if (!el) return false;
    var tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
  }

  function setActive(i) {
    if (active >= 0 && results[active]) results[active].removeAttribute('aria-current');
    active = Math.max(0, Math.min(i, results.length - 1));
    var el = results[active];
    el.setAttribute('aria-current', 'true');
    el.focus({ preventScroll: true });
    el.scrollIntoView({ block: 'nearest' });
  }

  document.addEventListener('keydown', function (e) {
    // "/" jumps focus to the search bar. Handled before the modifier guard
    // below, and prevented unconditionally, because on some keyboard layouts
    // "/" is produced with AltGr (which Firefox reports as Ctrl+Alt); otherwise
    // it would fall through to Firefox's "Quick Find" bar. Skipped while already
    // typing so a slash inside a query still types normally.
    if (e.key === '/' && !e.metaKey && !isTyping(e.target)) {
      e.preventDefault();
      if (searchInput) { searchInput.focus(); searchInput.select(); }
      return;
    }

    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (isTyping(e.target) || !results.length) return;

    if (e.key === 'j' || e.key === 'ArrowDown') {
      e.preventDefault();
      setActive(active + 1);
    } else if (e.key === 'k' || e.key === 'ArrowUp') {
      e.preventDefault();
      setActive(active <= 0 ? 0 : active - 1);
    } else if (e.key === 'Enter' && active >= 0) {
      var link = results[active].querySelector('h2 a');
      if (link) { e.preventDefault(); link.click(); }
    }
  });

  // After an address-bar or external-site navigation (Seurch as default search
  // engine) the browser often leaves keyboard focus outside the page, so the
  // document-level keys above don't fire until the user clicks. If nothing in
  // the page is focused yet, pull focus into the results so keyboard navigation
  // works straight away. No result is highlighted until the user actually
  // navigates, and the search box (no autofocus here) is left alone so "/" and
  // typing still behave.
  if (results.length && (!document.activeElement || document.activeElement === document.body)) {
    results[0].focus({ preventScroll: true });
  }
})();

// Per-result actions menu (<details class="result-menu">). The native
// disclosure opens/closes on its summary on its own; this only adds the
// "dismiss when you click away (or press Escape)" behaviour CSS can't express,
// so the menu still works without JS, this just enhances it.
(function () {
  // Outside click: the <details> toggles its own `open` as a default action
  // that runs after this bubble listener, so clicking a summary still opens it.
  // Clicking a different result's summary also lands here and closes this one,
  // so at most one menu is open at a time.
  document.addEventListener('click', function (e) {
    document.querySelectorAll('details.result-menu[open]').forEach(function (menu) {
      if (!menu.contains(e.target)) menu.removeAttribute('open');
    });
  });
  // Escape closes the open menu and returns focus to its trigger.
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    document.querySelectorAll('details.result-menu[open]').forEach(function (menu) {
      menu.removeAttribute('open');
      var summary = menu.querySelector('summary');
      if (summary) summary.focus();
    });
  });
})();
