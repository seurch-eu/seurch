from django.core.management.base import BaseCommand

from api.models import ApiKey


class Command(BaseCommand):
    help = 'List public-API keys (no secrets are shown).'

    def add_arguments(self, parser):
        parser.add_argument('--user', default=None, help='Limit to one username.')

    def handle(self, *args, **options):
        keys = ApiKey.objects.select_related('user').all()
        if options['user']:
            keys = keys.filter(user__username=options['user'])

        keys = list(keys)
        if not keys:
            self.stdout.write('No API keys found.')
            return

        for key in keys:
            state = 'revoked' if key.revoked else 'active'
            last_used = key.last_used_at.isoformat() if key.last_used_at else 'never'
            self.stdout.write(
                f'{key.display_prefix}  {state:<7}  {key.user.username:<20}  '
                f'used={last_used}  {key.name}'
            )
