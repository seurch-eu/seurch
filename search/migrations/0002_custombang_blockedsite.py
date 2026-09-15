from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('search', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CustomBang',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('trigger', models.CharField(max_length=32)),
                ('url_template', models.CharField(max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='custom_bangs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'search_custom_bang',
                'ordering': ['trigger'],
                'unique_together': {('user', 'trigger')},
            },
        ),
        migrations.CreateModel(
            name='BlockedSite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('domain', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='blocked_sites', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'search_blocked_site',
                'ordering': ['domain'],
                'unique_together': {('user', 'domain')},
            },
        ),
    ]
