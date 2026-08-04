# First-boot operator guide (one time, run by you on the prod instance)

Everything here happens exactly once. After this, every 30-day redeploy is
automatic (RUNBOOK-redeploy.md). No secrets ever leave this box or enter git.

Prereqs: Ubuntu 24 instance with docker + compose, this repo at `/opt/netbox`,
the data disk attached, an instance profile granting the S3 media bucket, and
the existing Apache with mod_auth_mellon.

## How the layout works (read this first)

**None of these paths exist yet — you create them in the steps below.** There
are only two locations, and the split is the whole point of the design:

| Path | What it is | Survives a redeploy? |
|---|---|---|
| `/opt/netbox` | This git repo: compose files, plugins, scripts. Ships with the AMI. | **No** — replaced with each new AMI, and that is fine, it is just code. |
| `/mnt/data_disk/netbox-secrets/` | The files you write by hand: env files and Diode credentials. Lives on the **persistent data disk**. | **Yes** — the disk detaches from the old instance and attaches to the new one. |

The data-disk mount point is `/mnt/data_disk` throughout. If it ever moves, set
it once rather than editing paths everywhere:

```bash
echo 'DATA_MOUNT=/mnt/data_disk' | sudo tee /etc/netbox-deploy.conf
```

`deploy/bootstrap.sh` and the systemd unit both read that file. Bake it into the
AMI or write it from user-data — `/etc` is replaced along with the AMI, so an
edit made by hand on a running instance will not survive a redeploy.

`/mnt/data_disk` is the mount point for the data disk, so `mkdir` there only works after
the disk is mounted (step 1). `/opt/netbox` is wherever the AMI checked the
repo out — if yours is elsewhere, set `REPO_DIR` when running bootstrap.

**The containers do not read from the data disk directly.** On every boot
`deploy/bootstrap.sh` connects the two: it symlinks the env files from the data
disk into `/opt/netbox/env/`, and copies `client-credentials.json` into
`/opt/netbox/discovery/oauth2/client/`. Docker Compose then reads everything
from the repo paths as normal. (That one file is copied rather than symlinked
because its directory is bind-mounted into a container, and a symlink inside a
bind mount resolves against the *container's* filesystem, where that path does
not exist.)

So the rule of thumb: **you only ever hand-edit files under
`/mnt/data_disk/netbox-secrets/`.** Never edit the copies under `/opt/netbox` — a
redeploy throws them away.

## 1. Prepare the data disk (label is what bootstrap mounts by)

If the disk is already mounted at `/mnt/data_disk` (fstab or cloud-init), skip
straight to creating the directory — bootstrap detects an existing mount and
leaves it alone.

```bash
# Only if the disk is brand new and not already mounted:
sudo mkfs.ext4 -L NETBOXDATA /dev/nvme1n1   # adjust device; SKIP if it already has data
sudo mkdir -p /mnt/data_disk && sudo mount LABEL=NETBOXDATA /mnt/data_disk

# Always:
sudo mkdir -p /mnt/data_disk/netbox-secrets
mountpoint /mnt/data_disk    # must say "is a mountpoint" before continuing
```

That last check matters: if the disk is not mounted, everything below would be
written to the root filesystem and silently disappear at the next redeploy.
Bootstrap refuses to start in that situation for the same reason.

## 2. Generate the persistent app secrets

```bash
cd /opt/netbox
gen() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }
DBPW=$(gen 32); RPW=$(gen 24); RCPW=$(gen 24)
sed -e "s/{{SECRET_KEY}}/$(gen 60)/" \
    -e "s/{{API_TOKEN_PEPPER}}/$(gen 50)/" \
    -e "s/{{DB_PASSWORD}}/$DBPW/" \
    -e "s/{{REDIS_PASSWORD}}/$RPW/" \
    -e "s/{{REDIS_CACHE_PASSWORD}}/$RCPW/" \
    env/netbox.env.example | sudo tee /mnt/data_disk/netbox-secrets/netbox.env >/dev/null
sed "s/{{REDIS_PASSWORD}}/$RPW/"  env/redis.env.example       | sudo tee /mnt/data_disk/netbox-secrets/redis.env >/dev/null
sed "s/{{REDIS_CACHE_PASSWORD}}/$RCPW/" env/redis-cache.env.example | sudo tee /mnt/data_disk/netbox-secrets/redis-cache.env >/dev/null
```

(The generated `DB_PASSWORD` in netbox.env is a placeholder — prod.env
overrides DB_* with the real RDS values in the next step. The SECRET_KEY and
pepper in this file must NEVER change afterward.)

## 3. Fill in prod.env and .env

**Two files, and the split is not cosmetic.** `.env` holds settings Docker
Compose itself reads while parsing (which image, which overlays, which address
to publish on). `prod.env` holds the environment handed *to the container*
(database, S3, SSO). Compose does **not** read `env_file:` entries for `${...}`
substitution, so a compose-level setting placed in `prod.env` is silently
ignored — `BIND_ADDRESS` is the one that bites, because NetBox then stays on
loopback and the Apache server cannot reach it.

