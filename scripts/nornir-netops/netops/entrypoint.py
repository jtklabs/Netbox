"""Application startup: initialize system TLS trust before importing clients."""

import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    # Global SSL injection belongs at application startup, not package import.
    # Python 3.9 users retain the REQUESTS_CA_BUNDLE compatibility path.
    if sys.version_info >= (3, 10):
        import truststore

        truststore.inject_into_ssl()

    from .cli import main as cli_main

    return cli_main(argv)
