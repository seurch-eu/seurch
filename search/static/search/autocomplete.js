/* Search-bar autocomplete, real suggestions via Brave suggest API */
(function () {
  // Suggestions are powered by Brave's suggest API and complete a web query, so
  // skip autocomplete entirely when the user switched Brave off for the Web
  // search type. Reads the same seurch_prefs cookie the theme bootstrap does.
  // (The /suggest/ endpoint also enforces this server-side, this just avoids
  // firing pointless requests.)
  try {
    var m = document.cookie.match(/(?:^|; )seurch_prefs=([^;]*)/);
    var prefs = m ? JSON.parse(decodeURIComponent(m[1])) : {};
    var off = (prefs.disabled_providers || {}).web || [];
    if (off.indexOf('brave') !== -1) return;
  } catch (e) {}

  var ICON = '<i class="fa-solid fa-magnifying-glass" aria-hidden="true"></i>';

  document.querySelectorAll('input[type=search]').forEach(function (inp) {
    var form = inp.closest('form');
    if (!form) return;
    form.style.position = 'relative';

    var uid = Math.random().toString(36).slice(2);
    var box = document.createElement('ul');
    box.className = 'ac-box';
    box.setAttribute('role', 'listbox');
    box.id = 'ac-list-' + uid;
    form.appendChild(box);

    inp.setAttribute('role', 'combobox');
    inp.setAttribute('aria-autocomplete', 'list');
    inp.setAttribute('aria-expanded', 'false');
    inp.setAttribute('aria-controls', box.id);

    var timer = null;
    var controller = null;
    var savedQuery = '';

    function getActive() {
      return box.querySelector('li.ac-active');
    }

    function selectItem(li) {
      var prev = getActive();
      if (prev) {
        prev.classList.remove('ac-active');
        prev.setAttribute('aria-selected', 'false');
      }
      if (li) {
        li.classList.add('ac-active');
        li.setAttribute('aria-selected', 'true');
        inp.setAttribute('aria-activedescendant', li.id);
        inp.value = li.textContent.trim();
      } else {
        inp.removeAttribute('aria-activedescendant');
        inp.value = savedQuery;
      }
    }

    function closeBox() {
      box.innerHTML = '';
      inp.setAttribute('aria-expanded', 'false');
      inp.removeAttribute('aria-activedescendant');
    }

    // Stop suggesting entirely: cancel a queued debounce, abort any in-flight
    // fetch, and close the dropdown. Used when the user commits to a search so
    // suggestions can't pop up (or flash open mid-navigation) afterwards.
    function stopSuggesting() {
      clearTimeout(timer);
      if (controller) controller.abort();
      closeBox();
    }

    function render(suggestions, q) {
      box.innerHTML = '';
      suggestions.forEach(function (s, idx) {
        var li = document.createElement('li');
        li.id = 'ac-item-' + uid + '-' + idx;
        li.setAttribute('role', 'option');
        li.setAttribute('aria-selected', 'false');
        var i = s.toLowerCase().indexOf(q.toLowerCase());
        var body = i >= 0
          ? s.slice(0, i) + '<strong>' + s.slice(i, i + q.length) + '</strong>' + s.slice(i + q.length)
          : s;
        li.innerHTML = ICON + '<span>' + body + '</span>';
        li.addEventListener('mousedown', function (e) {
          e.preventDefault();
          inp.value = s;
          stopSuggesting();
          form.submit();
        });
        box.appendChild(li);
      });
      inp.setAttribute('aria-expanded', suggestions.length ? 'true' : 'false');
    }

    function fetchSuggestions(q) {
      if (controller) controller.abort();
      controller = new AbortController();
      fetch('/suggest/?q=' + encodeURIComponent(q), { signal: controller.signal })
        .then(function (r) { return r.json(); })
        .then(function (data) { render(data.suggestions || [], q); })
        .catch(function () {});
    }

    function schedule(q) {
      clearTimeout(timer);
      closeBox();
      if (!q || q.length < 2) return;
      timer = setTimeout(function () { fetchSuggestions(q); }, 350);
    }

    inp.addEventListener('input', function () {
      savedQuery = inp.value.trim();
      schedule(savedQuery);
    });
    inp.addEventListener('focus', function () {
      savedQuery = inp.value.trim();
      schedule(savedQuery);
    });
    inp.addEventListener('blur', function () {
      setTimeout(function () { closeBox(); }, 200);
    });
    inp.addEventListener('keydown', function (e) {
      var items = box.querySelectorAll('li');
      var active = getActive();

      if (e.key === 'ArrowDown') {
        if (!items.length) return;
        e.preventDefault();
        // Past the last item → deselect and restore original query
        selectItem(active ? active.nextElementSibling : items[0]);
      } else if (e.key === 'ArrowUp') {
        if (!items.length) return;
        e.preventDefault();
        if (!active) {
          selectItem(items[items.length - 1]);
        } else if (active === items[0]) {
          // At first item → deselect and restore original query
          selectItem(null);
        } else {
          selectItem(active.previousElementSibling);
        }
      } else if (e.key === 'Escape') {
        // Restore original query and close
        inp.value = savedQuery;
        closeBox();
      } else if (e.key === 'Tab' && active) {
        // Accept highlighted suggestion without submitting
        e.preventDefault();
        inp.value = active.textContent.trim();
        savedQuery = inp.value;
        closeBox();
      }
    });

    // Committing to a search (Enter or clicking Search) must not leave a
    // suggestion fetch queued, otherwise the dropdown can flash open while the
    // results page is still loading.
    form.addEventListener('submit', stopSuggesting);
  });
})();
