#!/usr/bin/env bash
# Enable the discovery stack (Diode + orb-agent) on a PROD instance.
#
# Dev gets all of this from scripts/init-dev-env.sh; prod keeps its env on the
# data disk where that script never looks. This is the prod equivalent, and it
# exists because the by-hand version has a trap in the middle: the three Diode
# OAuth client secrets live in BOTH the data-disk .env and
# client-credentials.json, and if the copies disagree the components fail
# against each other with auth errors long after anyone remembers editing
# either file. Everything here is generated once, from one source.
#
# Idempotent: existing values are never regenerated; re-running fills in only
# what is missing and verifies the two files agree.
#
# After it runs:  sudo systemctl restart netbox-compose   (bootstrap re-links
# and brings the stack up with the overlay), then open the Diode port to your
# collectors — and nothing else; it is plaintext gRPC.
set -euo pipefail
cd "$(dirname "$0")/.."

SECRETS_DIR=${SECRETS_DIR:-/mnt/data_disk/netbox-secrets}
ENV_FILE="$SECRETS_DIR/.env"

log() { echo "[enable-discovery] $*"; }
gen() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }
getval() { grep "^$2=" "$1" | tail -1 | cut -d= -f2-; }

[ "$(id -u)" -eq 0 ] || { echo "Run as root — the data-disk secrets are root-owned." >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "FATAL: $ENV_FILE not found — this is the PROD enabler; complete docs/FIRST-BOOT.md first (dev uses scripts/init-dev-env.sh)." >&2; exit 1; }

# --- 1. Ensure every discovery variable exists (per-variable, not per-block) --
# Appending an all-or-nothing block cannot heal a partial state, and a partial
# state is exactly what a failed edit or an older version of this script
# leaves behind. Each variable is checked on its own; existing values are
# never touched. Secrets are generated only when their line is absent.
if ! grep -q '^# --- discovery stack' "$ENV_FILE"; then
  printf '\n# --- discovery stack (added by scripts/enable-discovery-prod.sh) -------------\n' >> "$ENV_FILE"
fi
added=0
ensure() {
  grep -q "^$1=" "$ENV_FILE" || { printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; added=$((added+1)); }
}
ensure DIODE_TAG 2.1.0
ensure DIODE_NGINX_PORT 8090
# Diode's own redis + postgres (superuser AND app-level credentials — the
# containers refuse to initialise without the superuser password):
ensure REDIS_PASSWORD "$(gen 32)"
ensure REDIS_HOST diode-redis
ensure REDIS_PORT 6378
ensure REDIS_USERNAME ""
ensure LOGGING_LEVEL INFO
ensure SENTRY_DSN ""
ensure MIGRATION_ENABLED true
ensure POSTGRES_HOST diode-postgres
ensure POSTGRES_PORT 5432
ensure POSTGRES_USER postgres
ensure POSTGRES_PASSWORD "$(gen 32)"
ensure DIODE_POSTGRES_DB_NAME diode
ensure DIODE_POSTGRES_USER diode
ensure DIODE_POSTGRES_PASSWORD "$(gen 32)"
ensure TELEMETRY_ENVIRONMENT prod
ensure TELEMETRY_METRICS_EXPORTER prometheus
ensure TELEMETRY_TRACES_EXPORTER none
ensure HYDRA_POSTGRES_DB_NAME hydra
ensure HYDRA_POSTGRES_USER hydra
ensure HYDRA_POSTGRES_PASSWORD "$(gen 32)"
ensure HYDRA_STRATEGIES_ACCESS_TOKEN jwt
ensure HYDRA_STRATEGIES_REFRESH_TOKEN jwt
ensure HYDRA_STRATEGIES_JWT_SCOPE_CLAIM both
ensure HYDRA_TTL_ACCESS_TOKEN 1h
ensure HYDRA_OIDC_SUBJECT_IDENTIFIERS_SUPPORTED_TYPES public
ensure HYDRA_URLS_SELF_ISSUER http://127.0.0.1:4444
ensure HYDRA_SECRETS_SYSTEM_0 "$(gen 48)"
ensure AUTH_HTTP_PORT 8080
ensure OAUTH2_PUBLIC_SERVER_URL http://hydra:4444
ensure OAUTH2_ADMIN_SERVER_URL http://hydra:4445
ensure DIODE_AUTH_TOKEN_URL http://diode-auth:8080/token
ensure DIODE_TO_NETBOX_CLIENT_ID diode-to-netbox
ensure DIODE_TO_NETBOX_CLIENT_SECRET "$(gen 48)"
# BASE_PATH=netbox/ moves the plugin API — dev (no base path) uses /api/... .
ensure NETBOX_DIODE_PLUGIN_API_BASE_URL http://netbox:8080/netbox/api/plugins/diode
ensure NETBOX_DIODE_PLUGIN_SKIP_TLS_VERIFY false
ensure AUTO_APPLY_CHANGESETS true
ensure ENABLE_GRAPH_DB false
ensure INGESTION_LOG_PROCESSOR_BATCH_SIZE 50
ensure INGESTION_LOG_PROCESSOR_CONCURRENCY 1
ensure AUTO_APPLY_PROCESSOR_BATCH_SIZE 50
ensure AUTO_APPLY_PROCESSOR_CONCURRENCY 1
ensure NETBOX_TO_DIODE_CLIENT_SECRET "$(gen 48)"
ensure DIODE_INGEST_CLIENT_ID diode-ingest
ensure DIODE_INGEST_CLIENT_SECRET "$(gen 48)"
ensure DIODE_GRPC_TARGET grpc://diode-nginx:80/diode
# Device credentials for the CENTRAL agent (remote collectors carry their own;
# these also seed collector.env defaults in deploy-collector.sh bundles):
ensure DISCOVERY_SNMP_USER ""
ensure DISCOVERY_SNMP_AUTH_PASS ""
ensure DISCOVERY_SNMP_PRIV_PASS ""
ensure DISCOVERY_SSH_USER ""
ensure DISCOVERY_SSH_PASS ""
log "discovery variables: $added added, existing ones untouched"

