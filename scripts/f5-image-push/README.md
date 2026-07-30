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
the image-upload endpoint refuses lesser roles. Per-unit credential/port
overrides go in the CSV columns.

## Usage

```bash
./f5_image_push.py --image BIGIP-17.1.1.3-0.0.5.iso
./f5_image_push.py --image BIGIP-17.1.1.3-0.0.5.iso --dry-run   # preview targets
./f5_image_push.py --image BIGIP-17.1.1.3-0.0.5.iso --workers 6 --force
```

Behavior:

- Uploads to all units in parallel (`workers` in config, `--workers` to override).
- Computes the ISO's MD5 locally, then after each upload waits for the unit to
  list the image as verified and compares checksums — a mismatch is a failure.
- Skips units that already list the image with a matching checksum (use
  `--force` to re-upload).
- Auth tokens are extended to the 10-hour maximum so slow WAN uploads survive,
  and are deleted on completion. An expired token mid-upload triggers an
  automatic re-login and chunk retry.
- Exit code is non-zero if any unit failed; the summary names the failures.

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
- **400 near the end of an upload / image never verifies** — usually
  `/shared/images` is out of space; delete old images in the GUI Image List
  and re-run (the tool re-uploads only what's missing).
- **TLS errors** — self-signed management certs are the norm; leave
  `verify_ssl = false` until you've deployed real ones.
