#!/usr/bin/env python3
"""Entry point.

    ./configure.py ntp --servers 10.0.0.1,10.0.0.2
    ./configure.py ntp --servers 10.0.0.1,10.0.0.2 --replace --apply
    ./configure.py selftest
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from netops.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