```bash
sudo cp env/prod.env.example /mnt/data_disk/netbox-secrets/prod.env
sudo vi /mnt/data_disk/netbox-secrets/prod.env      # RDS endpoint/creds, S3 bucket, region

sudo tee /mnt/data_disk/netbox-secrets/.env >/dev/null <<'EOF'
COMPOSE_FILE=docker-compose.yml:compose/prod.yml
VERSION=v4.6.5-5.0.2
# The address NetBox publishes on. Apache is on another host, so this must NOT
# be loopback — use this instance's PRIVATE address, and restrict port 8080 to
# the Apache server with a security group.
BIND_ADDRESS=10.0.0.0
# PROD_IMAGE stays unset: the image is built during the AMI bake
# (scripts/prod-build.sh), or by bootstrap at first boot as a fallback.
EOF
```

Running discovery on this host too? Append the diode block + DISCOVERY_* creds
to that `.env` (copy the block shape from your dev `.env`), append
`:compose/discovery.yml` to COMPOSE_FILE, and place `client-credentials.json`
at `/mnt/data_disk/netbox-secrets/` with fresh secrets matching the .env values.

(Bootstrap links the redis env files into place on every boot, and generates
them from `netbox.env` if they are missing, so their passwords always match —
no manual `ln` and no chance of a mismatch.)

### If you see "env file env/redis.env not found"

Compose is being run before `deploy/bootstrap.sh` has linked the data-disk
files into the repo. Run bootstrap — that is what wires the two together:

```bash
sudo /opt/netbox/deploy/bootstrap.sh
```

or start it the normal way, `sudo systemctl start netbox-compose`. Running
`docker compose` directly in `/opt/netbox` only works *after* bootstrap has run
at least once since the repo was replaced.

## 4. SAML — nothing to do here

Mellon already runs on the existing Apache server with its SP keys and IdP
metadata in place, and that server already injects the identity headers
(`X-Remote-User`, `X-User-Email`, `X-UserFirstName`, `X-User-LastName`,
`X-User-Groups`). This instance runs no Apache and no Mellon.

## 5. Apache — on the OTHER server, not this one

Apache proxies across the network to this instance, so two things must line up.

**On this instance**, set `BIND_ADDRESS` in `/mnt/data_disk/netbox-secrets/.env`
(the `.env`, not `prod.env` — see step 3) to its **private** address, then allow
port 8080 **only** from the Apache server in the security group. That hop is
plain HTTP and NetBox trusts the identity headers Apache sets, so anything able
to reach 8080 directly could impersonate any user; the security group is what
prevents that.

**On the Apache server**, copy `apache/netbox.conf` from this repo, set
`NETBOX_BACKEND` at the top to this instance's private address, and include it
in the existing `netbox.example.com` :443 vhost:

```bash
sudo a2enmod proxy proxy_http headers      # mellon is already enabled
sudo cp netbox.conf /etc/apache2/conf-available/netbox.conf
# add "Include conf-available/netbox.conf" inside the vhost, then:
sudo apachectl configtest && sudo systemctl reload apache2
```

Check it end to end from the Apache server before going further:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://<netbox-private-ip>:8080/netbox/login/
```

A 200 means the path and security group are right. Connection refused means
`BIND_ADDRESS` is still loopback or the security group is closed.

## 6. Start and enable

```bash
sudo cp /opt/netbox/deploy/netbox-compose.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now netbox-compose
sudo tail -f /var/log/netbox-bootstrap.log   # or journalctl -u netbox-compose
```

First boot runs all migrations (several minutes).

## 7. Break-glass local admin + first SSO admin

```bash
cd /opt/netbox
docker compose exec netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py createsuperuser
```

Then log in once via SSO (your account gets auto-created with no rights) and
grant it from the break-glass admin at
`https://netbox.example.com/netbox/admin/` — or set
`REMOTE_AUTH_SUPERUSER_GROUPS` in prod.env once the IdP sends groups.

## 8. Verify (same list as RUNBOOK-redeploy.md step 6)

Login page over https, SSO round-trip, device pages, quote document download
(S3), `docker compose ps` all healthy.

## Image distribution (decision 2026-07-29: no ECR)

Leave `PROD_IMAGE` unset. Add one step to the monthly AMI bake, after the repo
checkout:

```bash
cd /opt/netbox && ./scripts/prod-build.sh
```

That builds the image under the exact tag `compose/prod.yml` expects and then
verifies every plugin loads inside it — a plugin that would fail on boot is
caught during the bake instead of during a redeploy. Instances then start with
the image already present (fast boot, no Docker Hub dependency at boot time).

If the bake skips this, `deploy/bootstrap.sh` builds at first boot instead
(~5 extra minutes).
