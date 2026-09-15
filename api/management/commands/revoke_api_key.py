from django.core.management.base import BaseCommand, CommandError

from api import keys
from api.models import ApiKey


class Command(BaseCommand):
    help = 'Revoke a public-API key by its prefix (the seurch_sk_… identifier).'

    def add_arguments(self, parser):
        parser.add_argument(
            'prefix',
            help='Key prefix, e.g. seurch_sk_ab12cd34 (the full key is accepted too).',
        )

    def handle(self, *args, **options):
        prefix = options['prefix'].strip()
        prefix = prefix.removeprefix(keys.KEY_BRAND)
        prefix = prefix.split('.', 1)[0]  # tolerate a full key being pasted in

        try:
            key = ApiKey.objects.select_related('user').get(prefix=prefix)
        except ApiKey.DoesNotExist:
            raise CommandError(f'No API key with prefix {prefix!r}.')

        if key.revoked:
            self.stdout.write(self.style.WARNING(f'Key {key.display_prefix} is already revoked.'))
            return

        key.revoked = True
        key.save(update_fields=['revoked'])
        self.stdout.write(self.style.SUCCESS(
            f'Revoked key {key.display_prefix} ("{key.name}") for {key.user.username}.'
        ))
