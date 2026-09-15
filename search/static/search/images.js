(function () {
  var lightbox = document.getElementById('img-lightbox');
  var panel = document.getElementById('panel-images');
  if (!lightbox || !panel) return;

  var bodyEl = document.getElementById('img-lightbox-body');
  var titleEl = document.getElementById('img-lightbox-title');
  var sourceEl = document.getElementById('img-lightbox-source');
  var imgEl = document.getElementById('img-lightbox-img');
  var visitEl = document.getElementById('img-lightbox-visit');
  var openEl = document.getElementById('img-lightbox-open');
  var closeBtn = document.getElementById('img-lightbox-close');
  var gridEl = document.getElementById('img-lightbox-similar');
  var moreEl = document.getElementById('img-lightbox-more');
  var simTpl = document.getElementById('img-similar-tpl');
  var skelTpl = document.getElementById('img-skeleton-tpl');

  var similarUrl = lightbox.getAttribute('data-similar-url');
  var lang = lightbox.getAttribute('data-lang') || '';
  var safe = lightbox.getAttribute('data-safe') || '';
  // Providers the grid was scoped to (the results-page provider picker), so the
  // similar-image search hits the same ones. Empty unless the user narrowed it.
  var scope = (lightbox.getAttribute('data-scope') || '').split(',').filter(Boolean);
  // The similar-images grid is opt-out (Settings -> General): when off, the
  // template omits the grid (and its templates) entirely, so the lightbox still
  // opens for the image and its visit/open links but skips the lookup.
  var similarEnabled = gridEl && lightbox.getAttribute('data-similar-enabled') !== '0';
  var loadingText = (gridEl && gridEl.getAttribute('data-loading')) || '';
  var emptyText = (gridEl && gridEl.getAttribute('data-empty')) || '';
  var SKELETON_TILES = 12;  // ~one screen of placeholders, matching the result cap

  var searchInput = document.querySelector('input[type="search"][name="q"]');
  var lastFocus = null;    // restore focus here on close
  var currentSeed = null;  // guards against a stale response landing late
  var cache = {};          // caption -> {query, images}, so reopening doesn't refetch

  function pageQuery() {
    if (searchInput && searchInput.value.trim()) return searchInput.value.trim();
    return new URLSearchParams(window.location.search).get('q') || '';
  }

  // The results page for a query, keeping the current filters and resetting to page 1.
  function searchUrl(query) {
    var url = new URL(window.location.href);
    url.searchParams.set('tab', 'images');
    url.searchParams.set('q', query);
    url.searchParams.delete('page');
    return url.toString();
  }

  function showMessage(text) {
    gridEl.textContent = '';
    gridEl.removeAttribute('aria-busy');
    if (!text) return;
    var p = document.createElement('p');
    p.className = 'col-span-full text-sm text-slate-500 dark:text-slate-400';
    p.textContent = text;
    gridEl.appendChild(p);
  }

  // Pulsing placeholder tiles while the similar images load, so the grid keeps
  // its shape instead of flashing a line of text (mirrors the knowledge-panel
  // skeleton). aria-busy marks the region loading; the tiles are decorative,
  // and loadingText rides along as a screen-reader-only status.
  function showSkeleton() {
    gridEl.textContent = '';
    gridEl.setAttribute('aria-busy', 'true');
    if (loadingText) {
      var sr = document.createElement('span');
      sr.className = 'sr-only';
      sr.textContent = loadingText;
      gridEl.appendChild(sr);
    }
    for (var i = 0; i < SKELETON_TILES; i++) {
      gridEl.appendChild(skelTpl.content.firstElementChild.cloneNode(true));
    }
  }

  function renderGrid(entry) {
    var images = (entry && entry.images) || [];
    gridEl.textContent = '';
    gridEl.removeAttribute('aria-busy');
    if (!images.length) { showMessage(emptyText); return; }
    images.forEach(function (im) {
      if (!im.thumb) return;
      var btn = simTpl.content.firstElementChild.cloneNode(true);
      var img = btn.querySelector('img');
      img.src = im.thumb;
      img.alt = im.title || '';
      if (im.title) btn.setAttribute('aria-label', im.title);
      btn.addEventListener('click', function () {
        focusImage({ title: im.title || '', source: im.source || '', page: im.url || '', img: im.thumb });
        bodyEl.scrollTop = 0;
      });
      gridEl.appendChild(btn);
    });
    if (entry.query) { moreEl.href = searchUrl(entry.query); moreEl.classList.remove('hidden'); }
  }

  function loadSimilar(title, excludeUrl) {
    if (!similarEnabled) return;  // feature opted out: no grid in the DOM
    var key = title || pageQuery();
    currentSeed = key;
    moreEl.classList.add('hidden');
    if (cache[key]) { renderGrid(cache[key]); return; }
    showSkeleton();
    var params = new URLSearchParams({
      q: title || '', query: pageQuery(), src: excludeUrl || '', lang: lang, safe: safe
    });
    scope.forEach(function (provider) { params.append('scope', provider); });
    fetch(similarUrl + '?' + params.toString(), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin'
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (currentSeed !== key) return;  // a different image was focused meanwhile
        var entry = { query: (data && data.query) || '', images: (data && data.images) || [] };
        cache[key] = entry;
        renderGrid(entry);
      })
      .catch(function () { if (currentSeed === key) showMessage(emptyText); });
  }

  // Make `item` the focused image, either the initial open or a similar-thumbnail
  // click, then (re)load its similar images.
  function focusImage(item) {
    titleEl.textContent = item.title;
    titleEl.classList.toggle('hidden', !item.title);
    sourceEl.textContent = item.source;
    sourceEl.classList.toggle('hidden', !item.source);
    imgEl.src = item.img || '';
    imgEl.alt = item.title;
    visitEl.href = item.page || '#';
    openEl.href = item.img || '#';
    loadSimilar(item.title, item.page);
  }

  function openCard(card) {
    var thumb = card.querySelector('img');
    if (!thumb) return;
    lastFocus = document.activeElement;
    lightbox.classList.remove('hidden');
    lightbox.classList.add('flex');
    lightbox.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
    bodyEl.scrollTop = 0;
    closeBtn.focus();
    focusImage({
      title: (thumb.getAttribute('alt') || '').trim(),
      source: card.getAttribute('data-source') || '',
      page: card.getAttribute('data-page') || '',
      img: thumb.currentSrc || thumb.src
    });
  }

  function close() {
    lightbox.classList.add('hidden');
    lightbox.classList.remove('flex');
    lightbox.setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    currentSeed = null;
    if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();
  }

  // Open the lightbox instead of following the result's link (the link is the
  // no-JS fallback). Only the main image link (.js-img-open) is intercepted, so
  // the separate source badge still navigates straight to its page. Delegated
  // so it covers every result in the grid.
  panel.addEventListener('click', function (e) {
    var link = e.target.closest('a.js-img-open');
    if (!link || !panel.contains(link)) return;
    var card = link.closest('[data-img-card]');
    if (!card) return;
    e.preventDefault();
    openCard(card);
  });

  closeBtn.addEventListener('click', close);
  // Backdrop click (on the overlay itself, not the dialog card) closes.
  lightbox.addEventListener('click', function (e) { if (e.target === lightbox) close(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !lightbox.classList.contains('hidden')) close();
  });
})();
