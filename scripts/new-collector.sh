#!/usr/bin/env bash
# Mint credentials for a new remote collector.
#
# Run this on the CENTRAL server (where the Diode stack runs). It creates an
# OAuth2 client scoped to `diode:ingest` and prints the env block to drop into
# the remote box's collector.env.
#
#   ./scripts/new-collector.sh site-nyc
#
# Each collector gets its own client so one site can be revoked without
# touching the others:
#
#   ./scripts/new-collector.sh --list
#   ./scripts/new-collector.sh --revoke <client_id>
set -euo pipefail
cd "$(dirname "$0")/.."

DIODE_URL=${DIODE_URL:-http://127.0.0.1:${DIODE_NGINX_PORT:-8090}}

if [ ! -f .env ]; then
  echo "error: .env not found — run scripts/init-dev-env.sh first" >&2
  exit 1
fi
admin_secret=$(grep '^NETBOX_TO_DIODE_CLIENT_SECRET=' .env | tail -1 | cut -d= -f2-)
if [ -z "$admin_secret" ]; then
  echo "error: NETBOX_TO_DIODE_CLIENT_SECRET missing from .env" >&2
  exit 1
fi

json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$1',''))"; }

token=$(curl -sS -X POST "$DIODE_URL/diode/auth/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "grant_type=client_credentials&client_id=netbox-to-diode&client_secret=${admin_secret}&scope=diode:read diode:write" \
  | json access_token)
if [ -z "$token" ]; then
  echo "error: could not get an admin token from $DIODE_URL" >&2
  echo "       is the Diode stack up?  docker compose ps diode-nginx" >&2
  exit 1
fi

case "${1:-}" in
  --list)
    curl -sS "$DIODE_URL/diode/auth/clients" -H "Authorization: Bearer $token" \
      | python3 -m json.tool
    exit 0
    ;;
  --revoke)
    [ -n "${2:-}" ] || { echo "usage: $0 --revoke <client_id>" >&2; exit 2; }
    code=$(curl -sS -o /dev/null -w '%{http_code}' -X DELETE \
      "$DIODE_URL/diode/auth/clients/$2" -H "Authorization: Bearer $token")
    if [ "$code" = "200" ] || [ "$code" = "204" ]; then
      echo "revoked $2"
    else
      echo "revoke failed (HTTP $code)" >&2; exit 1
    fi
    exit 0
    ;;
  '')
    echo "usage: $0 <collector-name> | --list | --revoke <client_id>" >&2
    exit 2
    ;;
esac

name=$1
response=$(curl -sS -X POST "$DIODE_URL/diode/auth/clients" \
  -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
  -d "{\"client_name\":\"${name}\",\"scope\":\"diode:ingest\"}")
client_id=$(printf '%s' "$response" | json client_id)
client_secret=$(printf '%s' "$response" | json client_secret)

if [ -z "$client_id" ] || [ -z "$client_secret" ]; then
  echo "error: client creation failed: $response" >&2
  exit 1
fi

cat <<EOF

Collector "$name" created. Put this in the remote box's collector.env
(the secret is shown once — it cannot be retrieved again):

  COLLECTOR_NAME=$name
  DIODE_CLIENT_ID=$client_id
  DIODE_CLIENT_SECRET=$client_secret
  DIODE_TARGET=grpcs://<diode-host>:8443/diode

Then on the remote box:
  ./install.sh            # from the collector/ directory of this repo

Revoke later with:
  ./scripts/new-collector.sh --revoke $client_id
EOF
