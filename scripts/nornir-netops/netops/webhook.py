"""Send NAC reports to the destination configured in the environment."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from http.client import HTTPException
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .debuglog import protect


class WebhookError(ValueError):
    """Invalid delivery settings or an unsuccessful delivery."""


@dataclass(frozen=True)
class Settings:
    url: str = field(repr=False)
    token: str = field(repr=False)
    timeout: float = 15.0


def settings_from_env(prefix: str = "NETOPS_NAC_WEBHOOK") -> Optional[Settings]:
    url = os.environ.get(f"{prefix}_URL", "").strip()
    token = os.environ.get(f"{prefix}_TOKEN", "").strip()
    if not url and not token:
        return None
    protect([url, token])
    if not url or not token:
        raise WebhookError(f"set both {prefix}_URL and {prefix}_TOKEN")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        valid = (
            parsed.scheme in {"http", "https"} and parsed.hostname
            and not parsed.username and not parsed.password and not parsed.fragment
            and not any(character.isspace() or ord(character) < 32 for character in url)
            and port != 0
        )
    except ValueError:
        valid = False
    if not valid:
        raise WebhookError(f"{prefix}_URL must be an HTTP(S) URL without user info or a fragment")
    if any(character.isspace() or ord(character) < 33 or ord(character) > 126 for character in token):
        raise WebhookError(f"{prefix}_TOKEN must contain printable ASCII without whitespace")
    try:
        timeout = float(os.environ.get(f"{prefix}_TIMEOUT", "15"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
    except ValueError:
        raise WebhookError(f"{prefix}_TIMEOUT must be a positive number of seconds") from None
    return Settings(url, token, timeout)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Keep the bearer token and device report at the configured destination.
        return None


def send(settings: Settings, report: str) -> None:
    request = Request(
        settings.url,
        data=report.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.token}",
        },
        method="POST",
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=settings.timeout) as response:
            if not 200 <= response.status < 300:
                raise WebhookError(f"NAC webhook returned HTTP {response.status}")
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise WebhookError(f"NAC webhook returned HTTP {code}") from None
    except (URLError, OSError, ValueError, HTTPException) as exc:
        if isinstance(exc, WebhookError):
            raise
        # URLs, tokens and response bodies do not belong in errors or logs.
        raise WebhookError("NAC webhook delivery failed (connection, TLS, or timeout error)") from None
