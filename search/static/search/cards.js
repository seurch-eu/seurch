// Lazy-loads the web-tab knowledge panel after the results have rendered, so a
// slow card never delays the answer the user searched for. The results page
// ships the panel as a skeleton placeholder carrying data-cards-url; here we
// fetch that endpoint and swap the cards in (and, for a place query with no
// instant answer yet, prepend the map quick-answer to the results column).
(function () {
  var panel = document.querySelector('aside.knowledge-panel[data-cards-url]');
  if (!panel) return;
  var url = panel.getAttribute('data-cards-url');

  fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' }, credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) {
      // On no payload (or no cards) drop the placeholder so the layout reclaims
      // the column, matching a server render that found nothing.
      if (!data || !data.panel) {
        panel.remove();
      } else {
        panel.removeAttribute('aria-busy');
        panel.removeAttribute('data-cards-url');
        panel.innerHTML = data.panel;
      }

      // The map quick-answer shares the single instant-answer slot, only inject
      // it when the results column doesn't already show one.
      if (data && data.instant) {
        var col = document.querySelector('.search-results');
        if (col && !col.querySelector('.ia-wrap')) {
          col.insertAdjacentHTML('afterbegin', data.instant);
        }
      }
    })
    .catch(function () { panel.remove(); });
})();
