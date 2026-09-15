# design-sync notes — Seurch

## Shape: styling-only (off-script)
This repo (`seurch` / `seurch-css`) is a **Django server-rendered app styled with
Tailwind CSS v4**, not a JavaScript/React component library. The standard
design-sync converter has nothing to bundle here:

- No `.tsx`/`.jsx`/`.ts` source, no component `dist/`, nothing exporting components.
- No Storybook, no `*.stories.*`.
- `package.json` ("seurch-css") is purely a Tailwind + Font Awesome build toolchain.
- UI = Django HTML templates (`*/templates/**/*.html`) + Tailwind utility classes.

Per the user's choice (2026-06-25), only the **styling layer** is synced so the
design agent at least has Seurch's brand tokens, compiled utility CSS, and icon
font. The component picker will show only Foundations reference cards.

## Sources of truth
- Tailwind input:  `assets/app.css` (main, defines `@theme` tokens + `@layer app` components)
                   `assets/instant.css` (instant-answer widgets)
- Compiled output: `search/static/search/tailwind.css`  (committed; built by `make css`)
                   `instant/static/instant/instant.css`
- Icon font:       `search/static/search/vendor/fontawesome/{css,webfonts}/`

## Caveat: purged Tailwind build
The committed `tailwind.css` is on-demand/purged — it contains only the utility
classes actually used in the templates. A design using a utility that isn't in
the build will render unstyled. The semantic token utilities (`bg-surface`,
`text-ink`, `border-edge`, `text-brand`, `bg-brand-soft`, …) are reliably present.

## To re-sync
Re-run the build/upload by hand (no converter applies). Tokens live in
`assets/app.css` `@theme` + `@layer base` (light) and `.dark` (dark overrides).
