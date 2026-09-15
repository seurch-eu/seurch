import json
import os

import httpx
from django.apps import apps
from django.core.management.base import BaseCommand

BANGS_URL = 'https://raw.githubusercontent.com/kagisearch/bangs/main/data/bangs.json'

RESERVED_TAB_TRIGGERS = {'translate'}


class Command(BaseCommand):
    help = 'Download bang definitions from kagisearch/bangs and build lookup files'

    def handle(self, *args, **options):
        self.stdout.write('Fetching bangs from kagisearch/bangs ...')
        try:
            with httpx.Client(follow_redirects=True, timeout=60) as client:
                r = client.get(BANGS_URL)
                r.raise_for_status()
                raw = r.json()
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Download failed: {exc}'))
            return

        self.stdout.write(f'Downloaded {len(raw)} entries')

        compact: dict[str, str] = {}
        for entry in raw:
            trigger = entry.get('t', '').lower().strip()
            url = entry.get('u', '')
            if trigger and url:
                compact[trigger] = url
            for alias in entry.get('ts', []):
                alias = alias.lower().strip()
                if alias and alias not in compact:
                    compact[alias] = url

        self.stdout.write(f'Indexed {len(compact)} triggers (including aliases)')

        for trigger in RESERVED_TAB_TRIGGERS:
            compact.pop(trigger, None)

        app_dir = apps.get_app_config('search').path

        data_dir = os.path.join(app_dir, 'data')
        os.makedirs(data_dir, exist_ok=True)
        data_path = os.path.join(data_dir, 'bangs.json')
        with open(data_path, 'w', encoding='utf-8') as f:
            json.dump(compact, f, separators=(',', ':'), ensure_ascii=False)
        self.stdout.write(f'Server-side data → {data_path}')

        static_dir = os.path.join(app_dir, 'static', 'search')
        os.makedirs(static_dir, exist_ok=True)
        static_path = os.path.join(static_dir, 'bangs.min.json')
        with open(static_path, 'w', encoding='utf-8') as f:
            json.dump(compact, f, separators=(',', ':'), ensure_ascii=False)
        self.stdout.write(f'Client-side data  → {static_path}')

        self.stdout.write(self.style.SUCCESS('Bang data updated successfully.'))