# --- 1b. NetBox must accept its compose-internal name -------------------------
# The reconciler calls NetBox as http://netbox:8080/... — Host header "netbox".
# Dev never notices (ALLOWED_HOSTS=*), prod restricts the list, and the
# symptom is a bare Django 400 in the reconciler logs.
PROD_ENV="$SECRETS_DIR/prod.env"
if [ -f "$PROD_ENV" ]; then
  if grep -q '^ALLOWED_HOSTS=' "$PROD_ENV" && ! grep -q '^ALLOWED_HOSTS=.*\bnetbox\b' "$PROD_ENV"; then
    sed -i '/^ALLOWED_HOSTS=/ s/$/ netbox/' "$PROD_ENV"
    log "added the internal service name 'netbox' to ALLOWED_HOSTS in prod.env"
  fi
fi

# --- 2. Put the overlay into COMPOSE_FILE ------------------------------------
if grep -q '^COMPOSE_FILE=.*compose/discovery\.yml' "$ENV_FILE"; then
  log "COMPOSE_FILE already includes compose/discovery.yml"
else
  sed -i 's|^COMPOSE_FILE=.*|&:compose/discovery.yml|' "$ENV_FILE"
  log "appended :compose/discovery.yml to COMPOSE_FILE"
fi

# --- 3. client-credentials.json, from the SAME secrets ------------------------
CRED="$SECRETS_DIR/client-credentials.json"
if [ ! -f "$CRED" ]; then
  sed -e "s/{{DIODE_INGEST_CLIENT_SECRET}}/$(getval "$ENV_FILE" DIODE_INGEST_CLIENT_SECRET)/" \
      -e "s/{{DIODE_TO_NETBOX_CLIENT_SECRET}}/$(getval "$ENV_FILE" DIODE_TO_NETBOX_CLIENT_SECRET)/" \
      -e "s/{{NETBOX_TO_DIODE_CLIENT_SECRET}}/$(getval "$ENV_FILE" NETBOX_TO_DIODE_CLIENT_SECRET)/" \
      discovery/oauth2/client/client-credentials.json.example > "$CRED"
  chmod 600 "$CRED"
  log "generated $CRED"
fi

# --- 4. Verify the two files agree — the failure mode this script exists for --
mismatch=0
for key in DIODE_INGEST_CLIENT_SECRET DIODE_TO_NETBOX_CLIENT_SECRET NETBOX_TO_DIODE_CLIENT_SECRET; do
  v=$(getval "$ENV_FILE" "$key")
  if ! grep -qF "\"$v\"" "$CRED"; then
    log "FATAL: $key in .env does not match client-credentials.json."
    mismatch=1
  fi
done
if [ "$mismatch" -ne 0 ]; then
  log "       The components would reject each other with auth errors. Either"
  log "       delete $CRED and re-run (it regenerates from .env), or fix the"
  log "       differing values by hand. Refusing to continue."
  exit 1
fi

log "OK. Next:"
log "  1. sudo systemctl restart netbox-compose"
log "  2. sudo docker compose up -d diode-auth-bootstrap"
log "     (idempotent; the one-shot that registers the hydra OAuth clients can"
log "      race hydra's very first boot — re-running it cures reconciler"
log "      'invalid_client' errors)"
log "  3. verify:  docker ps | grep diode   (7 containers)  and"
log "     curl -sS http://127.0.0.1:${DIODE_NGINX_PORT:-8090}/diode/auth/token -o /dev/null -w '%{http_code}\\n'  -> 405/400, not connection refused"
log "  4. security group: open ${DIODE_NGINX_PORT:-8090}/tcp ONLY to collector IPs (plaintext gRPC)"
log "  5. mint collectors:  sudo ./scripts/deploy-collector.sh <name> --target grpc://<this-instance-ip>:8090/diode --site \"<NetBox Site>\""
