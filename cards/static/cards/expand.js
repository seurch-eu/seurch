// Expands a knowledge card that was cropped to fit a phone screen.
(function () {
  // Swap the button's two labels (both shipped in the markup, so this file
  // needs no translated strings) and tell assistive tech which way it went.
  function setState(button, open) {
    button.setAttribute('aria-expanded', open ? 'true' : 'false');
    var more = button.querySelector('[data-card-more]');
    var less = button.querySelector('[data-card-less]');
    if (more) more.classList.toggle('hidden', open);
    if (less) less.classList.toggle('hidden', !open);
  }

  document.addEventListener('click', function (event) {
    var target = event.target;
    if (!target || !target.closest) return;

    // Card-level "Show more": reveal everything the mobile layout dropped.
    var button = target.closest('[data-card-toggle]');
    if (button) {
      var card = button.closest('[data-card]');
      if (!card) return;
      setState(button, card.classList.toggle('card-open'));
      return;
    }

    // Stack Exchange answers keep their own toggle: the answer body is a block
    // of sanitized HTML clamped by height, not one of the parts the card hides
    // on mobile, and its link is a real anchor to the answer on the source site
    // (which is what a no-JS visitor gets), so it's intercepted, not replaced.
    var readMore = target.closest('[data-se-readmore]');
    if (readMore) {
      var block = readMore.closest('[data-se-block]');
      var body = block && block.querySelector('[data-se-answer]');
      if (!body) return;  // no expandable body → leave the link as a plain href
      event.preventDefault();
      setState(readMore, !body.classList.toggle('se-answer--clamp'));
    }
  });
})();
