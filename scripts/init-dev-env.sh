#!/usr/bin/env bash
# Generate local dev env files from the committed .example templates, filling in
# fresh random secrets. Idempotent: existing files are left untouched.
set -euo pipefail
cd "$(dirname "$0")/.."

gen() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }

if [ ! -f .env ]; then
  cp .env.example .env
  printf 'SUPERUSER_PASSWORD=%s\n' "$(gen 20)" >>.env
  echo "created .env (dev superuser password inside)"
fi

if [ ! -f env/netbox.env ]; then
  DB_PASSWORD=$(gen 24)
  REDIS_PASSWORD=$(gen 24)
  REDIS_CACHE_PASSWORD=$(gen 24)
  sed -e "s/{{SECRET_KEY}}/$(gen 60)/" \
    -e "s/{{API_TOKEN_PEPPER}}/$(gen 50)/" \
    -e "s/{{DB_PASSWORD}}/$DB_PASSWORD/" \
    -e "s/{{REDIS_PASSWORD}}/$REDIS_PASSWORD/" \
    -e "s/{{REDIS_CACHE_PASSWORD}}/$REDIS_CACHE_PASSWORD/" \
    env/netbox.env.example >env/netbox.env
  sed "s/{{DB_PASSWORD}}/$DB_PASSWORD/" env/postgres.env.example >env/postgres.env
  sed "s/{{REDIS_PASSWORD}}/$REDIS_PASSWORD/" env/redis.env.example >env/redis.env
  sed "s/{{REDIS_CACHE_PASSWORD}}/$REDIS_CACHE_PASSWORD/" env/redis-cache.env.example >env/redis-cache.env
  echo "created env/*.env with fresh secrets"
fi

echo "dev env ready — next: docker compose build && docker compose up -d"
