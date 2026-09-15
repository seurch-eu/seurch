"""Template tag that emits an ``<img>`` source for an external image URL."""

from django import template

from ..imageproxy import must_proxy as _must_proxy
from ..imageproxy import proxify as _proxify

register = template.Library()


@register.simple_tag(takes_context=True)
def img_src(context, url):
    """Resolve an external image URL to what its ``<img src>`` should be."""
    if context.get('proxy_images') or _must_proxy(url):
        return _proxify(url)
    return url or ''
