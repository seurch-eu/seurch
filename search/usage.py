"""Per-user monthly search counters, shown as "Searches this month" in settings."""

import logging

from django.db import DatabaseError, transaction
from django.db.models import F
from django.utils import timezone

from .db import bound_statement_timeout
from .models import SearchCount

logger = logging.getLogger(__name__)


def record_search(user, count=1):
    """Add *count* to *user*'s counter for the current month. Best-effort."""
    count = int(count)
    if count <= 0:
        return
    now = timezone.now()
    try:
        with transaction.atomic():
            bound_statement_timeout()
            _, created = SearchCount.objects.get_or_create(
                user=user, year=now.year, month=now.month, defaults={'count': count},
            )
            if not created:
                SearchCount.objects.filter(
                    user=user, year=now.year, month=now.month,
                ).update(count=F('count') + count)
    except DatabaseError:
        logger.warning(
            'record_search skipped for user=%s (database error)',
            getattr(user, 'username', user), exc_info=True,
        )


def searches_this_month(user):
    """Number of searches *user* has performed this month. Best-effort; 0 on error."""
    now = timezone.now()
    try:
        with transaction.atomic():
            bound_statement_timeout()
            return SearchCount.objects.filter(
                user=user, year=now.year, month=now.month,
            ).values_list('count', flat=True).first() or 0
    except DatabaseError:
        logger.warning(
            'searches_this_month unavailable for user=%s (database error)',
            getattr(user, 'username', user), exc_info=True,
        )
        return 0
