#!/usr/bin/env bash
# Generate a self-signed TLS certificate for the dev reverse proxy.
#
#   ./scripts/dev-tls-cert.sh netbox-dev.example.com
#   ./scripts/dev-tls-cert.sh 10.20.0.15          # an IP works too
#
# Writes compose/dev-tls/{dev.crt,dev.key} (gitignored) and prints the .env
# lines to set. Browsers will warn about the self-signed CA — that is expected
# for dev; the point is encrypting credentials in transit, not proving identity.
set -euo pipefail
cd "$(dirname "$0")/.."

host=${1:-}
if [ -z "$host" ]; then
  echo "usage: $0 <hostname-or-ip>" >&2
  exit 2
fi

out=compose/dev-tls
mkdir -p "$out"

# An IP has to go in the SAN as an IP entry, not a DNS one, or clients reject it.
if printf '%s' "$host" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
  san="IP:$host"
else
  san="DNS:$host"
fi

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$out/dev.key" -out "$out/dev.crt" \
  -days 825 -subj "/CN=$host" -addext "subjectAltName=$san" 2>/dev/null

chmod 600 "$out/dev.key"
echo "wrote $out/dev.crt and $out/dev.key (CN=$host, SAN=$san)"

port=${DEV_PROXY_PORT:-8443}
cat <<EOF

Add these to .env, then bring the proxy up:

  BIND_ADDRESS=0.0.0.0
  DEV_HOSTNAME=$host
  DEV_PROXY_PORT=$port
  CSRF_TRUSTED_ORIGINS=https://$host:$port
  COMPOSE_FILE=docker-compose.yml:compose/dev.yml:compose/dev-proxy.yml

  docker compose up -d

Then browse to https://$host:$port
EOF
