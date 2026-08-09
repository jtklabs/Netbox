#!/usr/bin/env bash
# Boot-time bootstrap for the 30-day AMI redeploy cycle. Idempotent — safe to
# run on every boot (wired via deploy/user-data.sh or the systemd unit).
#
# Expects:
#   - this repo at $REPO_DIR (baked into the AMI, or cloned by the AMI pipeline)
#   - docker engine + compose plugin installed in the AMI
#   - the persistent data disk attached, filesystem labeled $DATA_LABEL
#   - secrets prepared ONCE on the data disk (see FIRST-BOOT below)
set -euo pipefail

# Optional overrides without editing this file. Bake it into the AMI or write it
# from user-data — the repo and /etc are both replaced on every redeploy, so
# anything set here must come from one of those two, not be edited in place.
#   DATA_MOUNT=/mnt/data_disk
#   DATA_LABEL=NETBOXDATA
#   REPO_DIR=/opt/netbox
[ -f /etc/netbox-deploy.conf ] && . /etc/netbox-deploy.conf

REPO_DIR=${REPO_DIR:-/opt/netbox}
DATA_LABEL=${DATA_LABEL:-NETBOXDATA}
DATA_MOUNT=${DATA_MOUNT:-/mnt/data_disk}
SECRETS_DIR=${SECRETS_DIR:-$DATA_MOUNT/netbox-secrets}

log() { echo "[netbox-bootstrap] $(date -Is) $*"; }

# --- 0. Compose v2 is required ---------------------------------------------
# A stock Ubuntu install often provides only the standalone `docker-compose`
# v1 binary, which is end-of-life and cannot parse these files (pull_policy,
# service_healthy conditions, ${VAR:?} defaults). Fail here with a clear
# instruction rather than on a confusing YAML error later.
if ! docker compose version >/dev/null 2>&1; then
  log "FATAL: Docker Compose v2 is not available."
  if command -v docker-compose >/dev/null 2>&1; then
    log "       Found the legacy v1 binary ($(docker-compose --version 2>&1 | head -1))."
    log "       v1 is end-of-life and cannot parse this project's compose files."
  fi
  log "       Install it:  sudo apt-get install -y docker-compose-v2"
  exit 1
fi

# --- 1. Make sure the data disk is mounted ---------------------------------
# If fstab (or cloud-init) already mounted it, nothing to do. Otherwise mount by
# label. Either way we refuse to continue unless $DATA_MOUNT is a real mount
# point — writing secrets to the root filesystem because the disk failed to
# attach would silently lose them at the next redeploy.
if mountpoint -q "$DATA_MOUNT"; then
  log "data disk already mounted at $DATA_MOUNT"
else
  log "$DATA_MOUNT is not mounted; looking for a filesystem labeled $DATA_LABEL"
  for _ in $(seq 1 30); do
    DEV=$(blkid -L "$DATA_LABEL" 2>/dev/null || true)
    [ -n "$DEV" ] && break
    sleep 5
  done
  DEV=$(blkid -L "$DATA_LABEL" 2>/dev/null || true)
  if [ -z "$DEV" ]; then
    log "FATAL: $DATA_MOUNT is not a mount point and no filesystem labeled"
    log "       $DATA_LABEL was found. Is the data disk attached? If it mounts"
    log "       somewhere else, set DATA_MOUNT in /etc/netbox-deploy.conf."
    exit 1
  fi
  mkdir -p "$DATA_MOUNT"
  mount "$DEV" "$DATA_MOUNT"
  log "mounted $DEV at $DATA_MOUNT"
fi

# --- 2. Link secrets from the data disk into the repo ----------------------
# FIRST-BOOT (one time, manual): create $SECRETS_DIR containing
#   .env                 — COMPOSE_FILE=docker-compose.yml:compose/prod.yml, VERSION
#   netbox.env           — prod copy of env/netbox.env: fresh SECRET_KEY,
#                          API_TOKEN_PEPPER_1, REDIS_* passwords (SECRET_KEY must
#                          never change between redeploys)
#   prod.env             — from env/prod.env.example: RDS, S3, SSO settings
required=(".env" "netbox.env" "prod.env")
for f in "${required[@]}"; do
  if [ ! -f "$SECRETS_DIR/$f" ]; then
    log "FATAL: $SECRETS_DIR/$f missing — complete the FIRST-BOOT steps in this script's header"
    exit 1
  fi
