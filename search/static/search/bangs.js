/* Bang redirect, intercepts search form submits containing !trigger
 *
 * User-defined custom bangs (the #user-bangs JSON data block rendered by
 * base.html via {% json_script %} when the user is signed in) are checked
 * first and override the generic set.
 */
(function () {
  var bangsData = null;
  var loadPromise = null;

  function readUserBangs() {
    var el = document.getElementById('user-bangs');
    if (!el) return {};
    try { return JSON.parse(el.textContent) || {}; } catch (e) { return {}; }
  }

  var userBangs = readUserBangs();
  var BANG_RE  = /(^|\s)!([a-zA-Z0-9][a-zA-Z0-9._-]*)(\s|$)/;
  var BANG_REG = /(^|\s)!([a-zA-Z0-9][a-zA-Z0-9._-]*)(\s|$)/g;

  function getBangsUrl() {
    return (typeof window.BANGS_URL !== 'undefined')
      ? window.BANGS_URL
      : '/static/search/bangs.min.json';
  }

  function loadBangs() {
    if (bangsData) return Promise.resolve(bangsData);
    if (loadPromise) return loadPromise;
    loadPromise = fetch(getBangsUrl())
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (d) { bangsData = d; return d; })
      .catch(function () { bangsData = {}; return {}; });
    return loadPromise;
  }

  function parseBang(query) {
    var m = query.match(BANG_RE);
    if (!m) return null;
    return {
      trigger: m[2].toLowerCase(),
      terms: query.replace(BANG_REG, ' ').trim()
    };
  }

  function lookup(trigger, generic) {
    if (Object.prototype.hasOwnProperty.call(userBangs, trigger)) return userBangs[trigger];
    return generic[trigger];
  }

  function handleSubmit(e) {
    var form = e.currentTarget;
    var inp = form.querySelector('input[name=q]');
    if (!inp) return;
    var q = inp.value.trim();
    if (q.indexOf('!') === -1) return;
    var parsed = parseBang(q);
    if (!parsed) return;

    // User bang hits, redirect without waiting on the generic bangs fetch.
    if (Object.prototype.hasOwnProperty.call(userBangs, parsed.trigger)) {
      e.preventDefault();
      window.location.href = userBangs[parsed.trigger].replace(/\{\{\{s\}\}\}/g, encodeURIComponent(parsed.terms));
      return;
    }

    e.preventDefault();

    function doRedirect(data) {
      var tpl = lookup(parsed.trigger, data);
      if (tpl) {
        window.location.href = tpl.replace(/\{\{\{s\}\}\}/g, encodeURIComponent(parsed.terms));
      } else {
        form.removeEventListener('submit', handleSubmit);
        form.submit();
      }
    }

    if (bangsData) {
      doRedirect(bangsData);
    } else {
      loadBangs().then(doRedirect);
    }
  }

  loadBangs();

  document.querySelectorAll('form[role=search]').forEach(function (form) {
    form.addEventListener('submit', handleSubmit);
  });
})();
