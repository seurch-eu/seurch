from django.core.management.base import BaseCommand
from django.utils import timezone

from instant import currency
from instant.models import InstantCache


class Command(BaseCommand):
    help = 'Refresh the cached currency exchange rates and prune expired instant cache.'

    def handle(self, *args, **options):
        table = currency.get_rate_table(force=True)
        if table is None:
            self.stderr.write(self.style.ERROR('Could not fetch exchange rates (upstream unreachable).'))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Refreshed {len(table["rates"])} rates (base {currency.BASE_CURRENCY}, '
                f'date {table.get("date") or "?"}).'))

        deleted, _ = InstantCache.objects.filter(expires_at__lte=timezone.now()).delete()
        self.stdout.write(f'Pruned {deleted} expired instant-cache entries.')
