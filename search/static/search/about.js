/* About page, client-side search over the (large) bang list.
 *
 * The full bang map is too big to render on the page, so we load it once on
 * first interaction, build a lightweight index (trigger + hostname), and show
 * only the top matches as the user types. The user's own custom bangs (the
 * #user-bangs JSON data block rendered by base.html when signed in) are folded
 * in and flagged, mirroring how bangs.js lets them override the generic set.
 */
(function () {
  var input = document.getElementById('bang-search');
  var results = document.getElementById('bang-results');
  var status = document.getElementById('bang-status');
  if (!input || !results || !status) return;

  var MAX = 30;                 // most rows we ever paint
  var index = null;             // [{ t: trigger, u: url, h: hostname, c: custom }]
  var loadPromise = null;
  var failed = false;
  var debounce = null;
  var userBangs = (function () {
    var el = document.getElementById('user-bangs');
    if (!el) return {};
    try { return JSON.parse(el.textContent) || {}; } catch (e) { return {}; }
  })();
  var CHIP = 'font-mono font-bold text-indigo-600 dark:text-indigo-400 text-[0.85rem] shrink-0';
  var DESC = 'flex-1 min-w-0 truncate text-[0.82rem] text-slate-500 dark:text-slate-400';
  var BADGE = 'shrink-0 rounded-full bg-indigo-50 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300 text-[0.68rem] font-semibold px-2 py-0.5';

  function bangsUrl() {
    return (typeof window.BANGS_URL !== 'undefined') ? window.BANGS_URL : '/static/search/bangs.min.json';
  }

  // Cheap hostname extraction, avoids the URL constructor's overhead across
  // thousands of templates, and ignores the {{{s}}} placeholder in the query.
  function hostname(tpl) {
    var m = /^https?:\/\/([^/?#]+)/i.exec(tpl || '');
    return m ? m[1].replace(/^www\./i, '').toLowerCase() : '';
  }

  function buildIndex(data) {
    var out = [];
    var seen = {};
    Object.keys(userBangs).forEach(function (t) {
      var lt = String(t).toLowerCase();
      seen[lt] = true;
      out.push({ t: lt, u: userBangs[t], h: hostname(userBangs[t]), c: true });
    });
    Object.keys(data).forEach(function (t) {
      if (seen[t]) return;      // a custom bang shadows the generic one
      out.push({ t: t, u: data[t], h: hostname(data[t]), c: false });
    });
    return out;
  }

  function load() {
    if (index) return Promise.resolve(index);
    if (loadPromise) return loadPromise;
    loadPromise = fetch(bangsUrl())
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (d) { index = buildIndex(d); return index; })
      .catch(function () { failed = true; index = buildIndex({}); return index; });
    return loadPromise;
  }

  // Status line shown when the box is empty (idle), the count, or the error.
  function settle() {
    if (failed) { status.textContent = 'Could not load the bang list.'; return; }
    status.textContent = index.length
      ? (index.length.toLocaleString() + ' bangs, start typing to find one.')
      : 'No bangs available.';
  }

  function render(q) {
    q = q.trim().toLowerCase().replace(/^!+/, '');
    results.textContent = '';
    if (!index) return;
    if (!q) { settle(); return; }
    if (failed) { status.textContent = 'Could not load the bang list.'; return; }

    var matches = [];
    for (var i = 0; i < index.length; i++) {
      var e = index[i], p = e.t.indexOf(q), score;
      if (e.t === q) score = 0;
      else if (p === 0) score = 1;
      else if (p > 0) score = 2;
      else if (e.h && e.h.indexOf(q) !== -1) score = 3;
      else continue;
      matches.push({ e: e, s: score });
    }

    if (!matches.length) {
      status.textContent = 'No bangs match “' + q + '”.';
      return;
    }

    // Best score first; then shorter (closer) triggers; then alphabetical.
    matches.sort(function (a, b) {
      if (a.s !== b.s) return a.s - b.s;
      if (a.e.t.length !== b.e.t.length) return a.e.t.length - b.e.t.length;
      return a.e.t < b.e.t ? -1 : (a.e.t > b.e.t ? 1 : 0);
    });

    var frag = document.createDocumentFragment();
    matches.slice(0, MAX).forEach(function (m) {
      var e = m.e;
      var li = document.createElement('li');
      li.className = 'flex items-center gap-3 px-3 py-2.5';

      var code = document.createElement('code');
      code.className = CHIP;
      code.textContent = '!' + e.t;
      li.appendChild(code);

      var desc = document.createElement('span');
      desc.className = DESC;
      desc.title = e.u;
      desc.textContent = e.h || e.u;
      li.appendChild(desc);

      if (e.c) {
        var badge = document.createElement('span');
        badge.className = BADGE;
        badge.textContent = 'custom';
        li.appendChild(badge);
      }
      frag.appendChild(li);
    });
    results.appendChild(frag);

    var total = matches.length;
    if (total > MAX) {
      status.textContent = 'Showing ' + MAX + ' of ' + total.toLocaleString() + ' matches, keep typing to narrow it down.';
    } else {
      status.textContent = total === 1 ? '1 match.' : total + ' matches.';
    }
  }

  function onInput() {
    var q = input.value;
    clearTimeout(debounce);
    debounce = setTimeout(function () {
      load().then(function () { render(q); });
    }, 120);
  }

  input.addEventListener('input', onInput);

  // No-JS users get a real submit button (rendered in <noscript>) that reloads
  // the page with ?bang=… for the server to answer. With JS we search live, so
  // intercept the submit and skip that round-trip.
  if (input.form) {
    input.form.addEventListener('submit', function (e) {
      e.preventDefault();
      load().then(function () { render(input.value); });
    });
  }

  // Warm the data the moment the user shows intent, so the first keystroke is
  // snappy; reflect the count (or an error) once it's ready and still idle.
  input.addEventListener('focus', function () {
    if (index) return;
    status.textContent = 'Loading bangs…';
    load().then(function () { if (!input.value.trim()) settle(); });
  }, { once: true });

  // Arrived with a server-rendered query (?bang=…)? Take it over so the list
  // stays live and consistently formatted from here on.
  if (input.value.trim()) {
    load().then(function () { render(input.value); });
  }
})();
