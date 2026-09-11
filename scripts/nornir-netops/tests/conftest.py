from functools import lru_cache
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Tests run against the source tree, installed or not.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="session")
def netops_state_root(tmp_path_factory):
    return tmp_path_factory.mktemp("netops-state")


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, netops_state_root):
    """No test sees -- or leaks -- real NET_*/AWS_* variables.

    Without this, a developer's own .env-style exports would quietly satisfy the
    credential resolution the tests are meant to be exercising.
    """
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith(("NET_", "NETOPS_", "NETBOX_", "SNOW_", "AWS_")):
            del os.environ[key]
    # The debug log and the platform cache are still exercised, just never in
    # the working directory -- and never shared between tests, which would let
    # one test's detected platform satisfy another test's detection.
    # tmp_path already has a unique test name. Reuse it instead of repeatedly
    # scanning a growing directory for the next netops-stateN number.
    scratch = netops_state_root / tmp_path.name
    scratch.mkdir()
    os.environ["NETOPS_LOG_FILE"] = str(scratch / "netops-debug.log")
    os.environ["NETOPS_REPORT_DIR"] = str(scratch / "reports")
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


@pytest.fixture(scope="session", autouse=True)
def installed_plugin_metadata():
    """Discover installed Nornir entry points once per plugin group.

    The installed packages do not change during this suite. Only the metadata
    lookup is reused: InitNornir still loads/registers the real plugins and
    creates new inventories, runners and connections on every call. Keep the
    patch local to Nornir; other libraries see normal importlib.metadata.
    """
    from nornir.core.plugins import register

    entry_points = lru_cache(maxsize=None)(register.metadata.entry_points)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(register, "metadata", SimpleNamespace(entry_points=entry_points))
        yield
