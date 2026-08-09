#!/usr/bin/env python3
"""Run emulated SNMP devices in the foreground so you can scan them by hand.

The test suite starts these itself; this is for the times you want a fleet
sitting there while you run the scanner against it, watch what it writes into
NetBox, and iterate.

    ./run_emulator.py                       # every fixture, one port each
    ./run_emulator.py --only cisco-c9300-stack arista-7050sx
    ./run_emulator.py --base-port 12000 --listen 127.0.0.1

It prints the address of each device and the credentials they all share, then
waits until interrupted.

Point the scanner at one with:

    ./snmp_inventory.py --config snmp-inventory.conf --host 127.0.0.1:11610 \\
        --site-id <id> --dry-run

Note the host:port form — the scanner passes the target straight to net-snmp,
which accepts a port suffix, so no privileged port is needed anywhere.
"""

from __future__ import annotations

import argparse
import glob
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emulator import (  # noqa: E402
    DEFAULT_AUTH_PASS,
    DEFAULT_AUTH_PROTOCOL,
    DEFAULT_PRIV_PASS,
    DEFAULT_PRIV_PROTOCOL,
    DEFAULT_USER,
    EmulatedDevice,
    EmulatorError,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-port", type=int, default=11610,
                        help="first UDP port to use (default: %(default)s)")
    parser.add_argument("--listen", default="127.0.0.1",
                        help="address to bind (default: %(default)s)")
    parser.add_argument("--only", nargs="*", default=None,
                        help="fixture names to run (default: all)")
    parser.add_argument("--fixtures", default=FIXTURES, help="fixture directory")
    args = parser.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.fixtures, "*.walk")))
    if args.only:
        wanted = set(args.only)
        paths = [p for p in paths
                 if os.path.splitext(os.path.basename(p))[0] in wanted]
        missing = wanted - {os.path.splitext(os.path.basename(p))[0] for p in paths}
        if missing:
            parser.error(f"no such fixture: {', '.join(sorted(missing))}")
    if not paths:
        parser.error(f"no .walk fixtures found in {args.fixtures}")

    devices: list[EmulatedDevice] = []
    try:
        for offset, path in enumerate(paths):
            device = EmulatedDevice(path, port=args.base_port + offset, listen=args.listen)
            try:
                device.start()
            except EmulatorError as exc:
                print(f"failed to start {os.path.basename(path)}: {exc}", file=sys.stderr)
                continue
            devices.append(device)
            print(f"  {os.path.splitext(os.path.basename(path))[0]:22s} {device.address}")

        if not devices:
            print("nothing started", file=sys.stderr)
            return 1

        print()
        print("SNMPv3 credentials (all devices):")
        print(f"  user            {DEFAULT_USER}")
        print(f"  auth            {DEFAULT_AUTH_PROTOCOL} / {DEFAULT_AUTH_PASS}")
        print(f"  priv            {DEFAULT_PRIV_PROTOCOL} / {DEFAULT_PRIV_PASS}")
        print()
        print("Try one directly:")
        first = devices[0]
        print(f"  snmpwalk -v3 -l authPriv -u {DEFAULT_USER} "
              f"-a {DEFAULT_AUTH_PROTOCOL} -A {DEFAULT_AUTH_PASS} "
              f"-x {DEFAULT_PRIV_PROTOCOL} -X {DEFAULT_PRIV_PASS} \\")
        print(f"      -On {first.address} 1.3.6.1.2.1.1")
        print()
        print(f"{len(devices)} devices up. Ctrl-C to stop.")

        signal.pause()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        for device in devices:
            device.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
