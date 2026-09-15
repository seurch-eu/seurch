(function () {
  var frame = document.getElementById('map-frame');
  if (!frame) return;
  var osmLink = document.getElementById('map-osm-link');
  var dirLink = document.getElementById('map-directions');
  var results = document.querySelectorAll('.map-result');

  var ua = navigator.userAgent || '';
  var isIOS = /iPhone|iPad|iPod/i.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
  var isMobile = isIOS || /Android|Mobile|BlackBerry|IEMobile|Opera Mini/i.test(ua);

  function setDirections(el) {
    if (!dirLink || !el) return;
    var geo = el.getAttribute('data-geo');
    var osm = el.getAttribute('data-directions');
    if (isIOS && geo) {
      // iOS has no geo: handler, open Apple Maps to the same coordinates.
      dirLink.href = 'https://maps.apple.com/?daddr=' + geo.slice(4).split('?')[0] + '&dirflg=d';
      dirLink.removeAttribute('target');
      dirLink.removeAttribute('rel');
    } else if (isMobile && geo) {
      dirLink.href = geo;
      dirLink.removeAttribute('target');  // hand off in place, no blank tab
      dirLink.removeAttribute('rel');
    } else if (osm) {
      dirLink.href = osm;  // desktop → OpenStreetMap web routing
    }
  }
  setDirections(dirLink);  // swap the initial link for the current platform

  results.forEach(function (btn) {
    btn.addEventListener('click', function () {
      var embed = btn.getAttribute('data-embed');
      if (embed) frame.src = embed;
      if (osmLink) osmLink.href = btn.getAttribute('data-osm') || osmLink.href;
      setDirections(btn);
      results.forEach(function (b) {
        b.classList.remove('map-result-active');
        b.setAttribute('aria-pressed', 'false');
      });
      btn.classList.add('map-result-active');
      btn.setAttribute('aria-pressed', 'true');
    });
  });
})();