done
# Env files are parsed by the compose CLI on the HOST, so a symlink is fine.
ln -sf "$SECRETS_DIR/.env" "$REPO_DIR/.env"
ln -sf "$SECRETS_DIR/netbox.env" "$REPO_DIR/env/netbox.env"
ln -sf "$SECRETS_DIR/prod.env" "$REPO_DIR/env/prod.env"
# redis.env / redis-cache.env only carry a password that must match the one
# already in netbox.env, so derive them rather than making the operator create
# them consistently by hand. Missing them is otherwise invisible here and
# surfaces later as a compose "env file not found" failure.
for pair in "redis.env:REDIS_PASSWORD" "redis-cache.env:REDIS_CACHE_PASSWORD"; do
  f=${pair%%:*}; key=${pair##*:}
  if [ ! -f "$SECRETS_DIR/$f" ]; then
    pw=$(grep "^${key}=" "$SECRETS_DIR/netbox.env" | tail -1 | cut -d= -f2- || true)
    if [ -z "$pw" ]; then
      log "FATAL: $SECRETS_DIR/$f is missing and ${key} is not set in netbox.env,"
      log "       so it cannot be derived. See docs/FIRST-BOOT.md step 2."
      exit 1
    fi
    printf 'REDIS_PASSWORD=%s\n' "$pw" > "$SECRETS_DIR/$f"
    log "generated $SECRETS_DIR/$f from netbox.env"
  fi
  ln -sf "$SECRETS_DIR/$f" "$REPO_DIR/env/$f"
done

log "secrets linked from $SECRETS_DIR"

# Bind-mounted config must be readable by the non-root container user (NetBox
# runs as uid 999). Secrets created by the operator under a strict umask would
# otherwise fail with "permission denied" inside the container.
for p in "$REPO_DIR/configuration"; do
  [ -e "$p" ] || continue
  chmod -R a+rX "$p" 2>/dev/null || true
done

# --- 2a. Every file named in COMPOSE_FILE must exist -------------------------
# COMPOSE_FILE lives in the data-disk .env, which `git pull` never touches, so
# it can name an overlay this repo no longer ships — exactly what happened when
# the Diode discovery stack was removed and prod's .env still ended in
# ":compose/discovery.yml". Compose's own error for that names the missing path
# but not the reason, at the point where every service fails at once. Say it
# here instead, before anything is started.
compose_chain=$(grep '^COMPOSE_FILE=' "$SECRETS_DIR/.env" 2>/dev/null | tail -1 | cut -d= -f2- || true)
if [ -n "$compose_chain" ]; then
  missing_files=""
  # shellcheck disable=SC2001
  for f in $(echo "$compose_chain" | tr ':' ' '); do
    [ -f "$REPO_DIR/$f" ] || missing_files="$missing_files $f"
  done
  if [ -n "$missing_files" ]; then
    log "FATAL: COMPOSE_FILE in $SECRETS_DIR/.env names files this repo does"
    log "       not contain:$missing_files"
    log "       That file is on the data disk, so a git pull cannot fix it."
    log "       Edit COMPOSE_FILE there to drop the missing entries."
    exit 1
  fi
fi

# --- 2b. Catch settings placed in the wrong file ----------------------------
# Compose substitutes ${...} from the project .env only; env_file entries are
# passed to the container and are invisible to substitution. A compose-level
# setting put in prod.env is therefore silently ignored, and for BIND_ADDRESS
# that means NetBox quietly stays on loopback where Apache cannot reach it.
for v in BIND_ADDRESS PROD_IMAGE PROD_PULL_POLICY COMPOSE_FILE VERSION; do
  if grep -q "^${v}=" "$SECRETS_DIR/prod.env" 2>/dev/null; then
    log "WARN: ${v} is set in prod.env, where compose cannot see it."
    log "      Move it to $SECRETS_DIR/.env or it will have no effect."
  fi
done
if ! grep -q '^BIND_ADDRESS=' "$SECRETS_DIR/.env" 2>/dev/null; then
  log "WARN: BIND_ADDRESS is not set in $SECRETS_DIR/.env — NetBox will publish"
  log "      on 127.0.0.1 only, which a remote Apache server cannot reach."
fi

# --- 2c. Warn when the live prod.env has drifted from the template -----------
# prod.env lives on the data disk, deliberately outside the repo, so `git pull`
# updates env/prod.env.example and never touches the file actually in use. Any
# setting added to the template after this disk was prepared is therefore
# silently absent, and NetBox quietly falls back to its own default. That is
# exactly how a missing REMOTE_AUTH_AUTO_CREATE_GROUPS left every SSO user with
# zero permissions and no error anywhere. Report the drift rather than assuming
# whoever pulled also re-read the template.
# `|| true` throughout: a grep that matches nothing returns 1, which under
# `set -e` would abort the whole boot over a diagnostic.
if [ -f "$REPO_DIR/env/prod.env.example" ]; then
  tmpl_keys=$(grep -oE '^[A-Z_][A-Z0-9_]*=' "$REPO_DIR/env/prod.env.example" 2>/dev/null | tr -d '=' | sort -u || true)
  missing=""
  for key in $tmpl_keys; do
    grep -qE "^[[:space:]]*${key}=" "$SECRETS_DIR/prod.env" 2>/dev/null || missing="$missing $key"
  done
  if [ -n "$missing" ]; then
    log "WARN: settings present in env/prod.env.example but MISSING from"
    log "      $SECRETS_DIR/prod.env (a git pull cannot update that file):"
    for key in $missing; do log "        $key"; done
    log "      NetBox falls back to its own default for each. Review them —"
    log "      REMOTE_AUTH_* defaults in particular fail silently rather than loudly."
  fi
fi

# --- 3. Obtain images and start ---------------------------------------------
cd "$REPO_DIR"
if grep -q '^PROD_IMAGE=' .env && [ -n "$(grep '^PROD_IMAGE=' .env | cut -d= -f2)" ]; then
  log "pulling pinned images"
  docker compose pull --quiet || log "WARN: pull failed, will try cached/local images"
else
  # Ask for the netbox service's image specifically. `config --images` emits every
  # service's image in an order compose does not guarantee — taking the first one
  # inspects valkey about half the time, and finding valkey present would skip the
  # build even when the NetBox image is missing. `config --images netbox` does not
  # filter by service on compose 2.40 either, so read it out of the merged config.
  img=$(docker compose config 2>/dev/null | awk '
    /^  netbox:$/ {in_svc=1; next}
    in_svc && /^  [^ ]/ {in_svc=0}
    in_svc && $1 == "image:" {print $2; exit}
  ' || true)
  if [ -n "$img" ] && docker image inspect "$img" >/dev/null 2>&1; then
    log "image $img already present (baked into the AMI) — not rebuilding"
  else
    log "building image locally (slower boot; prefer baking it in with scripts/prod-build.sh)"
    docker compose build
  fi
fi
# Not fatal: netbox-worker waits on netbox's healthcheck, so compose can report
# a dependency failure during a first boot that is progressing normally. The
# health gate below is the real verdict and gives a far better diagnostic.
docker compose up -d || log "compose reported a dependency not ready — continuing to the health gate"

# Probe the address the stack is actually published on. Apache is on another
# host, so prod binds to this instance's private address — loopback would not
# answer and the gate below would fail a perfectly healthy boot.
if [ -z "${HEALTH_URL:-}" ]; then
  bind=$(grep -h '^BIND_ADDRESS=' "$SECRETS_DIR/prod.env" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"' | awk '{print $1}' || true)
  case "$bind" in
    ''|0.0.0.0) bind=127.0.0.1 ;;
  esac
  HEALTH_URL="http://${bind}:8080/netbox/login/"
fi
log "compose up issued; waiting for NetBox health at $HEALTH_URL"

# --- 4. Gate on health -------------------------------------------------------
# Host header matters: prod.env restricts ALLOWED_HOSTS, so probing by IP would
# get a Django 400 and this gate could never pass on a healthy stack. localhost
# is in the shipped ALLOWED_HOSTS. --max-time keeps a filtered port from turning
# the 10-minute budget into hours on libcurl's default connect timeout.
for _ in $(seq 1 60); do
  if curl -fsS --max-time 5 -H 'Host: localhost' -o /dev/null "$HEALTH_URL"; then
    log "OK: NetBox is serving"
    exit 0
  fi
  sleep 10
done
log "FATAL: NetBox did not become healthy within 10 minutes"
docker compose ps || true
exit 1
