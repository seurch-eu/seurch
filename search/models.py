import uuid

from django.conf import settings
from django.db import models


class SearchCache(models.Model):
    cache_key = models.CharField(max_length=64, unique=True)
    results = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'search_cache'


class ProxiedImage(models.Model):
    """Short-lived token → external image URL mapping behind the image proxy."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    url = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    persist = models.BooleanField(default=False)
    content = models.BinaryField(null=True, blank=True)
    content_type = models.CharField(max_length=100, blank=True, default='')

    class Meta:
        db_table = 'search_proxied_image'


class CustomBang(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='custom_bangs')
    trigger = models.CharField(max_length=32)
    url_template = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'search_custom_bang'
        unique_together = ('user', 'trigger')
        ordering = ['trigger']


class BlockedSite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='blocked_sites')
    domain = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'search_blocked_site'
        unique_together = ('user', 'domain')
        ordering = ['domain']


class UserSettings(models.Model):
    """Cloud snapshot of a user's settings document (preferences + bangs +
    blocked sites), used for cross-device save/restore. One row per user."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='saved_settings')
    document = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'search_user_settings'


class SearchCount(models.Model):
    """Per-user, per-month search counter, shown as "Searches this month" in settings."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='search_counts')
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'search_count'
        unique_together = ('user', 'year', 'month')


class ProviderStatus(models.Model):
    """Latest health observation for one external provider, one row per provider."""
    provider = models.CharField(max_length=40, unique=True)
    ok = models.BooleanField(default=True)
    # 'query' (observed from a user search) or 'probe' (active health check).
    source = models.CharField(max_length=16, default='query')
    last_error = models.CharField(max_length=300, blank=True, default='')
    last_ok_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'search_provider_status'
