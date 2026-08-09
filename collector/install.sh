#!/usr/bin/env bash
# Stand up a remote collector. Run this ON the remote box, from this directory.
#
#   ./install.sh              set up, validate and start
#   ./install.sh --check      validate configuration and connectivity only
#
# Requires: docker + compose plugin, and outbound reach to the Diode endpoint.
# Deliberately does NOT need PostgreSQL, Redis, or any NetBox credential.
set -euo pipefail
cd "$(dirname "$0")"

check_only=false
[ "${1:-}" = "--check" ] && check_only=true

fail() { echo "ERROR: $*" >&2; exit 1; }

# --- 1. config files --------------------------------------------------------
if [ ! -f collector.env ]; then
  cp collector.env.example collector.env
  echo "created collector.env — fill it in, then re-run ./install.sh"
  echo "  get DIODE_CLIENT_ID/SECRET from the central server:"
  echo "    ./scripts/new-collector.sh <collector-name>"
  exit 1
fi
if [ ! -f policies.yaml ]; then
  cp policies.yaml.example policies.yaml
  echo "created policies.yaml — edit it to describe what this site scans, then re-run"
  exit 1
fi

# shellcheck disable=SC1091
set -a; . ./collector.env; set +a

for var in COLLECTOR_NAME DIODE_CLIENT_ID DIODE_CLIENT_SECRET DIODE_TARGET; do
  [ -n "${!var:-}" ] || fail "$var is empty in collector.env"
done

# --- 2. render agent.yaml from the template + policies ----------------------
# The agent wants a single file; keeping policies separate makes them easy to
# edit and diff, so they are appended here under `policies:`.
{
  cat agent.yaml.template
  echo '  policies:'
  sed 's/^/    /' policies.yaml
} > agent.yaml.new
# Did the rendered config actually change? The agent reads agent.yaml once, at
# start, and the file arrives via a bind mount — so `docker compose up -d` sees
# an unchanged container definition and does nothing, leaving the old config
# running. Without this, editing policies.yaml and re-running install.sh looks
# like it worked and silently did not.
config_changed=false
if [ ! -f agent.yaml ] || ! cmp -s agent.yaml.new agent.yaml; then
  config_changed=true
fi
mv agent.yaml.new agent.yaml

python3 -c "
import sys
try:
    import yaml
except ImportError:
    print('  (pyyaml not installed locally — the agent will validate instead)')
    sys.exit(0)
doc = yaml.safe_load(open('agent.yaml'))
policies = (doc.get('orb') or {}).get('policies') or {}
if not policies:
    sys.exit('no policies found after rendering — is policies.yaml empty?')
print('  agent config parses, %d backend policy group(s)' % len(policies))
total = scheduled = 0
for backend, group in policies.items():
    if not isinstance(group, dict):
        continue
    for name, pol in group.items():
        total += 1
        cfg = (pol or {}).get('config') or {}
        if cfg.get('schedule'):
            scheduled += 1
open('.schedule-state', 'w').write('%d %d\n' % (total, scheduled))  # newline: read returns 1 at EOF without it
" || fail "rendered agent.yaml is invalid — check policies.yaml indentation"

# --- 3. connectivity to Diode ----------------------------------------------
host_port=$(printf '%s' "$DIODE_TARGET" | sed -E 's#^grpcs?://##; s#/.*$##')
host=${host_port%%:*}
port=${host_port##*:}
if [ "$port" = "$host" ]; then
  # No explicit port in the target: gRPC defaults to 80, gRPC-over-TLS to 443.
  case "$DIODE_TARGET" in
    grpcs://*) port=443 ;;
    *) port=80 ;;
  esac
fi
if command -v nc >/dev/null 2>&1; then
  if nc -z -w 5 "$host" "$port" 2>/dev/null; then
    echo "  Diode endpoint reachable: $host:$port"
  else
    fail "cannot reach the Diode endpoint at $host:$port
       Check the tunnel/firewall, and that DIODE_TARGET in collector.env is
       the address of the central server as seen from THIS box."
  fi
fi

if [ "$check_only" = true ]; then
  echo 'checks passed'
  exit 0
fi

# --- 4. run -----------------------------------------------------------------
# A scheduled policy does NOT run at agent start, so "restart to rescan" is
# actively wrong when every policy carries a schedule — which is exactly what
# the shipped example does. Counting with grep got this wrong: the example
# carries commented-out `schedule:` lines in its illustrative blocks, which
# read as "unscheduled policies exist". Use the parsed YAML instead, which
# knows the difference between a policy and a comment.
read -r total sched < .schedule-state 2>/dev/null || { total=0; sched=0; }
rm -f .schedule-state
if [ "$total" -gt 0 ] && [ "$sched" -eq "$total" ]; then
  rescan_hint="  NOTE: every policy is scheduled, so restarting the agent will NOT scan —
        it waits for the next cron match. To scan immediately, comment out the
        schedule: lines in policies.yaml and re-run ./install.sh."
else
  rescan_hint="  rescan:   docker compose restart orb-agent   (unscheduled policies run at start)"
fi

echo '==> starting the collector'
docker compose up -d
# up -d cannot notice a changed bind-mounted file, so restart explicitly when
# the config moved. Harmless when the container was only just created.
if [ "$config_changed" = true ]; then
  echo '    agent config changed — restarting the agent to load it'
  docker compose restart orb-agent
fi
sleep 5
docker compose ps
cat <<EOF

Collector "$COLLECTOR_NAME" started.

  logs:     docker compose logs -f orb-agent
  stop:     docker compose down
$rescan_hint

Discovered data appears in NetBox tagged with agent_name "$COLLECTOR_NAME".
EOF
