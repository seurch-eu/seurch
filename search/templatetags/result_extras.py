"""Template filter for safely rendering search-result snippets."""

import html
import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

_STRONG_TAG = re.compile(r'<\s*(/?)\s*strong[^>]*>', re.IGNORECASE)


@register.filter
def snippet(value):
    """Render an engine-supplied snippet with safe ``<strong>`` highlighting."""
    if not value:
        return ''
    text = str(value)
    out = []
    pos = 0
    for match in _STRONG_TAG.finditer(text):
        out.append(escape(html.unescape(text[pos:match.start()])))
        out.append('</strong>' if match.group(1) else '<strong>')
        pos = match.end()
    out.append(escape(html.unescape(text[pos:])))
    return mark_safe(''.join(out))
