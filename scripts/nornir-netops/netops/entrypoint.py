"""Application startup: initialize system TLS trust before importing clients."""

import sys
from typing import List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    # Version inspection must work without credentials, network or optional clients.
    if (sys.argv[1:] if argv is None else argv) == ['--version']:
        from . import __version__
        print(f'nornir-netops {__version__}')
        return 0

    # Global SSL injection belongs at application startup, not package import.
    # Python 3.9 users retain the REQUESTS_CA_BUNDLE compatibility path.
    if sys.version_info >= (3, 10):
        import truststore

        truststore.inject_into_ssl()

    from .cli import main as cli_main

    return cli_main(argv)
