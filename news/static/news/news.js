(function () {
  var panel = document.getElementById('panel-news');
  if (!panel) return;

  function hide(img) { img.classList.add('hidden'); }

  // `error` doesn't bubble, so it's caught in the capture phase on the panel.
  // Delegating (rather than binding per image) also covers the thumbnails that
  // fail long after this runs: they load lazily, as the feed is scrolled.
  panel.addEventListener('error', function (e) {
    var img = e.target;
    if (img && img.tagName === 'IMG' && img.closest('[data-news-thumb]')) hide(img);
  }, true);

  // Thumbnails that already failed before this file ran: a finished image with
  // no intrinsic width never decoded.
  panel.querySelectorAll('[data-news-thumb] img').forEach(function (img) {
    if (img.complete && img.naturalWidth === 0) hide(img);
  });
})();
