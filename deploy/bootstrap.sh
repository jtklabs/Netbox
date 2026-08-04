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
#   .env                 — COMPOSE_FILE=docker-compose.yml:compose/prod.yml[:compose/discovery.yml],
#                          VERSION, diode/discovery block, DISCOVERY_* creds
#   netbox.env           — prod copy of env/netbox.env: fresh SECRET_KEY,
#                          API_TOKEN_PEPPER_1, REDIS_* passwords (SECRET_KEY must
#                          never change between redeploys)
#   prod.env             — from env/prod.env.example: RDS, S3, SSO settings
#   client-credentials.json  — diode OAuth clients (if discovery runs here)
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
    pw=$(grep "^${key}=" "$SECRETS_DIR/netbox.env" | tail -1 | cut -d= -f2-)
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

# client-credentials.json is different: its DIRECTORY is bind-mounted into the
# diode containers, and a symlink inside a bind mount is resolved against the
# CONTAINER's filesystem, where the data disk path does not exist — it just sees
# a dangling link and reports "no such file". So copy it, every boot, so edits
# on the data disk still propagate.
if [ -f "$SECRETS_DIR/client-credentials.json" ]; then
  mkdir -p "$REPO_DIR/discovery/oauth2/client"
  dest="$REPO_DIR/discovery/oauth2/client/client-credentials.json"
  # Remove first: if an earlier version of this script left a symlink here,
  # `cp` sees source and destination as the same file and silently does
  # nothing, leaving the broken link in place.
  rm -f "$dest"
  cp "$SECRETS_DIR/client-credentials.json" "$dest"
fi
log "secrets linked from $SECRETS_DIR"

# Bind-mounted config must be readable by non-root container users (diode-auth
# is uid 100, NetBox uid 999). Secrets created by the operator under a strict
# umask would otherwise fail with "permission denied" inside the container.
for p in "$REPO_DIR/configuration" "$REPO_DIR/discovery/oauth2" \
         "$REPO_DIR/discovery/nginx" "$REPO_DIR/discovery/agent.yaml" \
         "$SECRETS_DIR/client-credentials.json"; do
  [ -e "$p" ] || continue
  chmod -R a+rX "$p" 2>/dev/null || true
done

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

# --- 3. Obtain images and start ---------------------------------------------
cd "$REPO_DIR"
if grep -q '^PROD_IMAGE=' .env && [ -n "$(grep '^PROD_IMAGE=' .env | cut -d= -f2)" ]; then
  log "pulling pinned images"
  docker compose pull --quiet || log "WARN: pull failed, will try cached/local images"
else
  log "PROD_IMAGE unset — building image locally (slower boot; prefer baking it into the AMI with scripts/prod-build.sh)"
  docker compose build
fi
docker compose up -d

# Probe the address the stack is actually published on. Apache is on another
# host, so prod binds to this instance's private address — loopback would not
# answer and the gate below would fail a perfectly healthy boot.
if [ -z "${HEALTH_URL:-}" ]; then
  bind=$(grep -h '^BIND_ADDRESS=' "$SECRETS_DIR/prod.env" .env 2>/dev/null | tail -1 | cut -d= -f2-)
  case "$bind" in
    ''|0.0.0.0) bind=127.0.0.1 ;;
  esac
  HEALTH_URL="http://${bind}:8080/netbox/login/"
fi
log "compose up issued; waiting for NetBox health at $HEALTH_URL"

# --- 4. Gate on health -------------------------------------------------------
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "$HEALTH_URL"; then
    log "OK: NetBox is serving"
    exit 0
  fi
  sleep 10
done
log "FATAL: NetBox did not become healthy within 10 minutes"
docker compose ps || true
exit 1
