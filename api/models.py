from django.conf import settings
from django.db import models
from django.utils import timezone

from . import keys


class ApiKey(models.Model):
    """A public-API credential owned by a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='api_keys',
    )
    name = models.CharField(max_length=100, default='API key')
    prefix = models.CharField(max_length=16, unique=True, db_index=True)
    hashed_key = models.CharField(max_length=64, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked = models.BooleanField(default=False)

    class Meta:
        db_table = 'api_key'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.prefix}…)'

    @classmethod
    def create(cls, user, name='API key'):
        """Create and save a key for *user*; return ``(instance, full_key)``.

        *full_key* is the only time the caller can see the secret - show it to
        the user once, then drop it.
        """
        prefix, secret, full_key = keys.generate_key()
        instance = cls.objects.create(
            user=user,
            name=(name or 'API key').strip()[:100] or 'API key',
            prefix=prefix,
            hashed_key=keys.hash_secret(secret),
        )
        return instance, full_key

    @classmethod
    def authenticate(cls, presented_key):
        """Return the active ``ApiKey`` for a presented key string, or ``None``."""
        prefix, secret = keys.parse_key(presented_key)
        if not prefix:
            return None
        try:
            instance = cls.objects.select_related('user').get(prefix=prefix, revoked=False)
        except cls.DoesNotExist:
            return None
        if not keys.verify_secret(secret, instance.hashed_key):
            return None
        return instance

    def touch_last_used(self):
        """Record that the key was just used (best-effort, single column write)."""
        self.last_used_at = timezone.now()
        self.save(update_fields=['last_used_at'])

    @property
    def display_prefix(self):
        """The key's public identifier, as shown in the UI and CLI."""
        return f'{keys.KEY_BRAND}{self.prefix}'
