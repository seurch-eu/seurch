from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from api.models import ApiKey


class Command(BaseCommand):
    help = 'Create a public-API key for a user. The key is printed once and cannot be recovered.'

    def add_arguments(self, parser):
        parser.add_argument('username', help='Username the key belongs to.')
        parser.add_argument('--name', default='CLI key', help='Label for the key.')

    def handle(self, *args, **options):
        User = get_user_model()
        try:
            user = User.objects.get(username=options['username'])
        except User.DoesNotExist:
            raise CommandError(f"No user named {options['username']!r}.")

        instance, full_key = ApiKey.create(user, name=options['name'])

        self.stdout.write(self.style.SUCCESS(
            f'Created API key "{instance.name}" for {user.username}.'
        ))
        self.stdout.write('')
        self.stdout.write(full_key)
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            'Store this key now - it is shown only once and cannot be recovered.'
        ))
