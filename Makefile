.DEFAULT_GOAL := help

.PHONY: help setup sync up down logs migrate makemigrations run shell test superuser reset-db mailpit bangs refresh-currency css css-install css-watch fonts messages messages-extract hooks commit check-commits changelog bump

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

setup: sync hooks up migrate messages ## Bootstrap the dev environment from scratch

sync: ## Install / update Python dependencies
	uv sync

up: ## Start dev services, PostgreSQL + Mailpit (detached)
	podman compose up -d

down: ## Stop dev services
	podman compose down

logs: ## Tail dev service logs
	podman compose logs -f

mailpit: ## Open Mailpit web UI in the browser (http://localhost:8025)
	@echo "Mailpit → http://localhost:8025"
	@xdg-open http://localhost:8025 2>/dev/null || open http://localhost:8025 2>/dev/null || true

migrate: ## Apply database migrations (and the shared cache table)
	uv run python manage.py migrate
	uv run python manage.py createcachetable

makemigrations: ## Generate new migrations
	uv run python manage.py makemigrations

run: messages ## Start the Django dev server
	uv run python manage.py runserver

messages: ## Compile translation catalogs (.po → .mo) so the UI is translated
	uv run python manage.py compilemessages

messages-extract: ## Re-scan templates/code for translatable strings and update .po files
	uv run python manage.py makemessages --all --no-location --no-obsolete --ignore=.venv

shell: ## Open the Django shell
	uv run python manage.py shell

test: ## Run the test suite
	uv run python manage.py test search accounts instant api

superuser: ## Create a superuser account
	uv run python manage.py createsuperuser

reset-db: ## Wipe and recreate the database volume
	podman compose down -v
	podman compose up -d

bangs: ## Download bang definitions from kagisearch/bangs
	uv run python manage.py fetch_bangs

refresh-currency: ## Refresh cached exchange rates (instant answers) + prune cache
	uv run python manage.py refresh_currency

css-install: ## Install the Tailwind toolchain (one-off, needed to edit styles)
	npm ci

fonts: ## Vendor self-hosted Font Awesome assets from node_modules → static (re-run after bumping the package)
	npm run build:fa

css: ## Compile Tailwind sources (assets/*.css) → committed stylesheets
	npm run build:css

css-watch: ## Recompile Tailwind CSS on every save (Ctrl-C to stop)
	@npm run watch:instant & trap 'kill $$!' INT TERM EXIT; npm run watch:app

hooks: ## Enable the repo's git hooks (commit-msg runs `cz check`)
	git config core.hooksPath .githooks
	@echo "git hooks → .githooks"

commit: ## Write a Conventional Commit interactively (commitizen)
	uv run cz commit

check-commits: ## Validate the commit messages on this branch against main
	@if git log --oneline origin/main..HEAD | grep -q .; then \
		uv run cz check --rev-range origin/main..HEAD; \
	else \
		echo "No commits since origin/main."; \
	fi

changelog: ## Preview the changelog entries for the unreleased commits
	uv run cz changelog --unreleased-version "Unreleased" --dry-run

bump: ## Bump the version, write CHANGELOG.md and create the release tag
	uv run cz bump
	@echo "Push the release with: git push --follow-tags origin main"
