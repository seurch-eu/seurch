/* Instant-answer widgets, vanilla JS, no dependencies.
   Each init() is guarded by the presence of its widget root, so only the
   single instant card on the page does any work. */
(function () {
  'use strict';
  var d = document;
  function $(s, r) { return (r || d).querySelector(s); }
  function $$(s, r) { return Array.prototype.slice.call((r || d).querySelectorAll(s)); }
  function debounce(fn, ms) {
    var t;
    return function () { var a = arguments, c = this; clearTimeout(t); t = setTimeout(function () { fn.apply(c, a); }, ms); };
  }
  function round6(n) { return Math.round(n * 1e6) / 1e6; }
  function secureRand() { var a = new Uint32Array(1); crypto.getRandomValues(a); return a[0] / 4294967296; }
  function fmtRate(n) {
    if (!isFinite(n)) return '–';
    return n.toLocaleString('en-US', { maximumFractionDigits: 4 });
  }

  // --- clipboard (event-delegated) -----------------------------------------
  function flashCopied(btn) { btn.classList.add('ia-copied'); setTimeout(function () { btn.classList.remove('ia-copied'); }, 1100); }
  function copyText(text, btn) {
    if (!text) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { if (btn) flashCopied(btn); }, function () { fallbackCopy(text, btn); });
    } else { fallbackCopy(text, btn); }
  }
  function fallbackCopy(text, btn) {
    var ta = d.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    d.body.appendChild(ta); ta.focus(); ta.select();
    try { d.execCommand('copy'); if (btn) flashCopied(btn); } catch (e) { /* ignore */ }
    d.body.removeChild(ta);
  }
  d.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.ia-copy');
    if (!btn) return;
    var val = btn.getAttribute('data-ia-copy');
    if (val === null) {
      var sel = btn.getAttribute('data-ia-copy-target');
      if (sel) {
        var sec = btn.closest('[data-ia-type]');
        var el = sec && sec.querySelector(sel);
        val = el ? (el.value !== undefined ? el.value : el.textContent) : '';
      } else {
        var prev = btn.previousElementSibling;
        val = prev ? prev.textContent.trim() : '';
      }
    }
    copyText(val, btn);
  });

  // --- colour maths --------------------------------------------------------
  function rgb2hsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    var mx = Math.max(r, g, b), mn = Math.min(r, g, b), h, s, l = (mx + mn) / 2;
    if (mx === mn) { h = s = 0; } else {
      var dd = mx - mn;
      s = l > 0.5 ? dd / (2 - mx - mn) : dd / (mx + mn);
      if (mx === r) h = (g - b) / dd + (g < b ? 6 : 0);
      else if (mx === g) h = (b - r) / dd + 2;
      else h = (r - g) / dd + 4;
      h /= 6;
    }
    return [Math.round(h * 360), Math.round(s * 100), Math.round(l * 100)];
  }
  function hsl2rgb(h, s, l) {
    h /= 360; s /= 100; l /= 100;
    function hue(p, q, t) { t = ((t % 1) + 1) % 1; if (t < 1 / 6) return p + (q - p) * 6 * t; if (t < 1 / 2) return q; if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6; return p; }
    var r, g, b;
    if (s === 0) { r = g = b = l; } else {
      var q = l < 0.5 ? l * (1 + s) : l + s - l * s, p = 2 * l - q;
      r = hue(p, q, h + 1 / 3); g = hue(p, q, h); b = hue(p, q, h - 1 / 3);
    }
    return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
  }
  function hex2(x) { return ('0' + x.toString(16)).slice(-2); }

  // --- currency ------------------------------------------------------------
  function initCurrency() {
    var root = $('[data-ia-currency]'); if (!root) return;
    var dataEl = $('#ia-currency-data'); if (!dataEl) return;
    var data = JSON.parse(dataEl.textContent), rates = data.rates, meta = data.meta;
    var card = root.closest('[data-ia-type]');
    var codes = Object.keys(rates).sort();
    var fromSel = $('[data-ia-from]', root), toSel = $('[data-ia-to]', root);
    var amtIn = $('[data-ia-amount]', root), toIn = $('[data-ia-to-amount]', root);
    function opt(c) { var m = meta[c] || {}; return '<option value="' + c + '">' + (m.flag ? m.flag + ' ' : '') + c + '</option>'; }
    fromSel.innerHTML = codes.map(opt).join(''); toSel.innerHTML = codes.map(opt).join('');
    fromSel.value = root.getAttribute('data-from'); toSel.value = root.getAttribute('data-to');
    function conv(a, f, t) { return a * rates[t] / rates[f]; }
    function quotes() {
      $$('[data-ia-quote]', card).forEach(function (chip) {
        var c = chip.getAttribute('data-ia-quote'), v = $('[data-ia-quote-val]', chip);
        if (v && rates[c] !== undefined) v.textContent = fmtRate(conv(1, fromSel.value, c));
      });
    }
    function fromChanged() {
      var a = parseFloat(amtIn.value);
      toIn.value = isNaN(a) ? '' : round6(conv(a, fromSel.value, toSel.value));
      quotes();
    }
    function toChanged() {
      var a = parseFloat(toIn.value);
      amtIn.value = isNaN(a) ? '' : round6(conv(a, toSel.value, fromSel.value));
      quotes();
    }
    amtIn.addEventListener('input', fromChanged);
    fromSel.addEventListener('change', fromChanged);
    toSel.addEventListener('change', fromChanged);
    toIn.addEventListener('input', toChanged);
    $('[data-ia-swap]', root).addEventListener('click', function () {
      var f = fromSel.value; fromSel.value = toSel.value; toSel.value = f; fromChanged();
    });
    quotes();
  }

  // --- units ---------------------------------------------------------------
  function initUnits() {
    var root = $('[data-ia-units]'); if (!root) return;
    var data = JSON.parse($('#ia-unit-data').textContent);
    var isTemp = data.temperature, byKey = {};
    data.units.forEach(function (u) { byKey[u.key] = u; });
    var fromSel = $('[data-ia-from]', root), toSel = $('[data-ia-to]', root);
    var amtIn = $('[data-ia-amount]', root), toIn = $('[data-ia-to-amount]', root);
    function opt(u) { return '<option value="' + u.key + '">' + u.symbol + '</option>'; }
    fromSel.innerHTML = data.units.map(opt).join(''); toSel.innerHTML = data.units.map(opt).join('');
    fromSel.value = root.getAttribute('data-from'); toSel.value = root.getAttribute('data-to');
    function tempConv(v, f, t) {
      var c = f === 'c' ? v : f === 'f' ? (v - 32) * 5 / 9 : v - 273.15;
      return t === 'c' ? c : t === 'f' ? c * 9 / 5 + 32 : c + 273.15;
    }
    function conv(v, f, t) { return isTemp ? tempConv(v, f, t) : v * byKey[f].factor / byKey[t].factor; }
    function fromChanged() { var a = parseFloat(amtIn.value); toIn.value = isNaN(a) ? '' : round6(conv(a, fromSel.value, toSel.value)); }
    function toChanged() { var a = parseFloat(toIn.value); amtIn.value = isNaN(a) ? '' : round6(conv(a, toSel.value, fromSel.value)); }
    amtIn.addEventListener('input', fromChanged);
    fromSel.addEventListener('change', fromChanged);
    toSel.addEventListener('change', fromChanged);
    toIn.addEventListener('input', toChanged);
  }

  // --- colour --------------------------------------------------------------
  function initColor() {
    var root = $('[data-ia-color]'); if (!root) return;
    var swatch = $('[data-ia-swatch]', root), native = $('[data-ia-native]', root),
      hexI = $('[data-ia-hex]', root), rgbI = $('[data-ia-rgb]', root), hslI = $('[data-ia-hsl]', root);
    function apply(r, g, b, src) {
      r = Math.max(0, Math.min(255, Math.round(r)));
      g = Math.max(0, Math.min(255, Math.round(g)));
      b = Math.max(0, Math.min(255, Math.round(b)));
      var hex = '#' + (hex2(r) + hex2(g) + hex2(b)).toUpperCase(), hsl = rgb2hsl(r, g, b);
      swatch.style.background = hex; native.value = hex;
      if (src !== 'hex') hexI.value = hex;
      if (src !== 'rgb') rgbI.value = 'rgb(' + r + ', ' + g + ', ' + b + ')';
      if (src !== 'hsl') hslI.value = 'hsl(' + hsl[0] + ', ' + hsl[1] + '%, ' + hsl[2] + '%)';
    }
    hexI.addEventListener('input', function () {
      var m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hexI.value.trim()); if (!m) return;
      var h = m[1]; if (h.length === 3) h = h.split('').map(function (c) { return c + c; }).join('');
      apply(parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), 'hex');
    });
    native.addEventListener('input', function () {
      var h = native.value.slice(1);
      apply(parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16), null);
    });
    rgbI.addEventListener('input', function () {
      var m = /(\d+)\D+(\d+)\D+(\d+)/.exec(rgbI.value); if (!m) return;
      apply(+m[1], +m[2], +m[3], 'rgb');
    });
    hslI.addEventListener('input', function () {
      var m = /(\d+)\D+(\d+)\D+(\d+)/.exec(hslI.value); if (!m) return;
      var rgb = hsl2rgb(+m[1], +m[2], +m[3]); apply(rgb[0], rgb[1], rgb[2], 'hsl');
    });
  }

  // --- base ----------------------------------------------------------------
  function initBase() {
    var root = $('[data-ia-base]'); if (!root) return;
    var inp = $('[data-ia-base-input]', root), baseSel = $('[data-ia-base-from]', root);
    var outs = { 10: $('[data-ia-out="10"]', root), 16: $('[data-ia-out="16"]', root), 2: $('[data-ia-out="2"]', root), 8: $('[data-ia-out="8"]', root) };
    function update() {
      var raw = inp.value.trim().replace(/^0x|^0b|^0o/i, ''), base = parseInt(baseSel.value, 10);
      var v = parseInt(raw, base);
      if (isNaN(v)) { Object.keys(outs).forEach(function (k) { outs[k].textContent = '-'; }); return; }
      outs[10].textContent = v.toLocaleString('en-US');
      outs[16].textContent = '0x' + v.toString(16).toUpperCase();
      outs[2].textContent = v.toString(2);
      outs[8].textContent = '0o' + v.toString(8);
    }
    inp.addEventListener('input', update);
    baseSel.addEventListener('change', update);
  }

  // --- encode / decode -----------------------------------------------------
  function initEncode() {
    var root = $('[data-ia-encode]'); if (!root) return;
    var input = $('[data-ia-encode-input]', root), output = $('[data-ia-encode-output]', root);
    var kindTabs = $$('[data-ia-kind-tabs] .ia-tab', root), actionTabs = $$('[data-ia-action-tabs] .ia-tab', root);
    var state = { kind: root.getAttribute('data-kind'), action: root.getAttribute('data-action') };
    function b64e(s) { try { return btoa(unescape(encodeURIComponent(s))); } catch (e) { return '(cannot encode)'; } }
    function b64d(s) { try { return decodeURIComponent(escape(atob(s.trim()))); } catch (e) { return '(invalid Base64 input)'; } }
    function urld(t) { try { return decodeURIComponent(t.replace(/\+/g, ' ')); } catch (e) { return '(invalid URL encoding)'; } }
    function run() {
      var t = input.value, out;
      if (state.kind === 'url') out = state.action === 'decode' ? urld(t) : encodeURIComponent(t).replace(/%20/g, '+');
      else out = state.action === 'decode' ? b64d(t) : b64e(t);
      output.value = out;
    }
    function paint() {
      kindTabs.forEach(function (b) { b.classList.toggle('ia-active', b.getAttribute('data-kind') === state.kind); });
      actionTabs.forEach(function (b) { b.classList.toggle('ia-active', b.getAttribute('data-action') === state.action); });
    }
    kindTabs.forEach(function (b) { b.addEventListener('click', function () { state.kind = b.getAttribute('data-kind'); paint(); run(); }); });
    actionTabs.forEach(function (b) { b.addEventListener('click', function () { state.action = b.getAttribute('data-action'); paint(); run(); }); });
    input.addEventListener('input', run);
    paint();
  }

  // --- JSON ----------------------------------------------------------------
  function initJson() {
    var root = $('[data-ia-json]'); if (!root) return;
    var input = $('[data-ia-json-input]', root), output = $('[data-ia-json-output]', root),
      status = $('[data-ia-json-status]', root), errEl = $('[data-ia-json-error]', root);
    function process(indent) {
      var t = input.value.trim();
      if (!t) { output.value = ''; status.textContent = ''; errEl.hidden = true; input.classList.remove('ia-invalid'); return; }
      try {
        var obj = JSON.parse(t);
        output.value = JSON.stringify(obj, null, indent);
        status.textContent = '✓ Valid'; status.style.color = '#16a34a';
        errEl.hidden = true; input.classList.remove('ia-invalid');
      } catch (e) {
        status.textContent = '✗ Invalid'; status.style.color = '#dc2626';
        errEl.textContent = e.message; errEl.hidden = false; input.classList.add('ia-invalid');
      }
    }
    input.addEventListener('input', debounce(function () { process(2); }, 250));
    $('[data-ia-json-format]', root).addEventListener('click', function () { process(2); });
    $('[data-ia-json-minify]', root).addEventListener('click', function () { process(0); });
    if (input.value.trim()) process(2);
  }

  // --- regex ---------------------------------------------------------------
  function initRegex() {
    var root = $('[data-ia-regex]'); if (!root) return;
    var patI = $('[data-ia-regex-pattern]', root), strI = $('[data-ia-regex-string]', root),
      result = $('[data-ia-regex-result]', root), info = $('[data-ia-regex-info]', root),
      count = $('[data-ia-regex-count]', root), flagBoxes = $$('[data-ia-flag]', root);
    function esc(s) { return s.replace(/[&<>]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]; }); }
    function run() {
      var pat = patI.value, str = strI.value;
      var flags = flagBoxes.filter(function (b) { return b.checked; }).map(function (b) { return b.getAttribute('data-ia-flag'); }).join('');
      if (flags.indexOf('g') === -1) flags += 'g';
      if (!pat) { result.innerHTML = esc(str); info.textContent = ''; count.textContent = ''; patI.classList.remove('ia-invalid'); return; }
      var re;
      try { re = new RegExp(pat, flags); patI.classList.remove('ia-invalid'); info.style.color = ''; }
      catch (e) { info.textContent = e.message; info.style.color = '#dc2626'; patI.classList.add('ia-invalid'); count.textContent = ''; return; }
      var html = '', last = 0, m, n = 0, guard = 0;
      re.lastIndex = 0;
      while ((m = re.exec(str)) !== null) {
        html += esc(str.slice(last, m.index)) + '<mark>' + esc(m[0]) + '</mark>';
        last = m.index + m[0].length;
        n++;
        if (m[0] === '') re.lastIndex++;
        if (++guard > 10000) break;
      }
      html += esc(str.slice(last));
      result.innerHTML = html;
      count.textContent = n + ' match' + (n === 1 ? '' : 'es');
      info.textContent = n ? '' : 'No matches';
    }
    var live = debounce(run, 150);
    patI.addEventListener('input', live); strI.addEventListener('input', live);
    flagBoxes.forEach(function (b) { b.addEventListener('change', run); });
    run();
  }

  // --- hash ----------------------------------------------------------------
  function initHash() {
    var root = $('[data-ia-hash]'); if (!root) return;
    var input = $('[data-ia-hash-input]', root), url = root.getAttribute('data-hash-url');
    var outs = {};
    $$('[data-ia-hash-out]', root).forEach(function (el) { outs[el.getAttribute('data-ia-hash-out')] = el; });
    input.addEventListener('input', debounce(function () {
      fetch(url + '?text=' + encodeURIComponent(input.value))
        .then(function (r) { return r.json(); })
        .then(function (j) { Object.keys(outs).forEach(function (k) { if (j[k] !== undefined) outs[k].textContent = j[k]; }); })
        .catch(function () { /* offline: keep last */ });
    }, 300));
  }

  // --- QR ------------------------------------------------------------------
  function initQR() {
    var root = $('[data-ia-qr]'); if (!root) return;
    var text = $('[data-ia-qr-text]', root), box = $('[data-ia-qr-img]', root),
      dl = $('[data-ia-qr-download]', root), url = root.getAttribute('data-qr-url');
    function refreshDownload() {
      var svg = box.querySelector('svg'); if (!svg || !dl) return;
      var blob = new Blob([new XMLSerializer().serializeToString(svg)], { type: 'image/svg+xml' });
      if (dl._u) URL.revokeObjectURL(dl._u);
      dl._u = URL.createObjectURL(blob); dl.href = dl._u;
    }
    refreshDownload();
    text.addEventListener('input', debounce(function () {
      var t = text.value.trim(); if (!t) return;
      fetch(url + '?text=' + encodeURIComponent(t))
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (svg) { if (svg) { box.innerHTML = svg; refreshDownload(); } })
        .catch(function () { /* ignore */ });
    }, 350));
  }

  // --- UUID ----------------------------------------------------------------
  function uuid4() {
    if (crypto.randomUUID) return crypto.randomUUID();
    var b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    var h = Array.prototype.map.call(b, function (x) { return hex2(x); });
    return h.slice(0, 4).join('') + '-' + h.slice(4, 6).join('') + '-' + h.slice(6, 8).join('') + '-' + h.slice(8, 10).join('') + '-' + h.slice(10, 16).join('');
  }
  function initUuid() {
    var root = $('[data-ia-uuid]'); if (!root) return;
    var main = $('[data-ia-uuid-main]', root);
    $('[data-ia-uuid-regen]', root).addEventListener('click', function () { main.textContent = uuid4(); });
  }

  // --- password generator --------------------------------------------------
  var _PW_SETS = {
    lower: 'abcdefghijkmnopqrstuvwxyz', upper: 'ABCDEFGHJKLMNPQRSTUVWXYZ',
    digits: '23456789', symbols: '!@#$%^&*()-_=+[]{};:,.?',
  };
  function initPassword() {
    var root = $('[data-ia-password]'); if (!root) return;
    var val = $('[data-ia-pw-value]', root), lenIn = $('[data-ia-pw-length]', root),
      lenVal = $('[data-ia-pw-length-val]', root), meter = $('[data-ia-pw-meter]', root),
      strength = $('[data-ia-pw-strength]', root), opts = $$('[data-ia-pw-opt]', root);
    function gen() {
      var pool = '';
      opts.forEach(function (o) { if (o.checked) pool += _PW_SETS[o.getAttribute('data-ia-pw-opt')]; });
      if (!pool) pool = _PW_SETS.lower + _PW_SETS.upper + _PW_SETS.digits;
      var len = parseInt(lenIn.value, 10), out = '', a = new Uint32Array(len);
      crypto.getRandomValues(a);
      for (var i = 0; i < len; i++) out += pool[a[i] % pool.length];
      val.textContent = out;
      lenVal.textContent = len;
      var bits = len * Math.log2(pool.length || 1);
      meter.style.width = Math.max(8, Math.min(100, Math.round(bits / 1.28))) + '%';
      var color = bits < 40 ? '#dc2626' : bits < 70 ? '#f59e0b' : '#16a34a';
      meter.style.background = color;
      if (strength) strength.textContent = (bits < 40 ? 'Weak' : bits < 70 ? 'Fair' : bits < 100 ? 'Strong' : 'Very strong') + ' · ' + Math.round(bits) + ' bits';
    }
    opts.forEach(function (o) { o.addEventListener('change', gen); });
    lenIn.addEventListener('input', gen);
    $('[data-ia-pw-regen]', root).addEventListener('click', gen);
    gen();
  }

  // --- unix timestamp ------------------------------------------------------
  function initTimestamp() {
    var root = $('[data-ia-timestamp]'); if (!root) return;
    var nowEl = $('[data-ia-ts-now]', root), input = $('[data-ia-ts-input]', root),
      utc = $('[data-ia-ts-utc]', root), local = $('[data-ia-ts-local]', root),
      rel = $('[data-ia-ts-rel]', root), dateIn = $('[data-ia-ts-date]', root),
      out = $('[data-ia-ts-out]', root);
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function tick() { nowEl.textContent = Math.floor(Date.now() / 1000); }
    tick(); setInterval(tick, 1000);
    function toSecs(v) { v = parseInt(v, 10); if (isNaN(v)) return null; return v >= 1e12 ? Math.floor(v / 1000) : v; }
    function relText(secs) {
      var diff = secs - Date.now() / 1000, a = Math.abs(diff), n, u;
      if (a < 60) { n = Math.round(a); u = 'second'; }
      else if (a < 3600) { n = Math.round(a / 60); u = 'minute'; }
      else if (a < 86400) { n = Math.round(a / 3600); u = 'hour'; }
      else if (a < 2592000) { n = Math.round(a / 86400); u = 'day'; }
      else if (a < 31536000) { n = Math.round(a / 2592000); u = 'month'; }
      else { n = Math.round(a / 31536000); u = 'year'; }
      var p = n + ' ' + (n === 1 ? u : u + 's');
      return diff < 0 ? p + ' ago' : 'in ' + p;
    }
    function fromInput() {
      var secs = toSecs(input.value.trim());
      if (secs === null) { utc.textContent = local.textContent = rel.textContent = '–'; return; }
      var d = new Date(secs * 1000);
      if (isNaN(d.getTime())) { utc.textContent = 'invalid'; local.textContent = rel.textContent = '–'; return; }
      utc.textContent = d.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC');
      local.textContent = d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
        pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
      rel.textContent = relText(secs);
    }
    if (!input.value.trim()) input.value = Math.floor(Date.now() / 1000);
    fromInput();
    input.addEventListener('input', fromInput);
    function fromDate() {
      if (!dateIn.value) { out.value = ''; return; }
      var d = new Date(dateIn.value);
      out.value = isNaN(d.getTime()) ? '' : Math.floor(d.getTime() / 1000);
    }
    dateIn.addEventListener('input', fromDate);
  }

  // --- random / dice / coin ------------------------------------------------
  function initRandom() {
    var root = $('[data-ia-random]'); if (!root) return;
    var val = $('[data-ia-random-value]', root), minI = $('[data-ia-random-min]', root),
      maxI = $('[data-ia-random-max]', root), meta = $('.ia-head-meta', root);
    function roll() {
      var lo = parseInt(minI.value, 10), hi = parseInt(maxI.value, 10);
      if (isNaN(lo) || isNaN(hi)) return;
      if (lo > hi) { var t = lo; lo = hi; hi = t; }
      val.textContent = (lo + Math.floor(secureRand() * (hi - lo + 1))).toLocaleString('en-US');
      if (meta) meta.textContent = lo + ' – ' + hi;
    }
    $('[data-ia-random-again]', root).addEventListener('click', roll);
    minI.addEventListener('change', roll); maxI.addEventListener('change', roll);
  }
  function initDice() {
    var root = $('[data-ia-dice]'); if (!root) return;
    var faces = $('[data-ia-dice-faces]', root), total = $('[data-ia-dice-total]', root);
    var count = parseInt(root.getAttribute('data-count'), 10) || 1, sides = parseInt(root.getAttribute('data-sides'), 10) || 6;
    $('[data-ia-dice-roll]', root).addEventListener('click', function () {
      var sum = 0, html = '';
      for (var i = 0; i < count; i++) { var r = 1 + Math.floor(secureRand() * sides); sum += r; html += '<span class="ia-die">' + r + '</span>'; }
      faces.innerHTML = html; total.textContent = sum;
    });
  }
  function initCoin() {
    var root = $('[data-ia-coin]'); if (!root) return;
    var face = $('[data-ia-coin-face]', root);
    $('[data-ia-coin-flip]', root).addEventListener('click', function () {
      face.classList.add('ia-flipping');
      setTimeout(function () { face.textContent = secureRand() < 0.5 ? 'Heads' : 'Tails'; }, 300);
      setTimeout(function () { face.classList.remove('ia-flipping'); }, 600);
    });
  }

  // --- world clock ---------------------------------------------------------
  function initClock() {
    var root = $('[data-ia-clock]'); if (!root) return;
    var tz = root.getAttribute('data-tz'), timeEl = $('[data-ia-clock-time]', root), dateEl = $('[data-ia-clock-date]', root);
    var timeFmt, dateFmt;
    try {
      timeFmt = new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
      dateFmt = new Intl.DateTimeFormat('en-GB', { timeZone: tz, weekday: 'long', day: '2-digit', month: 'long', year: 'numeric' });
    } catch (e) { return; }
    function tick() { var now = new Date(); timeEl.textContent = timeFmt.format(now); dateEl.textContent = dateFmt.format(now); }
    tick(); setInterval(tick, 1000);
  }

  // --- timer / stopwatch ---------------------------------------------------
  function initTimer() {
    var root = $('[data-ia-timer]'); if (!root) return;
    var display = $('[data-ia-timer-display]', root), tabs = $$('[data-ia-timer-tabs] .ia-tab', root),
      presets = $('[data-ia-timer-presets]', root), startB = $('[data-ia-timer-start]', root),
      pauseB = $('[data-ia-timer-pause]', root), resetB = $('[data-ia-timer-reset]', root);
    var mode = root.getAttribute('data-mode') || 'timer';
    var initial = parseInt(root.getAttribute('data-seconds'), 10) || 0;
    var remaining = initial, elapsed = 0, running = false, handle = null, endAt = 0, startAt = 0;
    function pad(n) { return (n < 10 ? '0' : '') + n; }
    function fmt(s) { s = Math.max(0, Math.round(s)); var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60; return (h > 0 ? pad(h) + ':' : '') + pad(m) + ':' + pad(sec); }
    function render() { display.textContent = fmt(mode === 'timer' ? remaining : elapsed); }
    function stop() { running = false; if (handle) { clearInterval(handle); handle = null; } startB.hidden = false; pauseB.hidden = true; }
    function tick() {
      var now = Date.now() / 1000;
      if (mode === 'timer') { remaining = endAt - now; if (remaining <= 0) { remaining = 0; stop(); display.classList.add('ia-done'); beep(); } }
      else { elapsed = now - startAt; }
      render();
    }
    function start() {
      if (running) return;
      if (mode === 'timer') { if (remaining <= 0) return; endAt = Date.now() / 1000 + remaining; }
      else { startAt = Date.now() / 1000 - elapsed; }
      running = true; handle = setInterval(tick, 200);
      startB.hidden = true; pauseB.hidden = false; display.classList.remove('ia-done');
    }
    function reset() { stop(); remaining = initial; elapsed = 0; display.classList.remove('ia-done'); render(); }
    function setMode(m) {
      mode = m; stop();
      tabs.forEach(function (b) { b.classList.toggle('ia-active', b.getAttribute('data-mode') === m); });
      presets.hidden = m !== 'timer';
      if (m === 'timer') remaining = initial; else elapsed = 0;
      display.classList.remove('ia-done'); render();
    }
    function beep() {
      try {
        var C = window.AudioContext || window.webkitAudioContext; if (!C) return;
        var ctx = new C(), o = ctx.createOscillator(), g = ctx.createGain();
        o.connect(g); g.connect(ctx.destination); o.frequency.value = 880;
        g.gain.setValueAtTime(0.2, ctx.currentTime);
        g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 1);
        o.start(); o.stop(ctx.currentTime + 1);
      } catch (e) { /* ignore */ }
    }
    tabs.forEach(function (b) { b.addEventListener('click', function () { setMode(b.getAttribute('data-mode')); }); });
    $$('[data-secs]', presets).forEach(function (b) {
      b.addEventListener('click', function () { stop(); initial = parseInt(b.getAttribute('data-secs'), 10); remaining = initial; display.classList.remove('ia-done'); render(); });
    });
    startB.addEventListener('click', start);
    pauseB.addEventListener('click', stop);
    resetB.addEventListener('click', reset);
    setMode(mode);
  }

  function init() {
    initCurrency(); initUnits(); initColor(); initBase(); initEncode(); initJson();
    initRegex(); initHash(); initQR(); initUuid(); initRandom(); initDice(); initCoin();
    initClock(); initTimer(); initPassword(); initTimestamp();
  }
  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', init);
  else init();
})();
