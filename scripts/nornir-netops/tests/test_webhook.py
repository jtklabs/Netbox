import io
import json
from http.client import BadStatusLine
from unittest.mock import MagicMock
from urllib.error import HTTPError, URLError

import pytest

from netops import webhook


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("NETOPS_NAC_WEBHOOK_URL", "https://example.com/nac")
    monkeypatch.setenv("NETOPS_NAC_WEBHOOK_TOKEN", "private-token")
    return webhook.settings_from_env()


def test_disabled_without_settings():
    assert webhook.settings_from_env() is None


@pytest.mark.parametrize("field", ["URL", "TOKEN"])
def test_both_fields_are_required(configured, monkeypatch, field):
    monkeypatch.delenv(f"NETOPS_NAC_WEBHOOK_{field}")
    with pytest.raises(webhook.WebhookError, match="set both"):
        webhook.settings_from_env()


@pytest.mark.parametrize("url", ["file:///tmp/report", "https://", "https://user:secret@example.com", "https://example.com/#secret", "https://example.com:bad", "https://example.com/\npath"])
def test_invalid_destinations_are_rejected_without_echoing_them(configured, monkeypatch, url):
    monkeypatch.setenv("NETOPS_NAC_WEBHOOK_URL", url)
    with pytest.raises(webhook.WebhookError) as error:
        webhook.settings_from_env()
    assert url not in str(error.value)


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "bad"])
def test_timeout_is_bounded(configured, monkeypatch, timeout):
    monkeypatch.setenv("NETOPS_NAC_WEBHOOK_TIMEOUT", timeout)
    with pytest.raises(webhook.WebhookError, match="positive number"):
        webhook.settings_from_env()


def test_header_injection_is_rejected(configured, monkeypatch):
    monkeypatch.setenv("NETOPS_NAC_WEBHOOK_TOKEN", "token\r\nX-Injected: yes")
    with pytest.raises(webhook.WebhookError, match="without whitespace"):
        webhook.settings_from_env()


@pytest.fixture
def opener(monkeypatch):
    client = MagicMock()
    client.open.return_value.__enter__.return_value.status = 204
    monkeypatch.setattr(webhook, "build_opener", lambda handler: client)
    return client


def test_posts_json_with_bearer_auth_and_timeout(configured, opener):
    report = json.dumps({"devices": {"switch": {"description": "Café"}}})
    webhook.send(configured, report)
    request = opener.open.call_args.args[0]
    assert request.full_url == configured.url
    assert request.get_method() == "POST"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Authorization") == "Bearer private-token"
    assert request.data == report.encode("utf-8")
    assert opener.open.call_args.kwargs == {"timeout": 15.0}
    assert "private-token" not in repr(configured)


@pytest.mark.parametrize("status", [301, 307, 401, 429, 500])
def test_http_failures_do_not_expose_response_or_credentials(configured, opener, status):
    opener.open.side_effect = HTTPError(configured.url, status, "private-token", {}, io.BytesIO(b"private body"))
    with pytest.raises(webhook.WebhookError, match=f"HTTP {status}") as error:
        webhook.send(configured, "{}")
    assert "private" not in str(error.value)
    assert configured.url not in str(error.value)
    assert opener.open.call_count == 1


@pytest.mark.parametrize("error", [URLError("private-token"), TimeoutError("private-token"), BadStatusLine("private-token")])
def test_network_errors_are_sanitized(configured, opener, error):
    opener.open.side_effect = error
    with pytest.raises(webhook.WebhookError, match="delivery failed") as caught:
        webhook.send(configured, "{}")
    assert "private-token" not in str(caught.value)


def test_redirects_are_refused():
    assert webhook._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example.com") is None
