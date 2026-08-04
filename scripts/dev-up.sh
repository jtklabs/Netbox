#!/usr/bin/env bash
# Bring up a dev environment. One command, no manual .env editing.
#
#   ./scripts/dev-up.sh                        local only, http://127.0.0.1:8080
#   ./scripts/dev-up.sh netbox-dev.corp.com    reachable over HTTPS at /netbox
#   ./scripts/dev-up.sh 10.20.0.15             an IP works too
#   ./scripts/dev-up.sh --local                go back to local-only
#
# Given a hostname or IP it does everything itself: generates the TLS
# certificate, sets every variable it needs in .env, and enables the reverse
# proxy that serves NetBox at /netbox — the same path production uses.
#
# Idempotent: re-run it to restart, or with a different host to move it.
set -euo pipefail
cd "$(dirname "$0")/.."

host=${1:-}
port=${DEV_PROXY_PORT:-8443}

# Set a key in .env, replacing any existing value. Done in Python because the
# values here contain slashes and colons, which make sed delimiters fragile.
set_env() {
  python3 - "$1" "$2" <<'PY'
import re, sys
key, value = sys.argv[1], sys.argv[2]
line = f'{key}={value}'
src = open('.env').read()
if re.search(rf'(?m)^{re.escape(key)}=', src):
    src = re.sub(rf'(?m)^{re.escape(key)}=.*$', line.replace('\\', '\\\\'), src)
else:
    src = src.rstrip('\n') + '\n' + line + '\n'
open('.env', 'w').write(src)
PY
}

echo '==> preparing env files'
bash scripts/init-dev-env.sh

base_chain='docker-compose.yml:compose/dev.yml:compose/discovery.yml'

if [ "$host" = "--local" ]; then
  echo '==> configuring for local access only'
  set_env BIND_ADDRESS 127.0.0.1
  set_env CSRF_TRUSTED_ORIGINS ''
  set_env COMPOSE_FILE "$base_chain"
  host=''
elif [ -n "$host" ]; then
  echo "==> configuring remote access as $host"
  # Bind to all interfaces. Narrowing to one address is better practice, but
  # Docker Desktop cannot bind the host's LAN address from inside its VM
  # ("cannot assign requested address"), so this keeps one command working on
  # both a Linux server and a Mac. On Linux you can set BIND_ADDRESS to a
  # specific interface address in .env afterwards and it will be respected.
  set_env BIND_ADDRESS 0.0.0.0
  set_env DEV_HOSTNAME "$host"
  set_env DEV_PROXY_PORT "$port"
  # Django rejects the login POST without this the moment NetBox is reached by
  # any name other than localhost.
  set_env CSRF_TRUSTED_ORIGINS "https://${host}:${port}"
  set_env COMPOSE_FILE "${base_chain}:compose/dev-proxy.yml"

  # Regenerate the certificate when it is missing or was issued for another name.
  cert=compose/dev-tls/dev.crt
  if [ ! -f "$cert" ] || ! openssl x509 -in "$cert" -noout -text 2>/dev/null | grep -q "$host"; then
    echo "==> generating a TLS certificate for $host"
    bash scripts/dev-tls-cert.sh "$host" >/dev/null
  else
    echo '==> reusing the existing TLS certificate'
  fi
fi

echo '==> building the NetBox image (plugins are compiled in)'
docker compose build

echo '==> starting the stack'
# Not fatal: netbox-worker waits on netbox's healthcheck, which does not pass
# until migrations finish, so compose reports a dependency failure on a first
# boot that is going perfectly well. The wait loop below is the real verdict
# and gives a far better diagnostic if something is actually wrong.
docker compose up -d || echo '    (compose reported a dependency not ready — continuing)'

# Probe the address the stack is actually published on: binding to a specific
# LAN address means loopback will NOT answer. The path moves to /netbox when
# the proxy is enabled.
if [ -n "$host" ]; then
  probe="https://${host}:${port}/netbox/login/"
  public="https://${host}:${port}/netbox/"
  curl_opts=(-sk)
else
  probe='http://127.0.0.1:8080/login/'
  public='http://127.0.0.1:8080'
  curl_opts=(-s)
fi

echo "==> waiting for NetBox"
echo '    first boot runs all database migrations and can take ~10 minutes'
for _ in $(seq 1 120); do
  if curl "${curl_opts[@]}" -f -o /dev/null "$probe" 2>/dev/null; then
    echo ''
    echo "NetBox is up: $public"
    echo "  username: admin"
    echo "  password: $(grep '^SUPERUSER_PASSWORD=' .env | cut -d= -f2-)"
    if [ -n "$host" ]; then
      echo ''
      echo '  The certificate is self-signed, so browsers will warn once.'
      echo '  Served at /netbox to match production.'
    else
      echo ''
      echo '  Local only. To reach it from another machine:'
      echo "    ./scripts/dev-up.sh <hostname-or-ip>"
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
