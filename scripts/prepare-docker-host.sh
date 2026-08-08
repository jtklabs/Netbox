#!/usr/bin/env bash
# Prepare a fresh Ubuntu 24 or RHEL 9 host to run this repo's Docker stacks
# (a remote collector, a test deployment, or the NetBox AMI bake itself):
# installs Docker Engine + Compose v2 and pins ALL Docker networking into the
# CGNAT range (100.64.0.0/10, RFC 6598).
#
# The CGNAT part is not optional here: Docker's defaults allocate from
# 172.17.0.0/12 (and then 192.168.0.0/16), which collides with this
# environment's real networks. The collision is nasty precisely because it is
# partial — the host still works, but any container traffic to a real
# 172.x.y.z host silently routes into the Docker bridge instead. Three
# separate allocators must all be moved:
#   * docker0 itself            -> "bip"
#   * every network Compose creates -> "default-address-pools"
#   * (both restart-persistent via /etc/docker/daemon.json)
#
# Idempotent: safe to re-run; it only restarts Docker when the config actually
# changed. Existing NETWORKS keep the subnet they were created with — the pool
# applies to new allocations — so this script warns about live 172.x networks
# and prints the recreate commands instead of destroying anything.
#
# Overrides (env): POOL_BASE=100.64.0.0/10  POOL_SIZE=24  BIP=100.64.0.1/24
#
# Optional collector-deploy access (see section 5): set DEPLOY_PUBKEY (and
# DEPLOY_USER) to let the central server's scripts/deploy-collector.sh push
# bundles here unattended.
set -euo pipefail

POOL_BASE=${POOL_BASE:-100.64.0.0/10}
POOL_SIZE=${POOL_SIZE:-24}
BIP=${BIP:-100.64.0.1/24}

log() { echo "[prepare-docker-host] $*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "This script must run as root (it installs packages and writes /etc/docker)." >&2
  exit 1
fi

# --- 0. Which distro is this? ------------------------------------------------
. /etc/os-release
FAMILY=""
case "${ID:-}" in
  ubuntu) [ "${VERSION_ID%%.*}" -ge 24 ] || log "WARN: built for Ubuntu 24, found ${VERSION_ID} — continuing"
          FAMILY=debian ;;
  rhel|rocky|almalinux|centos)
          [ "${VERSION_ID%%.*}" -ge 9 ] || log "WARN: built for EL9, found ${VERSION_ID} — continuing"
          FAMILY=rhel ;;
  *) case " ${ID_LIKE:-} " in
       *rhel*|*fedora*) FAMILY=rhel ;;
       *debian*)        FAMILY=debian ;;
       *) echo "Unsupported distro: ID=${ID:-?}. Handle Ubuntu 24 / RHEL 9 here." >&2; exit 1 ;;
     esac ;;
esac
log "distro: ${PRETTY_NAME:-$ID} (family: $FAMILY)"

# --- 1. Does anything on this host ALREADY use the CGNAT range? --------------
# The whole point of moving to 100.64/10 is escaping a conflict — do not walk
# into a different one. Tailscale is the classic tenant of this range.
if ip -4 route show 2>/dev/null | awk '{print $1}' | grep -qE '^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.'; then
  log "WARN: this host already has routes inside 100.64.0.0/10:"
  ip -4 route show | grep -E '^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.' | sed 's/^/    /'
  log "     Docker would collide with them. Override POOL_BASE/BIP to a clear"
  log "     sub-range (e.g. POOL_BASE=100.96.0.0/11) and re-run."
fi

# --- 2. Install Docker Engine + Compose v2 -----------------------------------
if [ "$FAMILY" = debian ]; then
  # Ubuntu's own archive: docker.io is current enough and needs no third-party
  # repo, and docker-compose-v2 is the same Compose plugin the bootstrap gate
  # requires. (The EOL v1 `docker-compose` binary cannot parse this repo's
  # compose files — deploy/bootstrap.sh refuses it explicitly.)
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q docker.io docker-compose-v2 docker-buildx python3 sudo
else
  # RHEL 9 ships podman, not Docker; Docker Engine comes from Docker's own
  # repository. podman-docker (a shim that fakes /usr/bin/docker) conflicts
  # with the real thing and must go first — only the shim is removed, podman
  # itself is left alone. --allowerasing lets dnf swap runc for containerd.io.
  dnf -y -q install dnf-plugins-core python3 sudo || dnf -y install dnf-plugins-core python3 sudo
  if rpm -q podman-docker >/dev/null 2>&1; then
    log "removing podman-docker (shim conflicts with Docker's own CLI)"
    dnf -y remove podman-docker
  fi
  dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
  dnf -y install --allowerasing docker-ce docker-ce-cli containerd.io docker-compose-plugin docker-buildx-plugin
  systemctl enable --now docker
  # firewalld coexists with docker-ce (it manages its own zone); inbound
  # rules for published ports are still on you if this host serves anything.
fi

# --- 3. Pin Docker's address space to CGNAT ----------------------------------
# Merge, never clobber: the host may already carry daemon.json settings
# (log drivers, proxies). Only our two keys are enforced.
mkdir -p /etc/docker
CHANGED=$(python3 - "$BIP" "$POOL_BASE" "$POOL_SIZE" <<'PY'
import json, os, sys, shutil, time
bip, base, size = sys.argv[1], sys.argv[2], int(sys.argv[3])
path = "/etc/docker/daemon.json"
cfg = {}
if os.path.exists(path):
    with open(path) as f:
        text = f.read().strip()
    cfg = json.loads(text) if text else {}
pools = [{"base": base, "size": size}]
if cfg.get("bip") == bip and cfg.get("default-address-pools") == pools:
    print("unchanged"); raise SystemExit
if os.path.exists(path):
    shutil.copy2(path, path + ".bak-" + time.strftime("%Y%m%d%H%M%S"))
cfg["bip"] = bip
cfg["default-address-pools"] = pools
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(cfg, f, indent=2); f.write("\n")
os.replace(tmp, path)
print("changed")
PY
)
if [ "$CHANGED" = changed ]; then
  log "daemon.json updated (bip=$BIP, pool=$POOL_BASE size /$POOL_SIZE) — restarting docker"
  systemctl restart docker
