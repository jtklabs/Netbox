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
| `/data/netbox-secrets/` | The files you write by hand: env files, SAML keys, Diode credentials. Lives on the **persistent data disk**. | **Yes** — the disk detaches from the old instance and attaches to the new one. |

`/data` is the mount point for the data disk, so `mkdir` there only works after
the disk is mounted (step 1). `/opt/netbox` is wherever the AMI checked the
repo out — if yours is elsewhere, set `REPO_DIR` when running bootstrap.

**The containers do not read from `/data` directly.** On every boot
`deploy/bootstrap.sh` connects the two: it symlinks the env files from the data
disk into `/opt/netbox/env/`, and copies `client-credentials.json` into
`/opt/netbox/discovery/oauth2/client/`. Docker Compose then reads everything
from the repo paths as normal. (That one file is copied rather than symlinked
because its directory is bind-mounted into a container, and a symlink inside a
bind mount resolves against the *container's* filesystem, where `/data` does
not exist.)

So the rule of thumb: **you only ever hand-edit files under
`/data/netbox-secrets/`.** Never edit the copies under `/opt/netbox` — a
redeploy throws them away.

## 1. Prepare the data disk (label is what bootstrap mounts by)

```bash
sudo mkfs.ext4 -L NETBOXDATA /dev/nvme1n1   # adjust device; SKIP if disk already has data
sudo mkdir -p /data && sudo mount LABEL=NETBOXDATA /data
sudo mkdir -p /data/netbox-secrets/saml
```

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
    env/netbox.env.example | sudo tee /data/netbox-secrets/netbox.env >/dev/null
sed "s/{{REDIS_PASSWORD}}/$RPW/"  env/redis.env.example       | sudo tee /data/netbox-secrets/redis.env >/dev/null
sed "s/{{REDIS_CACHE_PASSWORD}}/$RCPW/" env/redis-cache.env.example | sudo tee /data/netbox-secrets/redis-cache.env >/dev/null
```

(The generated `DB_PASSWORD` in netbox.env is a placeholder — prod.env
overrides DB_* with the real RDS values in the next step. The SECRET_KEY and
pepper in this file must NEVER change afterward.)

## 3. Fill in prod.env and .env

```bash
sudo cp env/prod.env.example /data/netbox-secrets/prod.env
sudo vi /data/netbox-secrets/prod.env      # RDS endpoint/creds, S3 bucket, region
sudo tee /data/netbox-secrets/.env >/dev/null <<'EOF'
COMPOSE_FILE=docker-compose.yml:compose/prod.yml
VERSION=v4.6.5-5.0.2
# With ECR: set PROD_IMAGE to the pushed URI and PROD_PULL_POLICY=missing
# PROD_IMAGE=123456789.dkr.ecr.us-east-1.amazonaws.com/netbox-custom:v4.6.5-5.0.2
EOF
```

Running discovery on this host too? Append the diode block + DISCOVERY_* creds
to that `.env` (copy the block shape from your dev `.env`), append
`:compose/discovery.yml` to COMPOSE_FILE, and place `client-credentials.json`
at `/data/netbox-secrets/` with fresh secrets matching the .env values.

(Bootstrap links the redis env files into place for you on every boot — no
manual `ln` needed.)

## 4. SAML service provider material

```bash
cd /data/netbox-secrets/saml
sudo mellon_create_metadata.sh https://nova.jtklabs.dev/mellon "https://nova.jtklabs.dev/mellon"
sudo mv *.key mellon.key && sudo mv *.cert mellon.cert
# Fetch/copy your IdP metadata:
sudo cp /path/to/idp-metadata.xml idp-metadata.xml
```

Register the generated SP metadata with your IdP, releasing a username
attribute (apache/netbox.conf assumes `uid` — edit `MELLON_uid` there if yours
differs).

## 5. Apache

```bash
sudo a2enmod proxy proxy_http headers auth_mellon
sudo cp /opt/netbox/apache/netbox.conf /etc/apache2/conf-available/netbox.conf
# Include it inside the existing nova.jtklabs.dev :443 vhost, then:
sudo apachectl configtest && sudo systemctl reload apache2
```

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
`https://nova.jtklabs.dev/netbox/admin/` — or set
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
