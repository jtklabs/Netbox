import os
import sys
from pathlib import Path

import pytest

# Tests run against the source tree, installed or not.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_environment():
    """No test sees -- or leaks -- real NET_*/AWS_* variables.

    Without this, a developer's own .env-style exports would quietly satisfy the
    credential resolution the tests are meant to be exercising.
    """
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith(("NET_", "NETOPS_", "AWS_")):
            del os.environ[key]
    yield
    os.environ.clear()
    os.environ.update(saved)
