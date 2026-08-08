# Runbook: recover a Docker host from a noexec data root

## Symptom

Every container fails to start with:

```
failed to create task for container: ... permission denied
```

The daemon runs, images pull, `docker ps` works — only *starting* containers
fails, and nothing in the error names the cause.

## Cause

Container filesystems live under Docker's data root (`/var/lib/docker` by
default) and runc must **exec binaries from that path**. Hardened baselines
(CIS-style) mount `/var` with `noexec`, which kills every container. `nosuid`
there is harmless; `noexec` is the killer. Confirm:

```bash
findmnt -T /var/lib/docker -o TARGET,SOURCE,OPTIONS
```

`scripts/prepare-docker-host.sh` now refuses to install onto a noexec data
root, so this runbook is for boxes prepared before that check existed — or for
choosing where to point `DOCKER_DATA_ROOT` when the preflight stops you.

## Constraint this respects

**No existing storage is modified.** Relocating the data root is purely
additive: a directory on a filesystem that already permits exec, plus one
`daemon.json` key. Nothing is remounted and no mount options change.

`/` itself always permits exec (system binaries live there), so a usable spot
almost always exists already. Pick one:

```bash
for p in / /opt /srv; do findmnt -no TARGET,OPTIONS -T $p; done
df -h /opt
```

Any candidate **without** `noexec` works. Space: ~10–20 GB for a collector,
~30–50 GB for the NetBox host (its heavy data is in RDS/S3, but images add
up). If nothing has the headroom, *add* storage — a new EBS volume or LV
mounted exec-permitted at the data-root path. New disk, new mount, existing
storage untouched; also the CIS-preferred shape (the Docker benchmark wants
`/var/lib/docker` on its own partition and does not ask for noexec on it).

## Recovery steps

Everything durable survives: installed packages, the CGNAT `daemon.json`
settings, and the application files (`/opt/netbox-collector` or
`/opt/netbox`). Only the contents of the dead data root are abandoned — and
they were never usable anyway.

```bash
# 1. clear the failed stack while the old root is still active
#    (removes stuck "Created" containers; harmless if it errors)
cd /opt/netbox-collector && sudo docker compose down --remove-orphans || true

# 2. re-run the SAME prepare script with the relocation — idempotent:
#    packages and CGNAT keys are detected as already-correct, data-root is
#    merged into daemon.json, dockerd restarts
sudo DOCKER_DATA_ROOT=/opt/docker-data ./prepare-docker-host.sh

# 3. confirm the daemon actually moved before going further
sudo docker info --format 'root: {{.DockerRootDir}}'
sudo docker ps -a          # empty — the new root starts blank; correct, not a problem

# 4. reclaim the stranded old root (safe now: nothing references it)
sudo rm -rf /var/lib/docker

# 5. re-pull and start
#    collector box:
cd /opt/netbox-collector && sudo ./install.sh
sudo docker compose logs -f orb-agent
#    NetBox host instead:
#      sudo systemctl restart netbox-compose     (expect the longer first boot
#      while images rebuild/pull into the new root)
```

A side benefit of the blank root: no stale-network cleanup exists. The CGNAT
pools were in `daemon.json` all along, so every recreated network complies
from its first allocation.

## Make it stick

Put `DOCKER_DATA_ROOT` (or the finished `daemon.json`) into the provisioning
source of truth. A hand-fixed box stays fixed only until the next
provisioning run or the next freshly built host — identical to the Apache
config lesson in FIRST-BOOT.
