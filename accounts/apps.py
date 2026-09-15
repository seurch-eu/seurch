from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        # Email is not required to create a user (e.g. via createsuperuser).
        from django.contrib.auth.models import User
        User.REQUIRED_FIELDS = []
