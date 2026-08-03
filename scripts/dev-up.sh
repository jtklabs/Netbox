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

# Health is always checked over loopback; BIND_ADDRESS only affects who else
# can reach it.
url=http://127.0.0.1:8080
echo "==> waiting for NetBox at $url"
echo '    first boot runs all database migrations and can take ~10 minutes'
for _ in $(seq 1 120); do
  if curl -fsS -o /dev/null "$url/login/" 2>/dev/null; then
    echo ''
    echo "NetBox is up: $url"
    echo "  username: admin"
    echo "  password: $(grep '^SUPERUSER_PASSWORD=' .env | cut -d= -f2-)"
    bind=$(grep '^BIND_ADDRESS=' .env | tail -1 | cut -d= -f2-)
    if [ -z "$bind" ] || [ "$bind" = "127.0.0.1" ]; then
      echo ''
      echo 'Bound to loopback only — not reachable from other machines.'
      echo 'To open it up, set BIND_ADDRESS (and CSRF_TRUSTED_ORIGINS) in .env;'
      echo 'for HTTPS see ./scripts/dev-tls-cert.sh and compose/dev-proxy.yml.'
    else
      echo "  reachable on: $bind"
    fi
    exit 0
  fi
  sleep 10
done

echo '' >&2
echo 'NetBox did not come up within 20 minutes. Check:' >&2
echo '  docker compose ps' >&2
echo '  docker compose logs netbox' >&2
exit 1
