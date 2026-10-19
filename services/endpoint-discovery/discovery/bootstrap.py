"""Create local credential files once; never print tokens."""
import argparse
import json
import os
import secrets
from pathlib import Path

from .config import digest_token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="config")
    parser.add_argument("--poller", required=True)
    parser.add_argument("--scope", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    directory = Path(args.directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths = [directory / name for name in ("portal.token", "poller.token", "access.json")]
    if any(path.exists() for path in paths):
        raise SystemExit("credential files already exist; rotate them explicitly")
    principals = []
    for name, role, path in (("network-portal", "reader", paths[0]), (args.poller, "poller", paths[1])):
        token = secrets.token_urlsafe(48)
        with path.open("x") as handle:
            handle.write(token + "\n")
        principals.append({"name": name, "role": role, "scopes": [args.scope], "token_sha256": digest_token(token)})
    with paths[2].open("x") as handle:
        json.dump({"principals": principals}, handle, indent=2)
    print("Created portal.token, poller.token, and access.json in the selected private directory.")


if __name__ == "__main__":
    main()
