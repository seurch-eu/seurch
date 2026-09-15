from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('search', '0004_proxiedimage'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SearchCount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveSmallIntegerField()),
                ('month', models.PositiveSmallIntegerField()),
                ('count', models.PositiveIntegerField(default=0)),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='search_counts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'search_count',
                'unique_together': {('user', 'year', 'month')},
            },
        ),
    ]
