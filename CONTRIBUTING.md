# Contributing to Seurch

Thanks for considering a contribution. Seurch is a privacy-first metasearch
engine: it queries several upstream providers, merges what they return and
renders it without tracking the person searching. That goal shapes most of the
review feedback you will get, so it is worth keeping in mind.

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Getting set up

[README.md](README.md#dev-stack-setup) has the full walkthrough. The short
version:

```bash
cp .env.example .env   # defaults match the compose services; add API keys
make setup             # deps, git hooks, dev services, migrations, catalogs
make run               # http://localhost:8000
```

You need Python 3.13+, [uv](https://docs.astral.sh/uv/), Podman (or Docker) for
the compose services (PostgreSQL, Mailpit and LibreTranslate), and `gettext` for
the translation catalogs. Without provider API keys the app still runs; the
affected tabs show a configuration notice instead of results.

`make setup` runs `make hooks`, which points `core.hooksPath` at `.githooks/`
so your commit messages are checked locally. If you skip it, CI will catch the
same problems later.

## Before you open a pull request

Run what CI runs. All three gates are cheap locally:

```bash
uv run ruff check .    # lint
make test              # Django test suite (needs PostgreSQL up)
make css               # recompile Tailwind; commit the result if it changed
```

CI (`.github/workflows/ci.yml`) has three jobs:

| Job | What it does |
|-----|--------------|
| `test` | `ruff check`, `compilemessages`, migrations, then the test suite |
| `css` | Rebuilds the Tailwind CSS and **fails if the committed stylesheets are stale** |
| `commits` | On pull requests, checks every commit message is a Conventional Commit |

The compiled stylesheets are committed to the repository, so a change to any
template or to `assets/*.css` usually means running `make css` and committing
the regenerated files. Tailwind scans the whole project for class candidates,
so even prose can affect the output — if `make css` produces a diff you did not
expect, that is why, and the diff still needs committing.

If you touched translatable strings, run `make messages-extract` and update the
catalogs in `locale/`; `make messages` compiles them. Keep an existing
translation when you only reword the surrounding markup.

## Commit messages

The project uses [Conventional Commits](https://www.conventionalcommits.org/),
enforced by [commitizen](https://commitizen-tools.github.io/commitizen/):

```
type(scope): summary in the imperative
```

Common types here are `feat`, `fix`, `refactor`, `docs`, `test`, `build` and
`ci`. The scope is usually the Django app you touched (`search`, `web`,
`images`, `cards`, `instant`, `accounts`, `api`, …).

```bash
make commit          # write one interactively
make check-commits   # validate this branch against origin/main
```

A breaking change gets a `!` after the type/scope, or a `BREAKING CHANGE:`
footer. The version is still pre-1.0, so a breaking change bumps the minor.

Commit messages feed the changelog, so write the body for someone reading it in
six months: say what changed and why, not what the diff already shows.

## What we look for in a change

- **Keep the privacy promise.** Nothing should leak the searcher's IP, query or
  fingerprint to a third party that does not need it. External images go
  through the image proxy, new upstream calls go through `search/clients.py` so
  they get the shared timeout, logging and health recording.
- **Degrade, never 500.** A provider that is down, slow or unconfigured must
  leave the rest of the page working. The status page and `search/health.py`
  exist for this.
- **Work without JavaScript.** Every feature needs a server-rendered path. JS
  is progressive enhancement, not a requirement.
- **Respect provider terms.** Several providers constrain caching, hotlinking
  and attribution; see the *Search providers* and *Knowledge cards* sections of
  the README before adding or changing one.
  little between apps — follow the file you are in.
- **Keep the change scoped.** A fix and an unrelated refactor are two pull
  requests.

### Tests

Add coverage for behaviour you add or change. Note the *AI-generated code and
docs* section of the README: the test suite was LLM-generated and then
reviewed, so an assertion encodes what the generator understood the code to
promise. Where a test and the code disagree about intended behaviour, work out
which is right rather than assuming it is the test — and fix or delete a test
that turns out to be pinning an accident, instead of bending the code to keep
it green.

```bash
make test                                            # everything CI runs
uv run python manage.py test search.tests.ClassName  # one class
```

## Reporting bugs and proposing features

Open an issue. For a bug, the useful things are what you did, what you
expected, what happened, and whether it reproduces with provider keys absent.
For anything large, open an issue before writing the code so the design can be
discussed before you spend time on it.

Please do not open a public issue for a security problem. Report it privately
through GitHub's
[security advisories](https://github.com/search-project/search/security/advisories/new)
instead, and give the maintainers time to ship a fix before disclosing it.

## Licensing

Seurch is licensed under the [GNU AGPL v3](LICENSE). Contributions are accepted
under the same licence: by opening a pull request you agree your work is
distributed under the AGPL-3.0. 
