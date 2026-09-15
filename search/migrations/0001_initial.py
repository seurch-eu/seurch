from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='SearchCache',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cache_key', models.CharField(max_length=64, unique=True)),
                ('results', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(db_index=True)),
            ],
            options={
                'db_table': 'search_cache',
            },
        ),
    ]
