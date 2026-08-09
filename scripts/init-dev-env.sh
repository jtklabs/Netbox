#!/usr/bin/env bash
# Generate the local dev env files from the committed .example templates,
# filling in fresh random secrets.
#
# Safe to re-run: every file is created only if missing, and the companion
# files are derived from env/netbox.env so their shared passwords always match
# even when only some of them exist.
set -euo pipefail
cd "$(dirname "$0")/.."

gen() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }

# Read a KEY=value out of an env file.
getval() { grep "^$2=" "$1" | tail -1 | cut -d= -f2-; }

created=()

# --- .env: selects the compose overlay chain, so nothing works without it ----
if [ ! -f .env ]; then
  cp .env.example .env
  printf 'SUPERUSER_PASSWORD=%s\n' "$(gen 20)" >>.env
  created+=(".env")
else
  # Existing .env: add any settings introduced since it was generated. Without
  # this, a new variable silently falls back to its compose default — which is
  # how BIND_ADDRESS ended up pinned to loopback on existing checkouts.
  added=()
  while IFS= read -r line; do
    case "$line" in
      ''|'#'*) continue ;;
      *=*) key=${line%%=*} ;;
      *) continue ;;
    esac
    if ! grep -q "^${key}=" .env; then
      printf '%s\n' "$line" >>.env
      added+=("$key")
    fi
  done <.env.example
  if [ ${#added[@]} -gt 0 ]; then
    created+=(".env: added ${added[*]}")
  fi
fi

# --- env/netbox.env is the source of truth for the shared passwords ---------
if [ ! -f env/netbox.env ]; then
  sed -e "s/{{SECRET_KEY}}/$(gen 60)/" \
    -e "s/{{API_TOKEN_PEPPER}}/$(gen 50)/" \
    -e "s/{{DB_PASSWORD}}/$(gen 24)/" \
    -e "s/{{REDIS_PASSWORD}}/$(gen 24)/" \
    -e "s/{{REDIS_CACHE_PASSWORD}}/$(gen 24)/" \
    env/netbox.env.example >env/netbox.env
  created+=("env/netbox.env")
fi

# --- companions, derived so they cannot drift out of sync -------------------
db_password=$(getval env/netbox.env DB_PASSWORD)
redis_password=$(getval env/netbox.env REDIS_PASSWORD)
redis_cache_password=$(getval env/netbox.env REDIS_CACHE_PASSWORD)

if [ ! -f env/postgres.env ]; then
  sed "s/{{DB_PASSWORD}}/$db_password/" env/postgres.env.example >env/postgres.env
  created+=("env/postgres.env")
fi
if [ ! -f env/redis.env ]; then
  sed "s/{{REDIS_PASSWORD}}/$redis_password/" env/redis.env.example >env/redis.env
  created+=("env/redis.env")
fi
if [ ! -f env/redis-cache.env ]; then
  sed "s/{{REDIS_CACHE_PASSWORD}}/$redis_cache_password/" \
    env/redis-cache.env.example >env/redis-cache.env
  created+=("env/redis-cache.env")
fi



# --- restore executable bits ------------------------------------------------
# A ZIP download (GitHub's "Download ZIP", or any zip round-trip) does not carry
# the executable bit, so every script arrives mode 644 and `./script.sh` fails
# with "permission denied". Fix them here, since this script is the documented
# entry point and can always be run as `bash scripts/init-dev-env.sh`.
restored=0
for s in scripts/*.sh deploy/*.sh collector/install.sh; do
  [ -f "$s" ] || continue
  if [ ! -x "$s" ]; then chmod +x "$s"; restored=$((restored + 1)); fi
done
[ "$restored" -gt 0 ] && created+=("restored +x on $restored script(s)")

# --- make bind-mounted config readable inside the containers ----------------
# Not every container runs as root: NetBox
# is uid 999. On Linux a bind mount keeps the host's ownership and mode, so a
# config file created under a restrictive umask (0077) is unreadable to those
# uids and the container dies with "permission denied" — which then restart-
# loops anything depending on it. macOS masks this, so it only bites on Linux.
#
# These files are configuration, not secrets, with one exception noted below.
normalise_perms() {
  local path=$1
  [ -e "$path" ] || return 0
  if [ -d "$path" ]; then
    chmod a+rX "$path" 2>/dev/null || true
    find "$path" -type d -exec chmod a+rX {} + 2>/dev/null || true
    find "$path" -type f -exec chmod a+r {} + 2>/dev/null || true
  else
    chmod a+r "$path" 2>/dev/null || true
  fi
}

for p in configuration; do
  normalise_perms "$p"
done

# --- verify: every file compose needs must now exist ------------------------
missing=()
for f in .env env/netbox.env env/postgres.env env/redis.env env/redis-cache.env; do
  [ -f "$f" ] || missing+=("$f")
done
if [ ${#missing[@]} -gt 0 ]; then
  echo "ERROR: still missing after setup: ${missing[*]}" >&2
  exit 1
fi

if [ ${#created[@]} -gt 0 ]; then
  printf 'created: %s\n' "${created[*]}"
else
  echo 'nothing to do — dev env files already present'
fi
echo 'dev env ready — next: docker compose build && docker compose up -d'
