from django.core.management.base import BaseCommand
from django.utils import timezone

from instant.models import InstantCache
from search.models import ProxiedImage, SearchCache


class Command(BaseCommand):
    help = 'Delete expired search cache, image-proxy, and instant cache entries'

    def handle(self, *args, **options):
        now = timezone.now()
        deleted, _ = SearchCache.objects.filter(expires_at__lte=now).delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} expired cache entries'))
        deleted, _ = ProxiedImage.objects.filter(expires_at__lte=now).delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} expired image-proxy entries'))
        deleted, _ = InstantCache.objects.filter(expires_at__lte=now).delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {deleted} expired instant cache entries'))
