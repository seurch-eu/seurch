"""Per-user blocked-site filtering."""

from urllib.parse import urlparse


def normalize_domain(value: str) -> str:
    """Reduce *value* (a URL or hostname) to a lowercase bare hostname.

    Strips scheme, port, path, and any leading ``www.`` so we compare apples
    to apples whether the user typed ``https://www.example.com/x`` or just
    ``example.com``.
    """
    value = (value or '').strip().lower()
    if not value:
        return ''
    if '://' not in value:
        value = 'http://' + value
    host = urlparse(value).hostname or ''
    return host.removeprefix('www.')


def blocked_domains_for(user) -> set[str]:
    """Return the set of normalized blocked domains for *user* (empty if anonymous)."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    from .models import BlockedSite
    return set(BlockedSite.objects.filter(user=user).values_list('domain', flat=True))


def _is_blocked(host: str, blocked: set[str]) -> bool:
    if not host:
        return False
    return host in blocked or any(host.endswith('.' + d) for d in blocked)


def filter_blocked(results, blocked: set[str]):
    """Strip results whose host (or a parent domain) is in *blocked*.

    Every result shape (web, image, news, video) carries the page it links to as
    ``url``, so that one field decides for all of them.
    """
    if not blocked or not results:
        return results
    return [r for r in results if not _is_blocked(normalize_domain(r.get('url', '')), blocked)]
