"""Per-key rate limits for the public API.

Two limits apply together (a request must satisfy both): a short **burst** cap
that shields the upstream search providers from a runaway client, and a longer
**sustained** cap on daily volume. Both are keyed on the authenticated user, so
each API key gets its own budget, and both rates are configurable via
``API_THROTTLE_BURST`` / ``API_THROTTLE_SUSTAINED`` (see settings).
"""

from rest_framework.throttling import UserRateThrottle


class BurstRateThrottle(UserRateThrottle):
    scope = 'burst'


class SustainedRateThrottle(UserRateThrottle):
    scope = 'sustained'
