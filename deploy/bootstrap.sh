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

REPO_DIR=${REPO_DIR:-/opt/netbox}
DATA_LABEL=${DATA_LABEL:-NETBOXDATA}
DATA_MOUNT=${DATA_MOUNT:-/data}
SECRETS_DIR=${SECRETS_DIR:-$DATA_MOUNT/netbox-secrets}
HEALTH_URL=${HEALTH_URL:-http://127.0.0.1:8080/netbox/login/}

log() { echo "[netbox-bootstrap] $(date -Is) $*"; }

# --- 1. Mount the data disk by filesystem label ----------------------------
if ! mountpoint -q "$DATA_MOUNT"; then
  log "waiting for data disk labeled $DATA_LABEL"
  for _ in $(seq 1 30); do
    DEV=$(blkid -L "$DATA_LABEL" 2>/dev/null || true)
    [ -n "$DEV" ] && break
    sleep 5
  done
  DEV=$(blkid -L "$DATA_LABEL" 2>/dev/null || true)
  if [ -z "$DEV" ]; then
    log "FATAL: no filesystem labeled $DATA_LABEL found — is the data disk attached?"
    exit 1
  fi
  mkdir -p "$DATA_MOUNT"
  mount "$DEV" "$DATA_MOUNT"
  log "mounted $DEV at $DATA_MOUNT"
fi

# --- 2. Link secrets from the data disk into the repo ----------------------
# FIRST-BOOT (one time, manual): create $SECRETS_DIR containing
#   .env                 — COMPOSE_FILE=docker-compose.yml:compose/prod.yml[:compose/discovery.yml],
#                          VERSION, PROD_IMAGE (if ECR), diode/discovery block, DISCOVERY_* creds
#   netbox.env           — prod copy of env/netbox.env: fresh SECRET_KEY,
#                          API_TOKEN_PEPPER_1, REDIS_* passwords (SECRET_KEY must
#                          never change between redeploys)
#   prod.env             — from env/prod.env.example: RDS, S3, SSO settings
#   client-credentials.json  — diode OAuth clients (if discovery runs here)
#   saml/                — mellon.key, mellon.cert, idp-metadata.xml (Apache)
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
for f in redis.env redis-cache.env; do
  [ -f "$SECRETS_DIR/$f" ] && ln -sf "$SECRETS_DIR/$f" "$REPO_DIR/env/$f"
done

# client-credentials.json is different: its DIRECTORY is bind-mounted into the
# diode containers, and a symlink inside a bind mount is resolved against the
# CONTAINER's filesystem, where /data does not exist — the container just sees
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

# --- 3. Obtain images and start ---------------------------------------------
cd "$REPO_DIR"
if grep -q '^PROD_IMAGE=' .env && [ -n "$(grep '^PROD_IMAGE=' .env | cut -d= -f2)" ]; then
  log "pulling pinned images"
  docker compose pull --quiet || log "WARN: pull failed, will try cached/local images"
else
  log "PROD_IMAGE unset — building image locally (slower boot; consider ECR)"
  docker compose build
fi
docker compose up -d
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
