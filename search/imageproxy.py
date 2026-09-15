"""Short-lived URL → token mapping behind the image proxy."""

import ipaddress
import socket
import uuid
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from django.urls import reverse
from django.utils import timezone

PROXY_TTL = timedelta(hours=24)

# Fixed namespace for deterministic, content-addressed tokens.
_NAMESPACE = uuid.UUID('1d3a4e9c-0b6f-4e2a-9c7d-3f8b2a1e6c40')

# Pixabay's API terms prohibit hotlinking, its images may not be referenced
# directly from a CDN URL in the page; they must be served from Seurch's own
# infrastructure.
_MUST_PERSIST_HOSTS = {'pixabay.com', 'cdn.pixabay.com'}


def _must_persist(host):
    return (host or '').lower() in _MUST_PERSIST_HOSTS


def must_proxy(url):
    """True when *url* has to be proxied regardless of the user's preference."""
    try:
        return _must_persist(urlparse(url).hostname)
    except ValueError:
        return False


def _blocked_ip(ip):
    """True for addresses the proxy must never touch: anything that is not a
    routable public address (private/loopback/link-local/reserved/CGNAT, via
    ``is_global``) plus multicast."""
    return not ip.is_global or ip.is_multicast


def _is_blocked_host(host):
    """True for hosts the proxy must never fetch (loopback / private / reserved)."""
    if not host:
        return True
    if host.lower() == 'localhost':
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return _blocked_ip(ip)


# Content types the proxy will pass through as-is. Anything else (an upstream
# answering with text/html, say) is served as an opaque download instead, so a
# hostile response can never execute on this app's origin.
_IMAGE_CONTENT_TYPES = {
    'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml',
    'image/avif', 'image/x-icon', 'image/vnd.microsoft.icon',
}


def safe_content_type(raw):
    """Upstream ``Content-Type`` → one that is safe to serve from this origin."""
    ctype = (raw or '').split(';')[0].strip().lower()
    return ctype if ctype in _IMAGE_CONTENT_TYPES else 'application/octet-stream'


def resolve_public_address(url):
    """Resolve *url*'s host at fetch time; return a public IP to connect to.

    ``None`` when the host is blocked, does not resolve, or when *any* resolved
    address is private/loopback/reserved: a name mixing public and private
    answers is treated as hostile (DNS rebinding), not half-usable. The caller
    connects to the returned address rather than re-resolving the name, closing
    the check-time/connect-time gap."""
    host = urlparse(url).hostname
    if _is_blocked_host(host):
        return None
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return None
    addresses = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None
        if _blocked_ip(ip):
            return None
        addresses.append(str(ip))
    return addresses[0] if addresses else None


def proxify(url):
    """Register ``url`` for proxying and return the local proxy path."""
    from search.models import ProxiedImage

    url = (url or '').strip()
    if not url:
        return ''
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        return ''
    if _is_blocked_host(parsed.hostname):
        return ''

    token = uuid.uuid5(_NAMESPACE, url)
    ProxiedImage.objects.update_or_create(
        id=token,
        defaults={
            'url': url,
            'expires_at': timezone.now() + PROXY_TTL,
            'persist': _must_persist(parsed.hostname),
        },
    )
    return f"{reverse('search:image_proxy')}?{urlencode({'id': str(token)})}"
