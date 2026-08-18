#!/bin/sh
set -eu

fail() {
  printf '%s\n' "PharmaDrone AI production startup refused: $1" >&2
  exit 1
}

[ -n "${DATABASE_URL:-}" ] || fail "DATABASE_URL is required."
case "$DATABASE_URL" in
  postgres://*|postgresql://*|postgresql+psycopg://*) ;;
  *) fail "DATABASE_URL must use PostgreSQL; SQLite is local/test only." ;;
esac

case "${DATABASE_BACKEND:-}" in
  ""|postgres|postgresql) ;;
  *) fail "DATABASE_BACKEND must not select SQLite in production." ;;
esac

[ -n "${PHARMADRONE_AI_AUTH_SECRET:-}" ] || fail "PHARMADRONE_AI_AUTH_SECRET is required."
[ "${#PHARMADRONE_AI_AUTH_SECRET}" -ge 32 ] || fail "PHARMADRONE_AI_AUTH_SECRET must contain at least 32 characters."
[ -n "${PHARMADRONE_AI_ALLOWED_ORIGINS:-}" ] || fail "PHARMADRONE_AI_ALLOWED_ORIGINS is required."

export APP_ENV=production
if [ "$#" -eq 0 ]; then
  set -- python -m uvicorn pharmadrone_ai.app:app --host 0.0.0.0 --port 8000
fi
exec "$@"
