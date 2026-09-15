"""Store session-link tokens as SHA-256 hashes instead of clear text.

The clear-text tokens already in the table are hashed in place rather than
dropped, so links people are currently using keep working; what changes is
that the server can no longer read them back, and Settings therefore shows a
link exactly once, at generation (see ``accounts.models.SessionLink``).

Irreversible by construction: a digest cannot be turned back into the token
it was made from. Rolling back means dropping the column and having every
user generate a new link.
"""

import hashlib

from django.db import migrations, models


def hash_existing_tokens(apps, schema_editor):
    """Fill token_hash from the clear-text token on every existing row.

    The digest is computed here rather than by importing
    ``accounts.models.hash_token``, so this migration keeps producing the
    same values if that helper is ever changed.
    """
    SessionLink = apps.get_model('accounts', 'SessionLink')
    rows = []
    for link in SessionLink.objects.all():
        link.token_hash = hashlib.sha256(link.token.encode('utf-8')).hexdigest()
        rows.append(link)
    if rows:
        SessionLink.objects.bulk_update(rows, ['token_hash'], batch_size=500)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
    ]

    operations = [
        # Added nullable and un-indexed first: the rows have no hash yet, so
        # neither NOT NULL nor UNIQUE could hold at this point.
        migrations.AddField(
            model_name='sessionlink',
            name='token_hash',
            field=models.CharField(editable=False, max_length=64, null=True),
        ),
        # No reverse_code: the clear text is gone for good once this runs.
        migrations.RunPython(hash_existing_tokens),
        migrations.AlterField(
            model_name='sessionlink',
            name='token_hash',
            field=models.CharField(db_index=True, editable=False, max_length=64, unique=True),
        ),
        migrations.RemoveField(
            model_name='sessionlink',
            name='token',
        ),
    ]
