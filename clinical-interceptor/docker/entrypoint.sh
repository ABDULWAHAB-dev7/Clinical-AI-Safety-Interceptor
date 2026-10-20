#!/bin/sh
# Applies migrations, then starts the API. Fails if the database is unreachable or a migration fails.
set -eu
: "${INTERCEPTOR_DATABASE_URL:?INTERCEPTOR_DATABASE_URL is required}"
: "${INTERCEPTOR_PSEUDO_KEY:?INTERCEPTOR_PSEUDO_KEY is required}"
alembic upgrade head
exec uvicorn interceptor.api.app:build_default_app --factory --host 0.0.0.0 --port "${PORT:-8000}" \
     --proxy-headers --forwarded-allow-ips="${INTERCEPTOR_TRUSTED_PROXIES:-127.0.0.1}"
