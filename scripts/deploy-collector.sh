#!/usr/bin/env bash
# Build (and optionally push) a ready-to-run collector for a remote site.
#
# Run on the CENTRAL server. For each collector it mints a scoped ingest
# credential, assembles a self-contained bundle with that credential and the
# site's policies already filled in, and — if you give it an SSH target —
# copies the bundle over and starts it.
#
#   # one site, produce a bundle to copy by hand
#   ./scripts/deploy-collector.sh site-nyc \
#       --target grpc://10.90.0.1:8090/diode --site "NYC Branch"
#
#   # one site, push and start it over SSH
#   ./scripts/deploy-collector.sh site-nyc \
#       --target grpc://10.90.0.1:8090/diode --site "NYC Branch" \
#       --host ubuntu@10.20.0.5
#
#   # a whole fleet from a file (name / ssh-host / site per line)
#   ./scripts/deploy-collector.sh --fleet collectors.txt \
#       --target grpc://10.90.0.1:8090/diode
#
# Bundles land in dist/ and contain live credentials — gitignored, mode 600.
set -euo pipefail
cd "$(dirname "$0")/.."

DIODE_URL=${DIODE_URL:-http://127.0.0.1:${DIODE_NGINX_PORT:-8090}}
OUT_DIR=${OUT_DIR:-dist}

name='' target='' site='' host='' fleet='' rotate=false
while [ $# -gt 0 ]; do
  case "$1" in
    --target) target=$2; shift 2 ;;
    --site) site=$2; shift 2 ;;
    --host) host=$2; shift 2 ;;
    --fleet) fleet=$2; shift 2 ;;
    --rotate) rotate=true; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    -*) echo "unknown option: $1" >&2; exit 2 ;;
    *) name=$1; shift ;;
  esac
done

[ -n "$target" ] || { echo "error: --target is required (the Diode endpoint as the REMOTE box sees it)" >&2; exit 2; }
[ -n "$name" ] || [ -n "$fleet" ] || { echo "error: give a collector name or --fleet <file>" >&2; exit 2; }

json() { python3 -c "import json,sys; print(json.load(sys.stdin).get('$1',''))"; }

