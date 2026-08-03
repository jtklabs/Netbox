#!/usr/bin/env bash
# One command to bring up a dev environment from a fresh clone: generates the
# env files, builds the plugin image, starts the stack and waits for NetBox.
#
# Everything it does is idempotent, so it is also the right way to restart.
set -euo pipefail
cd "$(dirname "$0")/.."

echo '==> preparing env files'
./scripts/init-dev-env.sh

echo '==> building the NetBox image (plugins are compiled in)'
docker compose build

echo '==> starting the stack'
docker compose up -d

url=http://127.0.0.1:8080
echo "==> waiting for NetBox at $url"
echo '    first boot runs all database migrations and can take ~10 minutes'
for _ in $(seq 1 120); do
  if curl -fsS -o /dev/null "$url/login/" 2>/dev/null; then
    echo ''
    echo "NetBox is up: $url"
    echo "  username: admin"
    echo "  password: $(grep '^SUPERUSER_PASSWORD=' .env | cut -d= -f2-)"
    exit 0
  fi
  sleep 10
done

echo '' >&2
echo 'NetBox did not come up within 20 minutes. Check:' >&2
echo '  docker compose ps' >&2
echo '  docker compose logs netbox' >&2
exit 1
