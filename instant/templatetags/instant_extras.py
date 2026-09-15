"""Template filters that localise cached weather values at render time.

The weather instant answer is cached in ``InstantCache`` (a JSON column) keyed
by the *search* language, which is independent of the viewer's UI language. So
the cache stores only JSON-safe primitives, the WMO weather code and the
weekday index, and these filters resolve them to the active UI language when
the page is rendered.
"""

from django import template
from django.utils.dates import WEEKDAYS_ABBR

from ..data import weather_label

register = template.Library()


@register.filter
def weather_condition(code):
    """Localised condition text for a WMO weather code (e.g. 'Partly cloudy')."""
    try:
        return weather_label(int(code))[0]
    except (TypeError, ValueError):
        return ''


@register.filter
def weekday_abbr(index):
    """Localised abbreviated weekday name for a Monday=0 index."""
    try:
        return WEEKDAYS_ABBR.get(int(index), '')
    except (TypeError, ValueError):
        return ''
