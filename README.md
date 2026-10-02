# Seurch

**Seurch** is a privacy-first metasearch engine built with Django 6. It blends
several independent web indexes, behind one clean interface, alongside Images, News,
Videos, Maps and Translate tabs and instant answers. Seurch as
a service is in alpha and available only to authenticated users (invite-only)
at this time.

Usefull links:
- [Seurch as a service](https://seurch.eu)
- [Documentation](https://docs.seurch.eu)

## Features

- **Web search across four engines**, Brave, Mojeek, Marginalia and Staan.
- **Search scope**, a quick per-search provider picker on the results page, on
  every tab that blends providers. 
- **Images, News and Videos** via the Brave Search API, each with extra
  providers blended in, **Pixabay** and **Wikidata** (Images), the **World News
  API** (News) and **Sepia/PeerTube** (Videos). 
- **Maps** via OpenStreetMap (Nominatim geocoding + an embedded map).
- **Translate** tab powered by [LibreTranslate](https://libretranslate.com/).
- **Knowledge panel** beside the web results, Wikipedia, TheTVDB (film/TV),
  TripAdvisor (places) and Stack Exchange cards.
- **Instant answers** above the results, weather, currency & unit conversion, a
  calculator, colour/base converters, QR codes, world clock, regex/JSON tools,
  hashes & UUIDs, HTTP status & port lookups, dice/coin/random, timer, and more
  (most computed locally; see [Instant answers](#instant-answers)).
- **Bangs**, start a query with `!` to send it straight to another site, `!w`
  Wikipedia, `!gh` GitHub, `!yt` YouTube, over 13,000 shortcuts from the
  community list, plus tab bangs (`!images`, `!n`, `!maps`, …) that switch tab
  without leaving Seurch and a lucky `!` that jumps to the first result.
- **Custom bangs**, define your own trigger and URL template under **Settings →
  Custom bangs**; they override a built-in shortcut of the same name and sync
  across your devices.
- **Public JSON API** - every search feature (web, images, news, videos, maps,
  translate, instant answers, knowledge cards, suggestions, provider status) is
  available programmatically over a Django REST Framework API, authenticated
  with per-user API keys and rate-limited per key.
- Dark/light/system theme; UI translated into seven languages.
- Optional account email, used only for password reset (without one, a lost
  password is unrecoverable).

## Prerequisites

- Python 3.13+
- [uv](https://github.com/astral-sh/uv)
- [Podman](https://podman.io/getting-started/installation) with [podman-compose](https://github.com/containers/podman-compose)
- A [Brave Search API](https://brave.com/search/api/) key (free tier available)
- *Optional:* keys for Mojeek, Marginalia (`public` works out of the box), Staan,
  TheTVDB, TripAdvisor, Stack Exchange, Pixabay and the World News API, each
  enables an extra engine or data source (see `.env.example`)
- *Optional:* [Node.js](https://nodejs.org/) 22+, only needed to **edit** the
  styles (the compiled CSS and icon assets are committed; see [Frontend](#frontend-tailwind-css--font-awesome))

## Dev stack setup

### 1. Configure environment

```bash
cp .env.example .env
```

The defaults in `.env.example` match the credentials used by the Podman Compose
PostgreSQL service, so no changes are needed for local development. Add your API
keys to `.env`, at minimum a Brave key for results:

```
BRAVE_API_KEY=your_brave_key_here
BRAVE_SUGGEST_API_KEY=your_brave_suggest_key_here   # autocomplete (separate Brave subscription)
MOJEEK_API_KEY=your_mojeek_key_here                 # optional
MARGINALIA_API_KEY=public                            # optional; "public" = free shared key
STAAN_API_KEY=your_staan_key_here                   # optional
```

`.env.example` documents every supported variable, including the optional
knowledge-card / media providers (`THETVDB_API_KEY`, `TRIPADVISOR_API_KEY`,
`STACKEXCHANGE_API_KEY`, `PIXABAY_API_KEY`, `WORLDNEWS_API_KEY`), the
LibreTranslate connection (`LIBRETRANSLATE_URL`) and the mail settings.
Marginalia's public API needs no signup, the literal value `public` is the free
shared key (rate-limited to ~1 req / 5 s). Without keys the app still runs, but
the affected tabs/cards display a configuration notice instead of results.

### 2. Bootstrap everything

```bash
make setup
```

This single command installs dependencies, enables the repo's git hooks, starts
the dev services, applies migrations and compiles the translation catalogs.

Or run each step manually:

```bash
make sync      # install Python dependencies
make hooks     # enable the git hooks (commit-message check)
make up        # start the dev services (PostgreSQL + Mailpit + LibreTranslate)
make migrate   # apply migrations
make run       # start the dev server
```

The app is then available at <http://localhost:8000>.

## Frontend (Tailwind CSS + Font Awesome)

The UI is styled with **Tailwind CSS v4** (CSS-first config, no
`tailwind.config.js`) and uses **self-hosted Font Awesome Free** icons (no CDN).
Both build outputs are **committed**, so the app and the Docker image need no
Node at runtime, Node is only required to *change* styles or icons.

| Path | Role |
|------|------|
| `assets/app.css` | Source → compiles to `search/static/search/tailwind.css` (global stylesheet). |
| `assets/instant.css` | Source → compiles to `instant/static/instant/instant.css` (instant-answer cards). |
| `search/static/search/vendor/fontawesome/` | Vendored Font Awesome CSS + webfont (never edited by hand). |
| `scripts/copy-fontawesome.mjs` | Copies Font Awesome from `node_modules` into static (`make fonts`). |

```bash
make css-install   # one-off: install the toolchain (npm ci)
make css           # compile assets/*.css → the committed stylesheets
make css-watch     # rebuild on every save while developing
```

After editing any template class **or** a source `.css`, run `make css` and
commit the regenerated output. CI has a dedicated `css` job that rebuilds from
source and **fails if the committed CSS is stale**, so it can never drift.

## Contributing

Bug reports and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md)
covers the dev setup, the three CI gates to run before opening a pull request
(`ruff check`, `make test`, `make css`), the Conventional Commit format the
`commits` job enforces, and what review looks for. Everyone taking part is
expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

Report security problems privately through
[GitHub security advisories](https://github.com/seurch-eu/seurch/security/advisories/new)
rather than as a public issue.

## Acknowledgements

Seurch is a metasearch engine: almost nothing it shows is its own. It stands on
the indexes, open data and free software below, and is grateful to all of them.

**Search & data providers**

- [Brave Search API](https://brave.com/search/api/), the primary index, and the
  source for the Images, News and Videos tabs.
- [Mojeek](https://www.mojeek.com/services/search/api/), an independent UK web
  index with a crawler of its own.
- [Marginalia Search](https://about.marginalia-search.com/article/api/), a
  non-commercial index for the small, non-commercial web.
- [Staan](https://staan.ai/) / European Search Perspective, the European index
  built by Qwant and Ecosia's joint venture.
- [Pixabay](https://pixabay.com/), [World News API](https://worldnewsapi.com/)
  and [Sepia](https://sepiasearch.org/) (the PeerTube search index), the
  supplementary Images, News and Videos providers.
- [OpenStreetMap](https://www.openstreetmap.org/) contributors and
  [Nominatim](https://nominatim.openstreetmap.org/), the Maps tab and the map
  quick answer. © OpenStreetMap contributors, data under
  [ODbL](https://www.openstreetmap.org/copyright).
- [LibreTranslate](https://github.com/LibreTranslate/LibreTranslate), the
  self-hosted engine behind the Translate tab.
- [Wikipedia](https://www.wikipedia.org/) and
  [Wikidata](https://www.wikidata.org/), knowledge-card detection and content,
  under CC BY-SA. Wikidata also supplies the Images tab with its items' images
  from [Wikimedia Commons](https://commons.wikimedia.org/), each under its own
  free licence.
- [TheTVDB](https://thetvdb.com/), [Tripadvisor](https://www.tripadvisor.com/)
  and [Stack Exchange](https://api.stackexchange.com/docs), the film/TV, places and
  Q&A knowledge cards.
- [Open-Meteo](https://open-meteo.com/), weather instant answers, and
  [Frankfurter](https://frankfurter.dev/), exchange rates from European Central
  Bank data, both free and keyless.
- [kagisearch/bangs](https://github.com/kagisearch/bangs), the community bang
  list Kagi maintains in the open, which is what `make bangs` fetches and what
  every external [bang](#bangs) resolves through.

Each provider's API terms apply to the results they return; Seurch's own
[licence](#license) does not extend to them.

**Built with**

- [Django](https://www.djangoproject.com/) and
  [Django REST Framework](https://www.django-rest-framework.org/), the
  application and the public API.
- [httpx](https://www.python-httpx.org/) for every upstream call,
  [psycopg](https://www.psycopg.org/) for PostgreSQL,
  [django-environ](https://django-environ.readthedocs.io/) for configuration.
- [Gunicorn](https://gunicorn.org/) and
  [WhiteNoise](https://whitenoise.readthedocs.io/), serving the app and its
  static files in the container.
- [lingua](https://github.com/pemistahl/lingua-py) for language detection and
  [segno](https://segno.readthedocs.io/) for QR-code instant answers.
- [Tailwind CSS](https://tailwindcss.com/) and
  [Font Awesome Free](https://fontawesome.com/), the interface, self-hosted, no
  CDN.
- [uv](https://github.com/astral-sh/uv), [Ruff](https://docs.astral.sh/ruff/),
  [Commitizen](https://commitizen-tools.github.io/commitizen/),
  [Podman](https://podman.io/), [PostgreSQL](https://www.postgresql.org/) and
  [Mailpit](https://mailpit.axllent.org/), the development toolchain.

And to [DuckDuckGo](https://duckduckgo.com/), whose bangs and instant answers
are the obvious inspiration for two of the features above.

## License

Seurch is free software, licensed under the **GNU Affero General Public License
v3.0 only** (AGPL-3.0-only). The full text is in [LICENSE](LICENSE).

This covers Seurch's own code. The upstream search APIs it queries, and the
vendored third-party assets under `search/static/search/vendor/` (Font Awesome
Free, which is CC BY 4.0 / SIL OFL 1.1 / MIT depending on the part), carry their
own terms.
