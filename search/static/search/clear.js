// Clear button for the search bar (search/templates/search/_search_clear.html).
(function () {
  document.querySelectorAll('[data-search-clear]').forEach(function (btn) {
    var form = btn.closest('form');
    var inp = form && form.querySelector('input[type=search]');
    if (!inp) return;

    // `hidden` and `flex` are toggled as a pair, rather than letting one class
    // list hold both, because two display utilities on the same element are
    // resolved by their order in the compiled stylesheet, not by the template.
    function sync() {
      var filled = inp.value !== '';
      btn.classList.toggle('hidden', !filled);
      btn.classList.toggle('flex', filled);
    }

    btn.addEventListener('click', function () {
      inp.value = '';
      sync();
      // Autocomplete listens for `input`, so tell it the query is gone; it
      // cancels any queued fetch and closes the dropdown for the old text.
      inp.dispatchEvent(new Event('input', { bubbles: true }));
      inp.focus();
    });

    inp.addEventListener('input', sync);
    // The results page arrives with the query already in the field, so the
    // button's resting state can't be assumed empty. `pageshow` re-checks it
    // after a back/forward navigation, where the browser may have restored a
    // value into the field on its own, without an `input` event.
    window.addEventListener('pageshow', sync);
    sync();
  });
})();
