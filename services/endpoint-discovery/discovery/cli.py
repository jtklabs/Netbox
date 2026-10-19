"""Deployment and inventory commands; no network device write operations."""
import argparse
import hashlib
import json
import os
import ssl
from pathlib import Path

import httpx

from .config import Settings
from .poller_url import secure_url
from .schemas import Target
from .store import database, migrate, provision, prune


def netbox_targets(url, token, mappings, ca_file=None):
    """Explicit site-to-scope mapping; GET-only, with origin-safe pagination."""
    url = secure_url(url)
    base = url + ("/" if url.endswith("/api") else "/api/")
    trust = ssl.create_default_context(cafile=ca_file)
    headers = {"Authorization": ("Bearer " if token.startswith("nbt_") else "Token ") + token}
    result = []
    with httpx.Client(base_url=base, headers=headers, verify=trust, follow_redirects=False, timeout=60) as client:
        def pages(path, params):
            offset = 0
            while True:
                response = client.get(path, params={**params, "limit": 250, "offset": offset})
                response.raise_for_status()
                page = response.json()
                yield from page["results"]
                if not page.get("next"):
                    break
                if not page["results"]:
                    raise ValueError("invalid empty NetBox pagination")
                # Ignore supplied pagination URLs: never forward credentials
                # to an address provided by a remote response.
                offset += len(page["results"])

        seen_sites = set()
        for mapping in mappings:
            site = mapping["site_id"]
            if site in seen_sites:
                raise ValueError("each NetBox site must have one scope/poller mapping")
            seen_sites.add(site)
            for device in pages("dcim/devices/", {"site_id": site, "status": "active"}):
                primary = device.get("primary_ip4") or device.get("primary_ip6")
                if not primary:
                    continue
                interfaces = list(pages("dcim/interfaces/", {"device_id": device["id"]}))
                target = {k: v for k, v in mapping.items() if k != "site_id"}
                scope_hash = hashlib.sha256(mapping["scope"].encode()).hexdigest()[:12]
                target.update(key=f"netbox-{device['id']}-{scope_hash}", address=primary["address"].split("/")[0],
                              name=device["name"], netbox_device_id=device["id"],
                              interface_ids={i["name"]: i["id"] for i in interfaces})
                result.append(Target.model_validate(target))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate")
    commands.add_parser("prune")
    inventory = commands.add_parser("import-targets")
    inventory.add_argument("file")
    inventory.add_argument("--replace", action="store_true")
    sync = commands.add_parser("sync-netbox")
    sync.add_argument("mapping_file")
    sync.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    engine, factory = database(settings.database_url)
    try:
        if args.command == "migrate":
            migrate(engine)
        elif args.command == "prune":
            print(json.dumps(prune(factory, settings)))
        else:
            if args.command == "sync-netbox":
                token = Path(os.environ["NETBOX_TOKEN_FILE"]).read_text().strip()
                targets = netbox_targets(os.environ["NETBOX_URL"], token,
                                         json.loads(Path(args.mapping_file).read_text()),
                                         os.getenv("NETBOX_CA_FILE"))
            else:
                targets = [Target.model_validate(t) for t in json.loads(Path(args.file).read_text())]
            provision(factory, targets, replace=args.replace)
            print(json.dumps({"targets_imported": len(targets)}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
