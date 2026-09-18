# Seurch

**Seurch** is a privacy-first metasearch engine built with Django 6. It blends
several independent web indexes, the [Brave Search API](https://brave.com/search/api/),
the [Mojeek API](https://www.mojeek.com/services/search/api/), the
[Marginalia API](https://about.marginalia-search.com/article/api/) and
[Staan](https://staan.ai/), behind one clean interface, alongside Images, News,
Videos, Maps and Translate tabs and DuckDuckGo-style instant answers. Seurch as
a service is in alpha and available only to authenticated users (invite-only)
at this time.

## Features

- **Web search across four engines**, Brave, Mojeek, Marginalia and Staan.
  Enable any subset; when more than one is active their ranked lists are merged
  with **Reciprocal Rank Fusion** and deduplicated, so a result several engines
  agree on rises to the top (results found in multiple engines are labelled with the
  contributing engines).
- **Search scope**, a quick per-search provider picker on the results page, on
  every tab that blends providers. It offers the providers of the tab you're on,
  Web the engines (Brave / Mojeek / Marginalia / Staan), Images Brave + Pixabay,
  News Brave + World News, Videos Brave + Sepia, so you can tick any subset to
  override the saved preferences for just that search, without changing them.
  Maps and Translate have a single provider each, so they show no picker. Each
  provider a web search hits counts as one search towards the monthly total.
- **Images, News and Videos** via the Brave Search API, each with an extra
  provider blended in, **Pixabay** (Images), the **World News API** (News) and
  **Sepia/PeerTube** (Videos). The supplementary provider is also the sole source
  for the web-only engines (Mojeek, Marginalia, Staan), which have no media
  search of their own.
- **Maps** via OpenStreetMap (Nominatim geocoding + an embedded map; no API key).
- **Translate** tab powered by a self-hosted [LibreTranslate](https://libretranslate.com/) instance.
- **Knowledge panel** beside the web results, Wikipedia, TheTVDB (film/TV),
  TripAdvisor (places) and Stack Exchange cards, detected via Wikipedia/Wikidata
  and lazy-loaded so a slow card never holds up the answer.
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
  across your devices (see [Bangs](#bangs)).
- **Per-user settings**, engines, safe search, search & interface language,
  theme, link behaviour, image proxying, lazy-loaded cards, custom bangs, blocked
  sites and which supplementary data sources are enabled, synced automatically
  across devices, with file export/import.
- **Provider status page**, a `/status` page shows whether each upstream
  provider is up or down, using free health endpoints where they exist and the
  outcome of recent searches otherwise (no extra API spend). It can be turned
  off, and public `/status/health` endpoints (one per provider, plus a roll-up)
  answer 200 or 500 for an external uptime monitor (see
  [Provider status](#provider-status)).
- **Public JSON API** - every search feature (web, images, news, videos, maps,
  translate, instant answers, knowledge cards, suggestions, provider status) is
  available programmatically over a Django REST Framework API, authenticated
  with per-user API keys and rate-limited per key (see [Public API](#public-api)).
- Dark/light/system theme; UI translated into seven languages.
- Optional account email, used only for password reset (without one, a lost
  password is unrecoverable).
- Login required, no public registration.

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

## Makefile reference

Run `make` with no arguments to list all targets.

| Target           | Description                              |
|------------------|------------------------------------------|
| `make setup`     | Bootstrap the dev environment from scratch |
| `make sync`      | Install / update Python dependencies    |
| `make up`        | Start dev services, PostgreSQL + Mailpit + LibreTranslate (detached) |
| `make down`      | Stop dev services                        |
| `make logs`      | Tail dev service logs                    |
| `make mailpit`   | Open Mailpit web UI (http://localhost:8025) |
| `make migrate`   | Apply database migrations                |
| `make makemigrations` | Generate new migrations             |
| `make run`       | Start the Django dev server              |
| `make shell`     | Open the Django shell                    |
| `make test`      | Run the test suite                       |
| `make superuser` | Create a superuser account               |
| `make reset-db`  | Wipe and recreate the database volume    |
| `make bangs`     | Download bang definitions from kagisearch/bangs |
| `make refresh-currency` | Refresh cached exchange rates + prune the instant-answer cache |
| `make messages`  | Compile translation catalogs (.po → .mo) |
| `make messages-extract` | Re-scan code for translatable strings → update .po |
| `make css-install` | Install the Tailwind/Font Awesome toolchain (one-off, `npm ci`) |
| `make css`       | Compile Tailwind sources → committed stylesheets |
| `make css-watch` | Recompile Tailwind CSS on every save     |
| `make fonts`     | Re-vendor the self-hosted Font Awesome assets |
| `make hooks`     | Enable the repo's git hooks (`commit-msg` → `cz check`) |
| `make commit`    | Write a Conventional Commit interactively (commitizen) |
| `make check-commits` | Validate this branch's commit messages against `origin/main` |
| `make changelog` | Preview the changelog for the unreleased commits |
| `make bump`      | Bump the version, write `CHANGELOG.md`, create the release tag |

## Database credentials (dev)

| Setting  | Value     |
|----------|-----------|
| Host     | localhost |
| Port     | 5432      |
| Database | search    |
| User     | search    |
| Password | search    |

## Mail (dev)

Password reset emails are caught by [Mailpit](https://mailpit.axllent.org/),
which starts automatically with `make up`. No emails leave your machine.

| Interface | URL / address          |
|-----------|------------------------|
| Web UI    | http://localhost:8025  |
| SMTP      | localhost:1025         |

Open the web UI with `make mailpit` to inspect sent messages.

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

## Search providers

Results are fetched server-side via `httpx`, API keys are never exposed to the
browser. The per-engine HTTP clients live in `search/clients.py`; each search
tab is its own Django app (`web`, `images`, `news`, `videos`, `maps`,
`translate`).

### Brave Search

| Endpoint | Tab |
|----------|-----|
| `/res/v1/web/search` | Web |
| `/res/v1/images/search` | Images |
| `/res/v1/news/search` | News |
| `/res/v1/videos/search` | Videos |

### Mojeek, Marginalia & Staan (Web)

Three further independent web indexes: **Mojeek** (UK), **Marginalia** (a
non-commercial, small-web-focused Swedish index) and **Staan** (the European
index built by Qwant and Ecosia's joint venture, European Search Perspective).
The user enables any subset of Brave / Mojeek / Marginalia / Staan; when more
than one returns results, `web/services.py` fuses their ranked lists with
**Reciprocal Rank Fusion** (RRF): each URL scores
`Σ 1/(60 + rank)` over the engines that returned it, so duplicates collapse into a
single entry and results multiple engines agree on rank higher. The merged entry
records which engines contributed (shown beside the result).

Staan (`GET /v2/search/web`, bearer token) has a few limits the other engines
don't: it pages by `offset` in steps of ten and refuses an offset past 30, so it
only serves the first four pages; its `q` is capped at 400 characters; and it
takes the search language as a single `market`, of which it serves `fr-FR`,
`de-DE` and the English family only, so the app's other languages send no market
at all. It also has no safe-search control, adult filtering on a blended search
is left to the engines that do have one. Whenever Staan can't serve a search it
simply sits it out and the other enabled engines answer. Only the plain search
is used, the optional `extra_snippets` / `full_content` modes make the API fetch
and rerank every result page, which is latency a results page must not pay for.

### Images, News & Videos (supplementary providers)

Each media tab blends Brave with a second provider, fetched concurrently and
interleaved round-robin:

- **Images**, [Pixabay](https://pixabay.com/) (`PIXABAY_API_KEY`).
- **News**, the [World News API](https://worldnewsapi.com/) (`WORLDNEWS_API_KEY`).
- **Videos**, [Sepia](https://sepiasearch.org/), the PeerTube search index (no
  key required).

Brave backs the brave/all engines; the supplementary provider is mixed in
whenever it's available and is the sole source for the web-only engines
(Mojeek, Marginalia, Staan).
Each can be switched off per-user in **Settings → Engines → Data sources**.

Opening an image on the **Images** tab pops a Google-Images-style lightbox: the
picture larger, links to its page and the full image, and a grid of **similar
images**, an image search seeded from the opened image's caption, shown as real
thumbnails (click one to focus it and keep exploring) plus a *See all results*
link to the full search. They load lazily, only for the image actually opened,
so they never delay the grid; with JavaScript off the result links to a
server-rendered detail page showing the same image, links and similar grid, so
the feature still works.

### Maps (OpenStreetMap)

The **Maps** tab needs no API key. Queries are geocoded with
[Nominatim](https://nominatim.openstreetmap.org/) and shown on an embedded
OpenStreetMap map. When a web search looks like an address or place, a map
quick-answer with a minimap and an **Open in Maps** link appears alongside the
results; a TripAdvisor card's address links straight to the map. Geocoding is
cached and gated by a place-detection heuristic to keep Nominatim usage light.

### Translate (LibreTranslate)

The **Translate** tab is powered by a self-hosted
[LibreTranslate](https://github.com/LibreTranslate/LibreTranslate) instance,
configured via `LIBRETRANSLATE_URL` (`make up` starts one locally at
`http://localhost:5000`). Leave the variable unset to disable the tab and its
setting entirely.

## Knowledge cards

The web tab (page 1 only) can render up to three side cards, detected and fetched
in the `cards` app:

- **Wikipedia** (keyless), the relevance anchor; its title and Wikidata id feed
  the other detectors.
- **Wikidata** (keyless), not a card but the classifier that tags the subject
  (film/TV, person, place) language-independently.
- **TheTVDB** (`THETVDB_API_KEY`, plus `THETVDB_PIN` for user-supported keys),
  film / TV-show panel. TheTVDB licenses its API either under a negotiated
  contract or the user-supported model (the end user's own subscriber PIN);
  configure whichever your deployment is licensed for, and the card displays
  the attribution + link to TheTVDB.com their terms require.
- **TripAdvisor** (`TRIPADVISOR_API_KEY`), restaurant / hotel / attraction panel,
  on the Terra Partner API's catalog endpoints (the retired Content API's keys
  now 403). The catalog projection is a reduced one, so the card shows name,
  Geo, address, rating, description and links, but no photo, cuisine, price
  level or ranking, which need a per-Location licence.
- **Stack Exchange** (`STACKEXCHANGE_API_KEY` optional), top question-and-answer
  panel.

The paid APIs are only called when the query actually looks like a movie / place,
every result is scored against the query (the closest wins, non-matches are
dropped), and each lookup is cached for an hour. The cards **lazy-load** after the
results by default (a JS fetch to `/search/cards/`), so they decorate the page
without delaying the answer. Each source can be toggled off in **Settings →
Engines → Data sources**.

On a phone the knowledge panel sits *above* the web results, so the cards render
cropped there (no hero image, no cast/crew, a shorter summary) rather than
pushing the answer down the page. A **"Show more"** button in the card footer
expands it to everything a wide screen shows, and back again. It needs
JavaScript, so it only appears when scripting is available; without it the card
simply stays as it is and its footer link still leads to the full entry.

## Instant answers

Common utility queries are answered inline at the top of the web results by the
`instant` app, in the spirit of DuckDuckGo's instant answers. Almost everything is
computed **locally** (no third party):

| Category | Example queries |
|----------|-----------------|
| Calculator | `2+2`, `sqrt(16)*3`, `15% of 200` |
| Unit conversion | `5 km to miles`, `100 f to c`, `2 cups to ml` |
| Base conversion | `0xff in decimal`, `255 in binary` |
| Colour | `#4f46e5`, `rgb(255,128,0)`, `color picker` |
| World clock | `time in tokyo`, `what time is it in new york` |
| QR code | `qr code https://example.com` |
| Hash / UUID | `md5 hello`, `sha256 of test`, `uuid` |
| Password | `password generator`, `strong password` |
| Unix time | `unix timestamp`, `1700000000 to date` |
| Encode / decode | `base64 encode hi`, `url decode foo%20bar` |
| JSON / Regex | `json formatter`, `regex tester` |
| HTTP / Ports | `http 404`, `port 443` |
| Randomisers | `roll 2d6`, `flip a coin`, `random number 1-100` |
| Timer | `timer 5 minutes`, `stopwatch` |
| What's my IP | `what's my ip` |

Triggers are **multilingual**, they fire in English, French, German, Spanish,
Italian, Portuguese and Dutch (e.g. `météo à Paris`, `wie spät ist es in Berlin`,
`100 dólares a euros`, `255 en binaire`). The vocabulary lives in
`instant/keywords.py`.

Two queries reach the network (free, keyless, open data) and are cached:

- **Currency**, `100 usd to eur`, rates from [Frankfurter](https://frankfurter.dev/)
  (European Central Bank data). Fetched once and stored in a table for **24 hours**;
  all pairs are derived locally from the cached table. A day-old rate is served if the
  feed is unreachable.
- **Weather**, `weather in Paris`, current conditions + 7-day forecast from
  [Open-Meteo](https://open-meteo.com/), cached one hour.

If you run behind a network allowlist, permit `api.frankfurter.dev`,
`geocoding-api.open-meteo.com`, and `api.open-meteo.com` for those two to work. The
local answers need no network. Prewarm/refresh rates with `make refresh-currency`.

## Bangs

A **bang** sends a query straight where it belongs: put `!trigger` anywhere in
the search and Seurch redirects, handing the rest of the query to the target.
Bangs are resolved before any provider is called, so they cost no API quota and
count for nothing against the monthly search total. `search/bangs.py` holds the
server-side resolution; `search/static/search/bangs.js` intercepts the form
submit and redirects straight from the browser when JavaScript is on, with the
server path as the fallback. The **About** page (`/about`) documents them for
users and has a searchable index of every shortcut.

Four kinds are recognised.

**Tab bangs** switch tab without leaving Seurch:

| Bang | Tab |
|------|-----|
| `!web` | Web |
| `!images`, `!i` | Images |
| `!news`, `!n` | News |
| `!videos`, `!v` | Videos |
| `!maps`, `!m` | Maps |
| `!translate` | Translate |

A tab bang for a tab the user doesn't have, `!news` while on a web-only engine,
or `!maps` with OpenStreetMap switched off, is deactivated rather than routed to
a hidden tab: the query runs as a normal search on the first available tab.

**Custom bangs** are per-user, defined under **Settings → Custom bangs** as a
trigger plus a URL template with `{{{s}}}` where the query belongs, e.g. `gh` →
`https://github.com/search?q={{{s}}}`. A custom bang **shadows** the built-in
trigger of the same name, so `!gh` can point wherever you like. Unlike the rest
of the preferences they live in the database (the `search_custom_bang` table,
unique per user and trigger), not the cookie, and sync across devices with
everything else under **Settings → Backup & sync**.

**External bangs** are the community list, over 13,000 triggers (aliases
included) covering Wikipedia, GitHub, YouTube, Stack Overflow, Amazon, npm and
so on.
They ship committed in the repository, so a fresh clone has bangs out of the
box. Refresh them with:

```bash
make bangs      # uv run python manage.py fetch_bangs
```

That downloads [kagisearch/bangs](https://github.com/kagisearch/bangs) and
writes two files: `search/data/bangs.json` (server-side resolution) and
`search/static/search/bangs.min.json` (the copy the browser fetches). Triggers
reserved for a tab bang (`translate`) are dropped. Commit both files when you
refresh them.

**The lucky bang**, a standalone `!` in the query, is "I'm feeling lucky" and is
checked before the other three: the web search runs and the first result (after
your blocked sites are filtered out) is opened directly. With no result to jump
to, the terms are searched normally. Otherwise a tab bang wins, then one of your
custom bangs, then the external list.

Bangs are skipped on the **Translate** tab, where a query starting with `!` is
text to translate rather than a shortcut.

## Provider status

A **`/status`** page (linked from the footer and **Settings**) shows whether
each upstream provider is currently working, so users know if a missing result
is an outage rather than "no matches", *without* spending paid API quota to
find out:

- Providers with a **free, public endpoint** are probed directly on a schedule:
  OpenStreetMap's Nominatim (`/status.php`), the self-hosted LibreTranslate
  (`/languages`), and the keyless Open-Meteo and Frankfurter feeds.
- Every other provider (Brave, Mojeek, Marginalia, Staan, Pixabay, World News, Sepia,
  TheTVDB, TripAdvisor, Stack Exchange, Wikipedia/Wikidata) is *never* polled just
  for a health check. Instead each real search records whether the provider
  answered, and the page reflects that latest success/failure.

Statuses live in the `ProviderStatus` table (one row per provider), written
best-effort and throttled from the request helpers and from the
`check_provider_health` management command, run every 10 minutes by the cron
role (see `config/crontab`). A provider with no API key configured is hidden,
not shown as down. The registry of providers lives in `search/health.py`.

### Turning the page off

Set `STATUS_PAGE_ENABLED=false` in `.env` if you would rather not publish which
providers this instance uses and when they fail. The page then returns 404, the
footer and Settings links to it disappear, and the API's `/api/v1/status/`
endpoint, which serves the same data, returns 404 too. Health is still recorded
and still readable, in the database and through the monitoring endpoint below.

### Uptime monitoring (`/status/health`)

Machine-readable siblings of the page, for an external uptime monitor such as
[phare.io](https://phare.io), Better Stack or Uptime Kuma. There are two kinds,
both public (no login) and both answering **200** when healthy and **500** when
not, which is what an uptime service alerts on:

- **`/status/health/<provider>`** - one endpoint per upstream, so each service
  gets its own check and an alert names what actually broke. See
  [Per-provider endpoints](#per-provider-endpoints) below for the full list.
- **`/status/health`** - the roll-up across every watched provider, handy as a
  single "is anything wrong" check.

Point a monitor at `https://your-instance/status/health` and it gets:

| Situation | Response |
|-----------|----------|
| Every watched provider up (or not yet observed) | **200** `{"status": "ok", "watched": 8, "operational": 6, "down": [], "unknown": 2}` |
| One or more watched providers down | **500** `{"status": "down", "watched": 8, "operational": 5, "down": ["brave"], "unknown": 2}` |
| Database unreachable | **500** `{"status": "error", "detail": "database unavailable"}` |
| `STATUS_MONITOR_PROVIDERS` matches nothing (a typo) | **500** `{"status": "error", "detail": "no providers watched"}` |
| Endpoint disabled (`STATUS_MONITOR_ENABLED=false`) | **404** |
| Wrong or missing `STATUS_MONITOR_TOKEN` | **403** |

Notes:

- It needs **no login** (a monitor can't sign in) and answers independently of
  `STATUS_PAGE_ENABLED`, so an instance that hides the page is still monitorable.
  Set `STATUS_MONITOR_TOKEN` to require a shared secret, sent as `?token=…`, an
  `X-Monitor-Token` header, or `Authorization: Bearer …`.
- A provider that has **never been observed** is not an outage, most report in
  only when someone searches, so a quiet one must not hold the monitor red.
  Only a recorded failure counts.
- `STATUS_MONITOR_PROVIDERS` narrows what is watched, to provider slugs and/or
  group keys (`engine`, `media`, `cards`, `instant`, `maps`, `translate`).
  `STATUS_MONITOR_PROVIDERS=engine` pages only for the search engines
  themselves; empty (the default) watches every configured provider. Names that
  match nothing are ignored, and if *none* of them match, the endpoint reports
  that as an error rather than reporting all-clear on a typo.
- The body names *which* providers are down, never the upstream error text
  (that stays on the page, behind a login). Replies are sent `Cache-Control:
  no-store` so no proxy serves a stale verdict.

### Per-provider endpoints

`/status/health/<provider>` answers for one upstream only, so a monitoring
service can hold a separate check per service and an alert says *which* one is
down rather than "something is". Same contract, one provider:

| Situation | Response |
|-----------|----------|
| Provider up | **200** `{"status": "ok", "provider": "brave", "state": "up", "checked_at": "…", "last_ok_at": "…"}` |
| Provider down | **500** `{"status": "down", "provider": "brave", "state": "down", …}` |
| Never observed yet | **200** `{"status": "ok", "provider": "brave", "state": "unknown", …}` |
| Not configured here, or an unknown name | **404** `{"status": "error", "provider": "…", "detail": "…"}` |

One URL per provider, using the slug from `search/health.py`:

| Provider | Endpoint |
|----------|----------|
| Brave | `/status/health/brave` |
| Mojeek | `/status/health/mojeek` |
| Marginalia | `/status/health/marginalia` |
| Staan | `/status/health/staan` |
| Pixabay | `/status/health/pixabay` |
| Sepia | `/status/health/sepia` |
| World News API | `/status/health/worldnews` |
| Wikipedia | `/status/health/wikipedia` |
| Wikidata | `/status/health/wikidata` |
| TheTVDB | `/status/health/thetvdb` |
| TripAdvisor | `/status/health/tripadvisor` |
| Stack Exchange | `/status/health/stackexchange` |
| Open-Meteo (weather) | `/status/health/weather` |
| Frankfurter (currency) | `/status/health/currency` |
| OpenStreetMap / Nominatim | `/status/health/openstreetmap` |
| LibreTranslate | `/status/health/translate` |

A provider this deployment hasn't configured (no API key) returns 404 rather
than 200: there is nothing to report, and a green check for a provider that
isn't even wired up would be worse than an obviously broken one. So set up
checks only for the providers you actually run.

`STATUS_MONITOR_PROVIDERS` scopes the **roll-up** only, a per-provider URL names
its provider and always answers for it. `STATUS_MONITOR_ENABLED` and
`STATUS_MONITOR_TOKEN` apply to both kinds.

`/status/health` reflects the *providers*; the deployment's own liveness check
stays `/up` (answered by `config.middleware.HealthCheckMiddleware` before host
validation, for kamal-proxy). Monitor both if you want to tell "the app is
down" apart from "the app is up, an upstream provider is down".

## Footer links

The footer ships no privacy policy, terms or legal notice by default, their
content is jurisdiction-specific and tied to whoever operates the instance.
Self-hosted deployments can add their own via `FOOTER_LINKS` in `.env`,
"Label=URL" pairs separated by commas:

```
FOOTER_LINKS=Privacy=https://example.com/privacy,Legal notice=https://example.com/legal
```

## User settings

Each user's preferences are stored client-side in a single URL-encoded-JSON
**cookie** (`seurch_prefs`), so they persist independently of login and can be
exported, imported and synced. Unbounded lists (custom bangs, blocked sites) live
in the database instead.

| Setting | Options | Default |
|---------|---------|---------|
| Engines | Any subset of Brave / Mojeek / Marginalia / Staan | All enabled |
| Search scope | Per-search override of the active tab's providers (results page) | Saved engines & sources |
| Safe search | On / Off | On |
| Search language | Auto / one of the 7 UI languages | Auto (browser language) |
| Interface language | Auto / one of the 7 UI languages | Auto |
| Theme | System / Light / Dark | System |
| Open links | Same tab / New tab | Same tab |
| Proxy images | On / Off | Off |
| Lazy-load knowledge cards | On / Off | On |
| Data sources | Per-provider toggles (knowledge cards, weather, Pixabay, Sepia, World News, Translate, OpenStreetMap) | All enabled |

Settings sync automatically to the user's account (a `UserSettings` snapshot,
one row per user) and are restored on any device they sign in on, a middleware
reconciles each device's cookie against the latest snapshot on every request.
They can also be exported to / imported from a JSON file under **Settings →
Backup & sync**.

## Public API

Every search feature is available programmatically over a JSON API built with
[Django REST Framework](https://www.django-rest-framework.org/), mounted under
`/api/v1/`. The same `fetch_*` service functions back the API and the website,
so both always return the same results. The HTML views' cookie-stored
preferences (engine, safe search, language, time range, page) become plain query
parameters, so an API request is fully described by its URL.

**Authentication.** Every request needs an API key, sent in the `Authorization`
header (`X-Api-Key` is also accepted):

```bash
curl -H "Authorization: Api-Key seurch_sk_<your-key>" \
  "http://localhost:8000/api/v1/web/?q=climate&lang=en"
```

Keys are issued per user. Create and revoke them under **Settings → API keys**
(the full key is shown only once), or from the command line:

```bash
make superuser                                   # if you don't have a user yet
uv run python manage.py create_api_key <user> --name "my script"
uv run python manage.py list_api_keys [--user <user>]
uv run python manage.py revoke_api_key seurch_sk_<prefix>
```

Only a key's prefix and a SHA-256 hash of its secret are stored, never the key
itself. Requests are rate-limited per key (`API_THROTTLE_BURST` /
`API_THROTTLE_SUSTAINED` in `.env`); exceeding a limit returns HTTP 429. Each
query endpoint counts towards the key owner's monthly search total (shown in
Settings); a web search counts once per engine in scope (so `engine=brave,mojeek`
adds two), every other endpoint counts as one. The meta endpoints (`/`,
`status/`, `key/`) do not count.

**Endpoints** (all under `/api/v1/`, all return JSON):

| Endpoint | Description |
|----------|-------------|
| `web/`, `images/`, `news/`, `videos/` | Search. Params: `q`, `engine` (`brave`/`mojeek`/`marginalia`/`staan`/`all`), `safe`, `lang`, `page`, `date` (`d`/`w`/`m`/`y`). |
| `images/similar/` | Images similar to a given one, seeded from its caption (`?q=`) or the page `?query=`; `exclude_url` drops the opened image. |
| `maps/` | Geocode `?q=` or reverse-geocode `?lat=&lon=`. |
| `translate/` | Translate text (`?q=&target=&source=`); GET or POST. |
| `translate/languages/` | Available translation languages. |
| `instant/` | Instant answer for `?q=` (maths, units, weather, currency, …), or `null`. |
| `cards/` | Knowledge cards for `?q=` (Wikipedia, TheTVDB, TripAdvisor, Stack Exchange). |
| `suggest/` | Autocomplete suggestions for `?q=`. |
| `status/` | Upstream provider status. Absent (404) when `STATUS_PAGE_ENABLED=false`. |
| `key/` | Details of the key making the request. |

The in-app developer page at `/api/` links to the documentation site and the
key manager.

## Internationalization

The UI ships in English, French, German, Spanish, Italian, Portuguese and
Dutch. Users pick their interface language under **Settings → Language** (or
from the switcher on the login page).

Translations live in `locale/<lang>/LC_MESSAGES/django.po`. The compiled
`.mo` catalogs are build artifacts (gitignored), so they must be generated
before the translated UI works:

```bash
make messages          # compile .po → .mo (runs automatically on `make run`/`make setup`)
make messages-extract  # after adding new {% trans %} strings, refresh the .po files
```

`compilemessages` requires GNU gettext (`msgfmt`); the Docker image and CI
install it automatically. The `.po` files follow the standard gettext layout
and are Weblate-compatible.

## Commit messages and releases

Commits follow [Conventional Commits](https://www.conventionalcommits.org/),
enforced and automated with [commitizen](https://commitizen-tools.github.io/commitizen/)
(configured under `[tool.commitizen]` in `pyproject.toml`).

```bash
make hooks     # one-off per clone: point core.hooksPath at .githooks
make commit    # compose a commit message interactively
```

`make hooks` installs a `commit-msg` hook that rejects a message commitizen
cannot parse; `git commit --no-verify` skips it for the odd case that needs it.
The same check runs in CI over a pull request's commits, so a branch that
never ran `make hooks` still gets caught before merge.

A message is `type(scope): summary`, where `type` is one of `feat`, `fix`,
`docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore` or
`revert` - for example `fix(cards): send TripAdvisor its own locale codes`.
The type is what drives the version bump and where the line lands in the
changelog, so it is worth getting right.

### Cutting a release

The version lives in `[project].version` in `pyproject.toml` and is currently
`0.0.0`, the pre-release baseline: nothing has been tagged yet.

```bash
make changelog   # preview what the unreleased commits will produce
make bump        # rewrite the version, write CHANGELOG.md, commit and tag
git push --follow-tags origin main
```

`cz bump` reads the commits since the last tag and picks the increment
itself - `fix:` bumps the patch, `feat:` the minor. `major_version_zero` is
on, so a breaking change bumps the minor rather than jumping to 1.0.0 while
the project is still pre-1.0. Tags are annotated and named `v$version`
(`v0.0.1`), which is the pattern `docker.yml` builds images for.

The first release is a patch bump off the `0.0.0` baseline, which needs to be
asked for explicitly (there is no previous tag for commitizen to measure
against):

```bash
uv run cz bump --increment PATCH   # 0.0.0 → 0.0.1, tagged v0.0.1
git push --follow-tags origin main
```

Pushing the tag is what publishes the release image; see below.

## CI/CD

GitHub Actions workflows are in `.github/workflows/`:

- **`ci.yml`**, on every push/PR, runs `ruff` linting and the Django test suite
  (`test` job), and rebuilds the Tailwind CSS from source to fail if the
  committed stylesheets are stale (`css` job). On pull requests it also checks
  that every commit message parses as a Conventional Commit (`commits` job)
- **`docker.yml`**, builds the production image and pushes it to the GitHub
  Container Registry (`ghcr.io`) on pushes to `main` and on `v*` tags

Pushing again to a branch cancels that branch's own in-flight `ci.yml` run;
runs on `main` are never cancelled, so every commit there is verified on its
own.

`.github/dependabot.yml` proposes weekly updates for all three things with a
committed lockfile: Python (`uv.lock`), the workflows' actions, and the npm
toolchain (`package-lock.json`). A Font Awesome bump also needs `make fonts`
and a Tailwind bump `make css`, with the regenerated files committed - the
`css` job fails on a stale stylesheet either way.

### Published images

The registry is `ghcr.io/<owner>/<repo>` - for this repository,
`ghcr.io/seurch-eu/seurch`. A build tags the image by what triggered it:

| Trigger | Tags |
|---------|------|
| Push to `main` | `latest`, `main`, `sha-<short sha>` |
| Push of tag `v0.0.1` | `0.0.1`, `0.0`, `sha-<short sha>` |

So `latest` always tracks the tip of `main`, while a version tag is
immutable-by-convention and is what a deployment should pin. Every build is
additionally addressable by commit through its `sha-` tag. The running
version is surfaced in the footer: the workflow passes the ref and commit in
as the `GIT_REF`/`GIT_SHA` build args the `Dockerfile` bakes into the image.

Pushing an image needs no secret beyond the automatic `GITHUB_TOKEN`; the job
requests `packages: write` for it. The package is private on first publish -
make it public from the repository's *Packages* settings if the images are
meant to be pullable anonymously.

```bash
docker pull ghcr.io/seurch-eu/seurch:latest
docker pull ghcr.io/seurch-eu/seurch:0.0.1
```

## Docker

```bash
docker build -t seurch .
docker run -p 8000:8000 \
  -e SECRET_KEY=your-secret \
  -e DATABASE_URL=postgres://... \
  -e BRAVE_API_KEY=your-key \
  -e BRAVE_SUGGEST_API_KEY=your-key \
  -e MOJEEK_API_KEY=your-key \
  -e MARGINALIA_API_KEY=public \
  -e STAAN_API_KEY=your-key \
  seurch
```

The image uses `gunicorn` with 2 workers and serves its own static files via
[WhiteNoise](https://whitenoise.readthedocs.io/) (compressed and hashed). It
compiles the translation catalogs and collects static files at build time; on
start it applies database migrations, then listens on port 8000. It answers a
health check at `/up`.

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
  under CC BY-SA.
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
own terms - see [Search providers](#search-providers) and
[Frontend](#frontend-tailwind-css--font-awesome).

