from django.db import models


class CurrencyRate(models.Model):
    """Cached exchange-rate table.

    One row per base currency. ``rates`` maps ISO-4217 codes to the value of
    one unit of ``base``. Rows are refreshed from the upstream provider once a
    day (see ``instant.currency``); ``fetched_at`` drives the 24 h TTL.
    """

    base = models.CharField(max_length=3, unique=True)
    rates = models.JSONField()
    provider = models.CharField(max_length=32, default='')
    rate_date = models.CharField(max_length=10, default='')
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'instant_currency_rate'

    def __str__(self):
        return f'{self.base} ({len(self.rates)} rates)'


class InstantCache(models.Model):
    """Generic key/value cache for instant-answer lookups that hit the network.

    Used for weather (Open-Meteo) responses. Mirrors the TTL pattern used by
    ``search.SearchCache`` but kept in this app so the two stay decoupled.
    """

    cache_key = models.CharField(max_length=64, unique=True)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'instant_cache'
