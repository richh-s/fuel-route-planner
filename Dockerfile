# Pinned by digest for reproducible builds; Dependabot proposes updates.
FROM python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATABASE_PATH=/app/var/db.sqlite3

WORKDIR /app

# Every package, including transitive ones, is pinned and hash-checked.
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

# Public-domain US Census Gazetteer files used for offline geocoding. Files already in data/
# (checksum-verified) are used as they are; missing ones are downloaded, with retries.
COPY scripts/fetch_gazetteer.py scripts/
COPY data/ data/
RUN python scripts/fetch_gazetteer.py

COPY . .

# Build the database into the image so containers start ready to serve.
# The secret key here is only for these build steps; supply a real one at runtime.
RUN mkdir -p var \
    && export DJANGO_SECRET_KEY=build-only \
    && python manage.py collectstatic --noinput \
    && python manage.py migrate --noinput \
    && python manage.py load_places \
    && python manage.py import_fuel_stations

RUN useradd --create-home --uid 1000 app && chown -R app:app /app/var
USER app

ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "scripts/healthcheck.py"]

CMD ["gunicorn", "-c", "gunicorn.conf.py", "config.wsgi:application"]
