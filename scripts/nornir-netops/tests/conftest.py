import os
import sys
from pathlib import Path

import pytest

# Tests run against the source tree, installed or not.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path_factory):
    """No test sees -- or leaks -- real NET_*/AWS_* variables.

    Without this, a developer's own .env-style exports would quietly satisfy the
    credential resolution the tests are meant to be exercising.
    """
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith(("NET_", "NETOPS_", "AWS_")):
            del os.environ[key]
    # The debug log and the platform cache are still exercised, just never in
    # the working directory -- and never shared between tests, which would let
    # one test's detected platform satisfy another test's detection.
    scratch = tmp_path_factory.mktemp("netops-state")
    os.environ["NETOPS_LOG_FILE"] = str(scratch / "netops-debug.log")
    os.environ["NETOPS_PLATFORM_CACHE"] = str(scratch / "platform-cache.json")
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def isolated_cwd(tmp_path, monkeypatch):
    """Run every test in a scratch directory.

    Several defaults resolve against the working directory -- ./.env,
    ./standards.yaml, the debug log. Without this a test would quietly read the
    real files sitting beside the tool, and pass for the wrong reason.
    """
    monkeypatch.chdir(tmp_path)
