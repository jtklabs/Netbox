# F5 image push

Distributes a BIG-IP software image (`.iso`) to a list of units so it shows up
in each unit's GUI ready to install. Pure iControl REST over the management
interface — no SSH/SCP access to the boxes required.

## How the "shows up in the UI" part works

BIG-IP keeps installable software images in **`/shared/images`** on each unit.
Anything in that directory appears automatically in the GUI under
**System ›› Software Management : Image List** (hotfix ISOs appear under
**Hotfix List**). The GUI's own Import button just writes into that directory.

This tool uses the REST endpoint made for exactly that:
`POST /mgmt/cm/autodeploy/software-image-uploads/<file.iso>`, which streams the
ISO in 1 MB chunks straight into `/shared/images`. After the upload, the unit
MD5-verifies the file; once `verified: yes`, it is listed in the UI and an
operator (or a later script) can install it to a boot volume. Nothing else
needs to happen on the box.

## Setup

```bash
cd scripts/f5-image-push
pip install -r requirements.txt
cp config.ini.example config.ini      # fill in credentials (gitignored)
cp devices.csv.example devices.csv    # fill in your units (gitignored)
```

The account in `config.ini` needs the **Administrator** role on the units —
the image-upload endpoint refuses lesser roles. The CSV is inventory only
(`host` plus an optional display `name`); credentials and the management port
come exclusively from `config.ini`, so no secrets ever live in the device list.

## Usage

```bash
./f5_image_push.py --image BIGIP-17.1.1.3-0.0.5.iso
./f5_image_push.py --image BIGIP-17.1.1.3-0.0.5.iso --dry-run   # preview targets
./f5_image_push.py --image BIGIP-17.1.1.3-0.0.5.iso --workers 6 --force
```

Behavior:

- Uploads to all units in parallel (`workers` in config, `--workers` to override).
- Checks free space on the unit first (`df` of `/shared/images` via the
  util/bash endpoint): if there isn't room for the image plus ~100 MB headroom,
  that unit fails up-front with a clear message instead of dying mid-upload.
  Units that refuse the check (util/bash disabled) get a warning and proceed.
- Computes the ISO's MD5 locally; after each upload it waits for the unit to
  list the image as verified, then runs `md5sum` on the file on the unit
  (via util/bash) and compares — a mismatch is a failure. Note the `checksum`
  metadata field on `sys software image` is *not* the plain md5 of the ISO
  file, so the tool never compares against it.
- Skips units where the file in `/shared/images` already md5sums to the same
  value as the local image (use `--force` to re-upload). If the unit won't
  run md5sum (util/bash disabled), a listed-and-verified image of the same
  name is trusted and skipped.
- Auth tokens are extended to the 10-hour maximum so slow WAN uploads survive,
  and are deleted on completion. An expired token mid-upload triggers an
  automatic re-login and chunk retry.
- Exit code is non-zero if any unit failed; the summary names the failures.

## Disk size report

`f5_disk_report.py` is a read-only companion that uses the same `config.ini`
and `devices.csv`. For every unit it queries the logical disks over REST
(`/mgmt/tm/sys/disk/logical-disk`) and reports each disk's total size,
volume-group usage, and the current free space in `/shared/images` — useful
for spotting units that will fail the image push space check before you start.

```bash
./f5_disk_report.py                          # table on stdout
./f5_disk_report.py --output disk-report.csv # also write one row per disk
```

Unlike `push-report.csv`, the `--output` file is a point-in-time snapshot and
is overwritten on each run.

## Pruning old installers (`--prune`)

`--prune` on the push deletes every other software and hotfix ISO on each
unit before uploading, over the same API (`DELETE
/mgmt/tm/sys/software/image/<name>` and `.../hotfix/<name>`). Only the image
being pushed survives. Deleting an installer never touches installed boot
volumes or the running software — it only removes files from the GUI Image
List / Hotfix List.

```bash
./f5_image_push.py --image BIGIP-17.5.1.8-0.0.19.iso --prune
```

Pruning runs before the free-space check, so the reclaimed space counts
toward it. Every deletion is appended to `push-report.csv` as a `pruned`
row; a delete the unit refuses (e.g. an install is in progress from that
ISO) is logged as `prune-failed` and the push still proceeds — the space
check decides whether the upload can happen.

## UCS backup pull

`f5_ucs_pull.py` runs the flow in reverse: for every unit in `devices.csv` it
saves a fresh UCS archive on the box (`POST /mgmt/tm/sys/ucs`), downloads it
in chunks to this server via `/mgmt/shared/file-transfer/ucs-downloads/`, and
md5-verifies the local copy against `md5sum` on the unit.

```bash
./f5_ucs_pull.py                 # archives land in ucs-backups/<device>-<stamp>.ucs
./f5_ucs_pull.py --cleanup       # also delete the UCS from each unit after verifying
```

Outcomes append to `ucs-report.csv` (same append-only rules as the push
report). Saving a UCS on a large config can take a few minutes per unit —
that's the box building the archive, not the download.

**UCS files are secrets**: they contain the entire configuration including
SSL private keys and the local user database. The `ucs-backups/` directory
and `*.ucs` are gitignored; set `ucs_passphrase` in `config.ini` to have the
units encrypt the archives (you'll need the passphrase again to restore).

## Run report and re-runs

Every device outcome is appended to `push-report.csv` (override with
`--report`) the moment it happens — timestamp, image, device, host, status
(`uploaded` / `already-present` / `failed`), and detail (version, or the error
for failures). The file is append-only: re-runs add rows, they never rewrite
history, so it accumulates a full audit trail across runs. It's gitignored.

To retry after failures, just run the same command again against the full CSV.
The report is history, **not** the skip decision: on every run each unit is
queried directly and skipped only if the file on the box actually md5sums to
the same value as the local image. Previous successes are therefore left
alone (logged as `already-present`), previous failures are re-attempted — and
this stays correct even if the report file is deleted or someone removed the
image from a unit by hand.

## After the push

On each unit: **System ›› Software Management : Image List**, tick the image,
**Install** to the desired volume. Or from tmsh, if/when you want to automate
that step too:

```
tmsh install sys software image BIGIP-17.1.1.3-0.0.5.iso volume HD1.2
```

(Installation/reboot is deliberately left manual — pushing bits is safe to do
fleet-wide; rebooting load balancers is not.)

## Troubleshooting

- **401 on login** — wrong credentials, or the account authenticates through a
  different provider; `login_provider = tmos` is right for local and
  BIG-IP-configured remote auth users.
- **403 during upload** — the account isn't Administrator on that unit.
- **"not enough space in /shared/images"** — the pre-check caught a full
  partition; delete old images in the GUI Image List and re-run (the tool
  re-uploads only what's missing).
- **400 near the end of an upload / image never verifies** — space ran out on
  a unit where the pre-check couldn't run; same fix as above.
- **TLS errors** — self-signed management certs are the norm; leave
  `verify_ssl = false` until you've deployed real ones.
