import { mkdirSync, copyFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = join(root, 'node_modules/@fortawesome/fontawesome-free');
const dest = join(root, 'search/static/search/vendor/fontawesome');

const files = [
  'css/fontawesome.min.css',
  'css/solid.min.css',
  'webfonts/fa-solid-900.woff2',
  // Font Awesome Free is CC BY 4.0 / SIL OFL 1.1 / MIT depending on the part, so
  // ship its licence next to the assets rather than only the CSS header comment.
  'LICENSE.txt',
];

for (const rel of files) {
  const out = join(dest, rel);
  mkdirSync(dirname(out), { recursive: true });
  copyFileSync(join(src, rel), out);
  console.log('vendored', rel);
}
