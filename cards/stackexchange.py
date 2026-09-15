import logging
import re
import time
from html import escape, unescape
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from django.conf import settings

from cards.cache import cached_card
from cards.relevance import _is_programming_question, _name_is_relevant, _title_match
from search import health

logger = logging.getLogger(__name__)

STACKEXCHANGE_API_BASE = 'https://api.stackexchange.com/2.3'

# Length (visible characters) above which an answer is collapsed behind a
# "Read more" toggle rather than shown in full.
_LONG_ANSWER_LEN = 320

_TAG_RE = re.compile(r'<[^>]+>')

# Stack Exchange network sites we recognise from a result domain, mapped to
# their API `site` parameter and a human label. Any other `*.stackexchange.com`
# subdomain is derived on the fly (see `_se_site`).
_SE_SITES = {
    'stackoverflow.com': ('stackoverflow', 'Stack Overflow'),
    'superuser.com': ('superuser', 'Super User'),
    'serverfault.com': ('serverfault', 'Server Fault'),
    'askubuntu.com': ('askubuntu', 'Ask Ubuntu'),
    'mathoverflow.net': ('mathoverflow.net', 'MathOverflow'),
}


def _se_site(web_results):
    """Pick which Stack Exchange site to query from the search's own results."""
    for result in (web_results or [])[:8]:
        host = urlparse(result.get('url', '')).netloc.lower().removeprefix('www.')
        if host in _SE_SITES:
            return _SE_SITES[host]
        if host.endswith('.stackexchange.com'):
            sub = host[: -len('.stackexchange.com')]
            if sub and sub != 'meta':
                return sub, f'{sub.replace("-", " ").title()} Stack Exchange'
    return 'stackoverflow', 'Stack Overflow'


# HTML the answer body may keep. Everything else has its tags stripped (text is
# preserved); <script>/<style> are dropped entirely. No attributes survive
# except a validated http(s) href on <a>, so the sanitized result is safe to
# render with |safe and code/lists/formatting still come through.
_ALLOWED_TAGS = frozenset({
    'p', 'br', 'hr', 'pre', 'code', 'kbd', 'samp', 'b', 'strong', 'i', 'em',
    'u', 'ul', 'ol', 'li', 'blockquote', 'h1', 'h2', 'h3', 'h4', 'sub', 'sup',
    'a',
})
_DROP_CONTENT_TAGS = frozenset({'script', 'style'})
_VOID_TAGS = frozenset({'br', 'hr'})


class _AnswerSanitizer(HTMLParser):
    """Reduce Stack Exchange answer HTML to a safe, attribute-free subset."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_CONTENT_TAGS:
            self._skip = True
            return
        if self._skip or tag not in _ALLOWED_TAGS:
            return
        if tag == 'a':
            href = dict(attrs).get('href') or ''
            if href.startswith(('http://', 'https://')):
                self.out.append(
                    f'<a href="{escape(href, quote=True)}" target="_blank" '
                    'rel="noopener noreferrer nofollow">'
                )
            else:
                self.out.append('<a>')
        else:
            self.out.append(f'<{tag}>')

    def handle_startendtag(self, tag, attrs):
        if not self._skip and tag in _VOID_TAGS:
            self.out.append(f'<{tag}>')

    def handle_endtag(self, tag):
        if tag in _DROP_CONTENT_TAGS:
            self._skip = False
            return
        if self._skip or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        self.out.append(f'</{tag}>')

    def handle_data(self, data):
        if not self._skip:
            self.out.append(escape(data))


def _sanitize_html(html):
    """Return a safe HTML subset of an answer body (see `_AnswerSanitizer`)."""
    parser = _AnswerSanitizer()
    parser.feed(html or '')
    parser.close()
    return ''.join(parser.out).strip()


def _visible_length(html):
    """Approximate the rendered text length, ignoring tags."""
    return len(_TAG_RE.sub('', html))


def fetch_stackexchange(query, web_results=None, lang=''):
    """Return a Stack Exchange card dict for a programming question, or None."""
    return cached_card('stackexchange', query, lang, lambda: _fetch_stackexchange(
        query, web_results=web_results, lang=lang,
    ))


def _fetch_stackexchange(query, web_results=None, lang=''):
    """Fetch a Stack Exchange card from the API (uncached).

    The card mirrors whichever SE-network site surfaced for the query (Stack
    Overflow, Super User, Ask Ubuntu, …) and shows that question's top-voted
    (accepted, when available) answer with its code and formatting preserved.

    Stack Exchange content is overwhelmingly English, so the card is offered
    for any detected language. 
    """
    if not _is_programming_question(query, web_results):
        return None

    site, site_name = _se_site(web_results)
    api_key = settings.STACKEXCHANGE_API_KEY
    params = {
        'site': site,
        'sort': 'relevance',
        'order': 'desc',
        'pagesize': 5,
        'q': query,
    }
    if api_key:
        params['key'] = api_key

    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=8) as client:
            search_r = client.get(f'{STACKEXCHANGE_API_BASE}/search/advanced', params=params)
            search_r.raise_for_status()
            health.record_ok('stackexchange')
            items = search_r.json().get('items', [])

            # Rank the pertinent hits by how closely the title matches the query
            # (tie-broken by votes) and take the best.
            scored = [
                (_title_match(q.get('title', ''), query), q.get('score', 0) or 0, q)
                for q in items
                if _name_is_relevant(q.get('title', ''), query)
            ]
            if not scored:
                logger.debug('stackexchange drop: no pertinent hit for query=%r', query)
                return None
            question = max(scored, key=lambda s: (s[0], s[1]))[2]

            answer = None
            if question.get('answer_count'):
                answer_params = {
                    'site': site,
                    'filter': 'withbody',
                    'sort': 'votes',
                    'order': 'desc',
                    'pagesize': 1,
                }
                if api_key:
                    answer_params['key'] = api_key
                answer_r = client.get(
                    f'{STACKEXCHANGE_API_BASE}/questions/{question["question_id"]}/answers',
                    params=answer_params,
                )
                answer_r.raise_for_status()
                answer_items = answer_r.json().get('items', [])
                if answer_items:
                    a = answer_items[0]
                    body = _sanitize_html(a.get('body', ''))
                    answer = {
                        'body': body,
                        'is_long': _visible_length(body) > _LONG_ANSWER_LEN,
                        'score': a.get('score', 0),
                        'is_accepted': a.get('is_accepted', False),
                        'url': a.get('link') or question.get('link', ''),
                    }

        title = unescape(question.get('title', ''))
        logger.debug('stackexchange ok site=%s title=%r answers=%d (%.2fs)',
                     site, title, question.get('answer_count', 0), time.monotonic() - t0)
        return {
            'title': title,
            'url': question.get('link', ''),
            'site_name': site_name,
            'score': question.get('score', 0),
            'answer_count': question.get('answer_count', 0),
            'view_count': question.get('view_count', 0),
            'is_answered': question.get('is_answered', False),
            'tags': question.get('tags', [])[:5],
            'answer': answer,
        }
    except Exception as exc:
        logger.warning('stackexchange error (%.2fs): %s', time.monotonic() - t0, exc)
        health.record_down('stackexchange', str(exc))
        return None