else
  log "daemon.json already correct — no restart needed"
fi
systemctl enable docker >/dev/null 2>&1 || true

# --- 4. Warn about networks created before the pool change -------------------
# Existing networks keep their original subnets; only NEW allocations come
# from the pool. A stack created before this script still sits on 172.x until
# it is recreated.
STALE=$(docker network ls --format '{{.Name}}' | grep -vE '^(bridge|host|none)$' | while read -r n; do
  docker network inspect "$n" --format '{{.Name}} {{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null
done | awk '$2 ~ /^172\.|^192\.168\./ {print "    " $0}')
if [ -n "$STALE" ]; then
  log "WARN: existing networks still on conflicting ranges (created before this change):"
  echo "$STALE"
  log "     Recreate each stack to move it:  docker compose down && docker compose up -d"
  log "     (or for orphans:  docker network rm <name>)"
fi

# --- 5. Optional: SSH access for unattended collector deployment --------------
# scripts/deploy-collector.sh --host pushes the bundle with plain `ssh` and
# runs the install with `sudo` in a session that has no TTY. Unattended, that
# requires two things of the target user: the central server's public key in
# authorized_keys, and PASSWORDLESS sudo — a sudo password prompt in a
# non-interactive session is not answered, it is a failure. Both are set up
# here only when DEPLOY_PUBKEY is provided:
#
#   central server, once:   ssh-keygen -t ed25519 -N ''     # default identity
#   each collector box:     sudo DEPLOY_PUBKEY="ssh-ed25519 AAAA... central" \
#                                DEPLOY_USER=netdeploy ./prepare-docker-host.sh
#   central server, then:   ./scripts/deploy-collector.sh site-x ... --host netdeploy@<box>
#
# NOPASSWD sudo is exactly what "unattended install" means — give it to a
# dedicated deploy user, not a person's account. The user is created if
# missing. Nothing in this section runs when DEPLOY_PUBKEY is unset.
if [ -n "${DEPLOY_PUBKEY:-}" ]; then
  DEPLOY_USER=${DEPLOY_USER:-${SUDO_USER:-root}}
  case "$DEPLOY_PUBKEY" in
    ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*|sk-ssh-*) ;;
    *) log "FATAL: DEPLOY_PUBKEY does not look like an OpenSSH public key"; exit 1 ;;
  esac
  if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    log "creating deploy user $DEPLOY_USER"
    useradd -m -s /bin/bash "$DEPLOY_USER"
  fi
  dgroup=$(id -gn "$DEPLOY_USER")
  dhome=$(getent passwd "$DEPLOY_USER" | cut -d: -f6)
  install -d -m 700 -o "$DEPLOY_USER" -g "$dgroup" "$dhome/.ssh"
  touch "$dhome/.ssh/authorized_keys"
  if ! grep -qxF "$DEPLOY_PUBKEY" "$dhome/.ssh/authorized_keys"; then
    printf '%s\n' "$DEPLOY_PUBKEY" >> "$dhome/.ssh/authorized_keys"
  fi
  chmod 600 "$dhome/.ssh/authorized_keys"
  chown "$DEPLOY_USER:$dgroup" "$dhome/.ssh/authorized_keys"
  log "deploy key authorized for $DEPLOY_USER"
  if [ "$DEPLOY_USER" != root ]; then
    # Never install a sudoers fragment that visudo has not validated — a bad
    # one can lock sudo for the whole host.
    sudo_tmp=$(mktemp)
    printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$DEPLOY_USER" > "$sudo_tmp"
    if visudo -cf "$sudo_tmp" >/dev/null 2>&1; then
      install -m 440 "$sudo_tmp" /etc/sudoers.d/netbox-collector-deploy
      log "passwordless sudo granted to $DEPLOY_USER (/etc/sudoers.d/netbox-collector-deploy)"
    else
      rm -f "$sudo_tmp"
      log "FATAL: generated sudoers entry failed visudo validation — not installed"
      exit 1
    fi
    rm -f "$sudo_tmp"
  fi
fi

# --- 6. Verify ---------------------------------------------------------------
fail=0
command -v docker >/dev/null || { log "FATAL: docker not on PATH"; exit 1; }
docker compose version >/dev/null 2>&1 || { log "FATAL: Compose v2 missing — 'docker compose' does not work"; fail=1; }

BR=$(docker network inspect bridge --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}' 2>/dev/null || true)
case "$BR" in
  100.*) log "docker0 bridge: $BR" ;;
  *)     log "FATAL: docker0 is on '$BR', not the CGNAT range — daemon config did not take"; fail=1 ;;
esac

# Prove the POOL allocator (a different code path from bip) also answers from
# CGNAT: create a throwaway network exactly the way Compose would.
T=$(docker network create netbox-pooltest 2>/dev/null || true)
if [ -n "$T" ]; then
  PT=$(docker network inspect netbox-pooltest --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}')
  docker network rm netbox-pooltest >/dev/null
  case "$PT" in
    100.*) log "pool allocation test: $PT" ;;
    *)     log "FATAL: pool allocated '$PT' — default-address-pools not in effect"; fail=1 ;;
  esac
fi

[ "$fail" -eq 0 ] && log "OK: $(docker --version | cut -d, -f1), $(docker compose version --short 2>/dev/null), all networking in $POOL_BASE" || exit 1
