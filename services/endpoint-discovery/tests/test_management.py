import hashlib
import json
import stat
import sys

import httpx
import pytest

from discovery import bootstrap, cli


def test_bootstrap_writes_unique_private_tokens_without_printing_them(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["bootstrap", "--directory", str(tmp_path), "--poller", "p", "--scope", "a"])
    bootstrap.main()
    output = capsys.readouterr().out
    reader = (tmp_path / "portal.token").read_text().strip()
    poller = (tmp_path / "poller.token").read_text().strip()
    assert reader != poller and reader not in output and poller not in output
    assert stat.S_IMODE((tmp_path / "poller.token").stat().st_mode) == 0o600
    policy = json.loads((tmp_path / "access.json").read_text())
    assert policy["principals"][1]["token_sha256"] == hashlib.sha256(poller.encode()).hexdigest()
    with pytest.raises(SystemExit, match="already exist"):
        bootstrap.main()


def test_netbox_import_is_get_only_and_does_not_follow_foreign_pagination(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET" and request.url.host == "netbox.example"
        if request.url.path.endswith("/interfaces/"):
            return httpx.Response(200, json={"results": [{"name": "Gi1", "id": 101}], "next": None})
        if request.url.params["offset"] == "0":
            return httpx.Response(200, json={"results": [{"id": 42, "name": "switch-a",
                "primary_ip4": {"address": "192.0.2.10/24"}}], "next": "https://foreign.invalid/steal-token"})
        return httpx.Response(200, json={"results": [], "next": None})

    real_client = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client", lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(respond)))
    result = cli.netbox_targets("https://netbox.example/netbox", "test-token", [
        {"site_id": 1, "scope": "a/site", "poller": "poller-a"}])
    assert result[0].netbox_device_id == 42
    assert result[0].interface_ids == {"Gi1": 101}
    assert all(r.url.path.startswith("/netbox/api/") for r in requests)


def test_netbox_import_errors_before_returning_partial_inventory(monkeypatch):
    real_client = httpx.Client
    monkeypatch.setattr(cli.httpx, "Client", lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(
        lambda request: httpx.Response(503))))
    with pytest.raises(httpx.HTTPStatusError):
        cli.netbox_targets("https://netbox.example", "test-token", [{"site_id": 1, "scope": "a", "poller": "p"}])
