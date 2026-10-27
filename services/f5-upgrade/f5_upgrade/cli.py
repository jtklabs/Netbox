import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .models import JobRequest


def parser():
    p = argparse.ArgumentParser(description="Submit and inspect SSH BIG-IP upgrade jobs")
    p.add_argument("--url")
    p.add_argument("--env-file", default=".env")
    commands = p.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8080, type=int)
    commands.add_parser("worker")
    commands.add_parser("devices")
    commands.add_parser("jobs")
    for command in ("job", "events", "cancel", "rollback", "resolve"):
        sub = commands.add_parser(command)
        sub.add_argument("id")
        if command == "rollback":
            sub.add_argument("--idempotency-key")
        if command == "resolve":
            sub.add_argument("--worker-stopped", required=True, action="store_true")
            sub.add_argument("--device-inspected", required=True, action="store_true")
            sub.add_argument("--note", required=True)
    for command in ("plan", "submit"):
        sub = commands.add_parser(command)
        sub.add_argument("--device", required=True)
        sub.add_argument("--images", type=Path, help="JSON array of image name, sha256, version, build, kind")
        for flag in (
            "transfer",
            "images-on-device",
            "create-ucs",
            "ucs-unencrypted",
            "install",
            "reboot",
            "drain",
            "reboot-on-drain-timeout",
        ):
            sub.add_argument("--" + flag, action=argparse.BooleanOptionalAction, default=False)
        for flag in ("backup-directory", "target-volume", "expected-version", "expected-build"):
            sub.add_argument("--" + flag)
        sub.add_argument("--allowed-source-version", action="append", default=[])
        drain = sub.add_mutually_exclusive_group()
        drain.add_argument("--drain-connections", type=int)
        drain.add_argument(
            "--drain-percent", type=float, help="percentage of initial connections to remove (0-100)"
        )
        for flag, default in (
            ("drain-timeout", 300),
            ("install-timeout", 3600),
            ("reboot-timeout", 900),
            ("validation-timeout", 180),
        ):
            sub.add_argument("--" + flag, type=int, default=default)
        sub.add_argument("--idempotency-key")
    return p


def main():
    args = parser().parse_args()
    os.environ["F5_ENV_FILE"] = args.env_file
    load_dotenv(args.env_file)
    args.url = args.url or os.getenv("F5_API_URL", "http://127.0.0.1:8080")
    if args.command == "serve":
        import uvicorn

        uvicorn.run("f5_upgrade.api:create_app", factory=True, host=args.host, port=args.port)
        return
    if args.command == "worker":
        from .worker import main as worker

        worker()
        return
    token = os.getenv("F5_API_TOKEN")
    if not token:
        raise SystemExit("F5_API_TOKEN is required")
    method, body, path = "GET", None, "/api/v1/" + args.command
    headers = {"Authorization": "Bearer " + token}
    try:
        if args.command in ("plan", "submit"):
            values = {k: v for k, v in vars(args).items() if k in JobRequest.model_fields and v is not None}
            values["images"] = json.loads(args.images.read_text()) if args.images else []
            values["allowed_source_versions"] = args.allowed_source_version
            body = JobRequest.model_validate(values).model_dump()
            method, path = "POST", "/api/v1/plan" if args.command == "plan" else "/api/v1/jobs"
        elif args.command == "job":
            path = "/api/v1/jobs/" + args.id
        elif args.command in ("events", "rollback", "cancel", "resolve"):
            path = "/api/v1/jobs/" + args.id + "/" + args.command
            if args.command != "events":
                method, body = "POST", {}
            if args.command == "resolve":
                body = {
                    "worker_stopped": args.worker_stopped,
                    "device_inspected": args.device_inspected,
                    "note": args.note,
                }
        if method == "POST" and args.command in ("submit", "rollback"):
            key = args.idempotency_key or str(uuid.uuid4())
            headers["Idempotency-Key"] = key
            print("Idempotency-Key: " + key + " (reuse this key if the response is lost)", file=sys.stderr)
        response = httpx.request(method, args.url.rstrip("/") + path, headers=headers, json=body, timeout=60)
        print(json.dumps(response.json(), indent=2))
        if response.is_error:
            raise SystemExit(1)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        raise SystemExit("Request failed: " + str(exc)) from None


if __name__ == "__main__":
    main()
