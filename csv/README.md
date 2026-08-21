# csv/ — import and export drop zone

Mounted into the NetBox containers at `/opt/netbox/netbox/csv` (read-write),
so a file placed here is reachable by NetBox custom scripts and by management
commands run inside the container, and a script can write an export back.

What it is for: CSVs on their way in (lifecycle dates, replacement prices,
quote lines) and on their way out. Everything in this directory except this
file and `.gitkeep` is git-ignored — price lists and quotes are not for the
repo.

Permissions: the container runs as uid 999 (`unit`). Reading files you put
here works as-is. If a script needs to WRITE here on a Linux host, the
directory must be writable by that uid: `sudo chown 999 csv` or `chmod 777 csv`
(macOS Docker Desktop needs neither). The directory must exist before
`docker compose up` — which is why `.gitkeep` is committed — otherwise Docker
creates it root-owned and nothing in it is writable.
