from django.core.management.base import BaseCommand

from search import health


class Command(BaseCommand):
    help = (
        'Probe the providers that expose a free health endpoint (Nominatim, '
        'LibreTranslate, Open-Meteo, Frankfurter) and record their status. The '
        'paid providers are never probed, their status comes from real queries.'
    )

    def handle(self, *args, **options):
        results = health.run_probes()
        if not results:
            self.stdout.write('No providers to probe (none configured).')
            return
        for provider, ok in results.items():
            label = self.style.SUCCESS('up') if ok else self.style.ERROR('down')
            self.stdout.write(f'{provider}: {label}')
