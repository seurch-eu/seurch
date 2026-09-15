"""Database helpers shared by the app's best-effort bookkeeping writes."""

from django.db import connection

# Bookkeeping (search counters, provider status) touches a single hot row per
# user/provider that every worker updates. Cap how long one such statement may
# block, so a contended row degrades the counter rather than hanging a worker
# until gunicorn's request timeout kills it.
STATEMENT_TIMEOUT_MS = 3000


def bound_statement_timeout():
    """Cap statement runtime for the current transaction (PostgreSQL only)."""
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SET LOCAL statement_timeout = %s', [STATEMENT_TIMEOUT_MS])
