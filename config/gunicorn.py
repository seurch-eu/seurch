"""Gunicorn logging hooks.

The Session-Link login URL (``/private/<token>/``, see
``accounts.models.SessionLink``) carries a password-equivalent token in the
GET path. The default access-log line would write that token to container
stdout on every use, so the access logger scrubs it before the
line is formatted.

Wired up via ``--logger-class config.gunicorn.RedactingLogger`` in the
Dockerfile CMD.
"""

import re

from gunicorn.glogging import Logger

# Make the token disappears from the request line
_SESSION_LINK_RE = re.compile(r'/private/[^/\s?#;"]+')


def redact(value):
    """Scrub Session-Link tokens from one access-log atom."""
    if not isinstance(value, str):
        return value
    return _SESSION_LINK_RE.sub('/private/[redacted]', value)


class RedactingLogger(Logger):
    def atoms(self, resp, req, environ, request_time):
        return {key: redact(value)
                for key, value in super().atoms(resp, req, environ, request_time).items()}
