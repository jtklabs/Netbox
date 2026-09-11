"""Check TLS initialization in fresh processes, without changing pytest's SSL."""

import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_python(code):
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_import_does_not_inject_ssl():
    run_python("""
import ssl
original = ssl.SSLContext
import netops.entrypoint
assert ssl.SSLContext is original
""")


@pytest.mark.skipif(sys.version_info < (3, 10), reason="truststore requires Python 3.10+")
@pytest.mark.parametrize("script", [False, True])
def test_startup_injects_before_cli_import(script):
    run_python("""
import importlib.abc
import importlib.util
import runpy
import ssl
import sys
import truststore

class CliLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'netops.cli':
            # Check at import time, before any client can capture SSLContext.
            assert ssl.SSLContext is truststore.SSLContext
            return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        import requests
        from urllib3.util import ssl_
        assert ssl_.SSLContext is truststore.SSLContext
        context = ssl_.create_urllib3_context()
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        module.main = lambda argv=None: 23

sys.meta_path.insert(0, CliLoader())
""" + ("""
try:
    runpy.run_path('configure.py', run_name='__main__')
except SystemExit as exc:
    assert exc.code == 23
else:
    raise AssertionError('configure.py did not exit')
""" if script else """
from netops.entrypoint import main
assert main(['waf']) == 23
"""))


def test_python39_preserves_existing_ssl(monkeypatch):
    import ssl
    from types import SimpleNamespace
    from netops import entrypoint

    original = ssl.SSLContext
    monkeypatch.setattr(entrypoint, "sys", SimpleNamespace(version_info=(3, 9)))
    monkeypatch.setitem(sys.modules, "truststore", None)
    monkeypatch.setitem(sys.modules, "netops.cli", SimpleNamespace(main=lambda argv: argv))
    assert entrypoint.main(["waf"]) == ["waf"]
    assert ssl.SSLContext is original