# --- admin token, reused across the whole fleet ----------------------------
# -r, not -f: in prod .env is a root-owned symlink onto the data disk, and an
# unprivileged run sees "missing" when the truth is "not yours to read".
[ -r .env ] || { echo "error: .env not found or not readable.
  prod: it is root-owned on the data disk — run this script with sudo.
  dev:  run scripts/init-dev-env.sh first." >&2; exit 1; }
admin_secret=$(grep '^NETBOX_TO_DIODE_CLIENT_SECRET=' .env | tail -1 | cut -d= -f2-)
[ -n "$admin_secret" ] || { echo "error: NETBOX_TO_DIODE_CLIENT_SECRET missing from .env.
  prod: add the diode/discovery block from .env.example to
  /mnt/data_disk/netbox-secrets/.env — enabling discovery is a .env change,
  and the boot-time drift check only covers prod.env, not this file." >&2; exit 1; }

token=$(curl -sS -X POST "$DIODE_URL/diode/auth/token" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "grant_type=client_credentials&client_id=netbox-to-diode&client_secret=${admin_secret}&scope=diode:read diode:write" \
  | json access_token)
[ -n "$token" ] || { echo "error: could not authenticate to Diode at $DIODE_URL — is the stack up?" >&2; exit 1; }

# Device credentials default to the central ones; each bundle can be edited.
dev_user=$(grep '^DISCOVERY_SSH_USER=' .env | tail -1 | cut -d= -f2- || true)
dev_pass=$(grep '^DISCOVERY_SSH_PASS=' .env | tail -1 | cut -d= -f2- || true)
snmp_user=$(grep '^DISCOVERY_SNMP_USER=' .env | tail -1 | cut -d= -f2- || true)
snmp_auth=$(grep '^DISCOVERY_SNMP_AUTH_PASS=' .env | tail -1 | cut -d= -f2- || true)
snmp_priv=$(grep '^DISCOVERY_SNMP_PRIV_PASS=' .env | tail -1 | cut -d= -f2- || true)

build_one() {
  local cname=$1 chost=$2 csite=$3
  echo "==> $cname"

  # Refuse to silently create a second credential for a name that already has
  # one — two live clients for one site is confusing to audit and revoke.
  local existing
  existing=$(curl -sS "$DIODE_URL/diode/auth/clients" -H "Authorization: Bearer $token" \
    | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: d=[]
items = d if isinstance(d,list) else (d.get('data') or d.get('clients') or [])
print(' '.join(c.get('client_id','') for c in items if c.get('client_name')=='$cname'))" 2>/dev/null || true)
  if [ -n "$existing" ] && [ "$rotate" != true ]; then
    echo "    already has credentials ($existing)."
    echo "    Re-run with --rotate to issue a new one, then revoke the old:"
    echo "      ./scripts/new-collector.sh --revoke $existing"
    return 0
  fi

  local resp client_id client_secret
  resp=$(curl -sS -X POST "$DIODE_URL/diode/auth/clients" \
    -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
    -d "{\"client_name\":\"${cname}\",\"scope\":\"diode:ingest\"}")
  client_id=$(printf '%s' "$resp" | json client_id)
  client_secret=$(printf '%s' "$resp" | json client_secret)
  [ -n "$client_id" ] && [ -n "$client_secret" ] || {
    echo "    ERROR: credential creation failed: $resp" >&2; return 1; }
  echo "    credential: $client_id"

  local stage="$OUT_DIR/$cname"
  rm -rf "$stage"; mkdir -p "$stage"
  cp -R collector/. "$stage/"
  # Never ship build artefacts or a previous run's generated config.
  rm -f "$stage/collector.env" "$stage/policies.yaml" "$stage/agent.yaml" "$stage/workers.txt"
  find "$stage" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  find "$stage" -name '*.pyc' -delete 2>/dev/null || true
  # Belt and braces: the bundle is extracted on a box where nobody will think to
  # chmod, so guarantee install.sh is runnable regardless of the source mode.
  chmod +x "$stage/install.sh"

  cat > "$stage/collector.env" <<EOF
# Generated by scripts/deploy-collector.sh for "$cname".
# Contains a live ingest credential — treat as a secret.
COLLECTOR_NAME=$cname
DIODE_CLIENT_ID=$client_id
DIODE_CLIENT_SECRET=$client_secret
DIODE_TARGET=$target

# Device credentials, defaulted from the central .env. Edit if this site differs.
SNMP_USER=$snmp_user
SNMP_AUTH_PASS=$snmp_auth
SNMP_PRIV_PASS=$snmp_priv
DEVICE_USER=$dev_user
DEVICE_PASS=$dev_pass

ORB_AGENT_TAG=${ORB_AGENT_TAG:-2.11.0}
EOF
  chmod 600 "$stage/collector.env"

  # Seed policies with this site's name so discovered gear lands in the right
  # NetBox Site; the targets still have to be filled in per site.
  sed "s|site: Branch A|site: ${csite:-$cname}|g" \
    collector/policies.yaml.example > "$stage/policies.yaml"

  local bundle="$OUT_DIR/collector-$cname.tar.gz"
  tar -czf "$bundle" -C "$OUT_DIR" "$cname"
  chmod 600 "$bundle"
  rm -rf "$stage"
  echo "    bundle: $bundle"

  if [ -n "$chost" ]; then
    echo "    pushing to $chost"
    scp -q "$bundle" "$chost:/tmp/collector-$cname.tar.gz"
    # shellcheck disable=SC2029
    ssh "$chost" "set -e
      sudo mkdir -p /opt/netbox-collector
      sudo tar -xzf /tmp/collector-$cname.tar.gz -C /tmp
      sudo cp -R /tmp/$cname/. /opt/netbox-collector/
      sudo rm -rf /tmp/$cname /tmp/collector-$cname.tar.gz
      cd /opt/netbox-collector && sudo ./install.sh"
    echo "    started on $chost"
  else
    echo "    copy it over, then: tar xzf … && cd $cname && ./install.sh"
  fi
}

mkdir -p "$OUT_DIR"

if [ -n "$fleet" ]; then
  [ -f "$fleet" ] || { echo "error: fleet file $fleet not found" >&2; exit 1; }
  # name  [ssh-host]  [site...]   — blank lines and # comments ignored
  while read -r fname fhost fsite; do
    case "$fname" in ''|'#'*) continue ;; esac
    [ "$fhost" = "-" ] && fhost=''
    build_one "$fname" "$fhost" "$fsite"
  done < "$fleet"
else
  build_one "$name" "$host" "$site"
fi

echo ""
echo "Bundles are in $OUT_DIR/ and contain live credentials — do not commit them."
echo "List or revoke collectors with ./scripts/new-collector.sh --list | --revoke <id>"
