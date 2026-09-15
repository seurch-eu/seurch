FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# See .github/workflows/docker.yml
ARG GIT_REF=
ARG GIT_SHA=
ENV GIT_REF=${GIT_REF} \
    GIT_SHA=${GIT_SHA}

RUN pip install --no-cache-dir uv

RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext cron \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

COPY . .

RUN chmod +x bin/docker-entrypoint

# Compile .po to .mo
RUN SECRET_KEY=build-only DEBUG=False DJANGO_SETTINGS_MODULE=config.settings \
    python manage.py compilemessages

RUN SECRET_KEY=build-only DEBUG=False DJANGO_SETTINGS_MODULE=config.settings \
    python manage.py collectstatic --no-input

EXPOSE 8000

ENTRYPOINT ["/app/bin/docker-entrypoint"]

CMD ["gunicorn", \
     "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--logger-class", "config.gunicorn.RedactingLogger"]
