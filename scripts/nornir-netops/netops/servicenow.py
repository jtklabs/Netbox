"""ServiceNow change records.

The tool opens a change from a dry run and closes one after an apply. It never
approves anything: a change that has not passed Authorize is refused, with the
state it is actually in, rather than being nudged along.

Everything goes through the Change Management API (`/api/sn_chg_rest/change`)
rather than writing to the `change_request` table, because that API validates
the state model. Writing the table directly is how changes end up in states the
model does not allow.

**The state values are instance-specific.** The defaults below are the
out-of-the-box Normal change model; instances customise it constantly. Check
`sys_choice` for `change_request.state` on your instance and set
`change.states` in the standards file if they differ.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

#: Out-of-the-box Normal change states, by the name this tool refers to them by.
DEFAULT_STATES = {
    "new": "-5",
    "assess": "-4",
    "authorize": "-3",
    "scheduled": "-2",
    "implement": "-1",
    "review": "0",
    "closed": "3",
}

#: States from which it is legitimate to start implementing. Anything earlier
#: has not been approved yet.
IMPLEMENTABLE = ("scheduled", "implement")

#: ServiceNow's own close codes.
CLOSE_SUCCESS = "successful"
CLOSE_ISSUES = "successful_issues"
CLOSE_FAILED = "unsuccessful"

CHANGE_API = "/api/sn_chg_rest/change"
ATTACHMENT_API = "/api/now/attachment/file"


class ServiceNowError(Exception):
    """The instance rejected something, or could not be reached."""


class ChangeNotApprovedError(ServiceNowError):
    """The change exists but is not in a state we may implement from."""


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #


@dataclass
class Settings:
    """Where the instance is and what a change created here should look like.

    The static fields come from the `change:` section of the standards file, so
    the assignment group and risk rating are stated once with everything else.
    """

    instance: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    timeout: float = 30.0
    verify_tls: bool = True
    states: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_STATES))
    fields: Dict[str, Any] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        if not self.instance:
            raise ServiceNowError(
                "no ServiceNow instance configured: set $SNOW_INSTANCE or "
                "--snow-instance (the name, or the full https:// URL)"
            )
        instance = self.instance.strip().rstrip("/")
        if instance.startswith("http://") or instance.startswith("https://"):
            return instance
        return f"https://{instance}.service-now.com"

    def state(self, name: str) -> str:
        try:
            return str(self.states[name])
        except KeyError:
            raise ServiceNowError(
                f"no state value configured for {name!r} "
                f"(known: {', '.join(sorted(self.states))})"
            ) from None

    def state_name(self, value: Any) -> str:
        """Turn a state value back into a name, for a readable message."""
        for name, configured in self.states.items():
            if str(configured) == str(value):
                return name
        return f"state {value}"


def settings_from(standards, args) -> Settings:
    """Standards file for the static fields, flags and environment for the rest."""
    import os

    section = standards.section("change") if standards is not None else {}
    states = dict(DEFAULT_STATES)
    for name, value in (section.get("states") or {}).items():
        states[str(name)] = str(value)

    known = {"states", "instance", "assignment_group", "verify_tls"}
    fields = {k: v for k, v in section.items() if k not in known}
    if section.get("assignment_group"):
        fields["assignment_group"] = section["assignment_group"]

    return Settings(
        instance=getattr(args, "snow_instance", None)
        or section.get("instance")
        or os.environ.get("SNOW_INSTANCE"),
        username=os.environ.get("SNOW_USER"),
        password=os.environ.get("SNOW_PASS"),
        client_id=os.environ.get("SNOW_CLIENT_ID"),
        client_secret=os.environ.get("SNOW_CLIENT_SECRET"),
        timeout=float(os.environ.get("SNOW_TIMEOUT", "30")),
        verify_tls=str(section.get("verify_tls", "true")).lower() != "false",
        states=states,
        fields=fields,
    )


def load_secret(name: str, region: Optional[str], settings: Settings) -> None:
    """Fill credentials from an AWS secret, leaving anything already set."""
    from .credentials import fetch_json_secret

    document = fetch_json_secret(name, region)
    for attribute, keys in (
        ("username", ("username", "user")),
        ("password", ("password", "pass")),
        ("client_id", ("client_id",)),
        ("client_secret", ("client_secret",)),
        ("instance", ("instance",)),
    ):
        if getattr(settings, attribute):
            continue
        for key in keys:
            if document.get(key):
                setattr(settings, attribute, str(document[key]))
                break


# --------------------------------------------------------------------------- #
# client
# --------------------------------------------------------------------------- #


class Client:
    """The handful of calls this tool makes, and nothing else."""

    def __init__(self, settings: Settings, session: Any = None) -> None:
        self.settings = settings
        self._session = session
        self._token: Optional[str] = None

    # -- transport ----------------------------------------------------------

    def _requests(self):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise ServiceNowError(
                "ServiceNow support needs the 'requests' package "
                "(pip install -r requirements.txt)"
            ) from exc
        return requests

    def session(self):
        if self._session is None:
            self._session = self._requests().Session()
        return self._session

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.settings.client_id:
            headers["Authorization"] = f"Bearer {self._oauth_token()}"
        return headers

    def _auth(self) -> Optional[Tuple[str, str]]:
        if self.settings.client_id:
            return None  # bearer token instead
        if self.settings.username and self.settings.password is not None:
            return (self.settings.username, self.settings.password)
        raise ServiceNowError(
            "no ServiceNow credentials: set $SNOW_USER and $SNOW_PASS, or "
            "$SNOW_CLIENT_ID and $SNOW_CLIENT_SECRET, or --snow-secret"
        )

    def _oauth_token(self) -> str:
        """client_credentials grant. Fetched once per run."""
        if self._token:
            return self._token
        response = self.session().post(
            f"{self.settings.base_url}/oauth_token.do",
            data={
                "grant_type": "client_credentials",
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
            },
            timeout=self.settings.timeout,
            verify=self.settings.verify_tls,
        )
        if response.status_code >= 400:
            raise ServiceNowError(
                f"OAuth token request failed ({response.status_code}): "
                f"{_short(response.text)}"
            )
        self._token = response.json().get("access_token")
        if not self._token:
            raise ServiceNowError("OAuth token response contained no access_token")
        return self._token

    def request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        response = self.session().request(
            method,
            f"{self.settings.base_url}{path}",
            auth=self._auth(),
            headers={**self._headers(), **kwargs.pop("headers", {})},
            timeout=self.settings.timeout,
            verify=self.settings.verify_tls,
            **kwargs,
        )
        if response.status_code == 429:
            raise ServiceNowError(
                "ServiceNow rate limit reached (HTTP 429); retry when the "
                f"limit resets: {response.headers.get('X-RateLimit-Reset', 'unknown')}"
            )
        if response.status_code >= 400:
            raise ServiceNowError(
                f"{method} {path} failed ({response.status_code}): {_short(response.text)}"
            )
        if not response.content:
            return {}
        try:
            return response.json().get("result", {})
        except ValueError as exc:
            raise ServiceNowError(f"{method} {path}: response was not JSON") from exc

    # -- changes ------------------------------------------------------------

    def create_change(self, fields: Mapping[str, Any]) -> Dict[str, Any]:
        result = self.request(
            "POST",
            CHANGE_API,
            json={k: v for k, v in fields.items() if v is not None},
            headers={"Content-Type": "application/json"},
        )
        return _flatten(result)

    def get_change(self, number: str) -> Dict[str, Any]:
        result = self.request(
            "GET", CHANGE_API, params={"sysparm_query": f"number={number}"}
        )
        records = result if isinstance(result, list) else [result]
        records = [_flatten(record) for record in records if record]
        if not records:
            raise ServiceNowError(f"no change found with number {number}")
        return records[0]

    def update_change(self, sys_id: str, fields: Mapping[str, Any]) -> Dict[str, Any]:
        return _flatten(
            self.request(
                "PATCH",
                f"{CHANGE_API}/{sys_id}",
                json={k: v for k, v in fields.items() if v is not None},
                headers={"Content-Type": "application/json"},
            )
        )

    def add_work_note(self, sys_id: str, text: str) -> None:
        self.update_change(sys_id, {"work_notes": text})

    def attach(self, sys_id: str, filename: str, payload: bytes, content_type: str) -> None:
        self.request(
            "POST",
            ATTACHMENT_API,
            params={
                "table_name": "change_request",
                "table_sys_id": sys_id,
                "file_name": filename,
            },
            data=payload,
            headers={"Content-Type": content_type},
        )


def _flatten(record: Any) -> Dict[str, Any]:
    """The Change API wraps every field as {"value": ..., "display_value": ...}.

    Flattened to the raw value, because that is what state comparisons need --
    the display value of a state is "Scheduled", not "-2".
    """
    if not isinstance(record, Mapping):
        return {}
    flat: Dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, Mapping) and "value" in value:
            flat[key] = value["value"]
            if "display_value" in value:
                flat[f"{key}_display"] = value["display_value"]
        else:
            flat[key] = value
    return flat


def _short(text: str, limit: int = 300) -> str:
    """ServiceNow error bodies are long and mostly boilerplate."""
    try:
        document = json.loads(text)
        detail = document.get("error", {})
        message = detail.get("message") or detail.get("detail") or text
    except (ValueError, AttributeError):
        message = text
    message = " ".join(str(message).split())
    return message[:limit] + ("…" if len(message) > limit else "")


# --------------------------------------------------------------------------- #
# what the tool actually does with a change
# --------------------------------------------------------------------------- #


def close_code_for(counts: Mapping[str, int]) -> Tuple[str, str]:
    """Pick a close code from the run's outcome.

    Nothing succeeded is a failed change. Some things succeeding and some not is
    what "successful with issues" is for -- reporting that as either wholly
    successful or wholly failed would be false.
    """
    failed = counts.get("failed", 0) + counts.get("unverified", 0)
    changed = counts.get("changed", 0)
    compliant = counts.get("ok", 0) + counts.get("skipped", 0)

    if failed and not (changed or compliant):
        return CLOSE_FAILED, "every device failed"
    if failed:
        return CLOSE_ISSUES, f"{failed} device(s) failed, {changed} changed"
    return CLOSE_SUCCESS, f"{changed} device(s) changed, {compliant} already compliant"


def ensure_implementable(change: Mapping[str, Any], settings: Settings) -> None:
    """Refuse to implement a change that has not been approved.

    This is the line the tool does not cross. Moving a change past Authorize is
    somebody's decision, not a script's.
    """
    state = str(change.get("state", ""))
    allowed = {settings.state(name) for name in IMPLEMENTABLE}
    if state in allowed:
        return
    raise ChangeNotApprovedError(
        f"{change.get('number', 'change')} is in {settings.state_name(state)} and cannot "
        f"be implemented from there -- it needs to reach "
        f"{' or '.join(IMPLEMENTABLE)} first. Approve it in ServiceNow, then re-run."
    )


def describe_plan(feature: str, mode: str, records: Mapping[str, Mapping[str, Any]]) -> str:
    """The implementation plan: every command, per device, as it would be sent.

    Already scrubbed -- these are the payload's commands, not the real ones.
    """
    lines: List[str] = []
    for name in sorted(records):
        record = records[name]
        commands = list(record.get("commands") or [])
        if record.get("save_command"):
            commands.append(record["save_command"])
        if not commands:
            continue
        lines.append(f"{name} ({record.get('hostname', '')}) [{record.get('platform')}]")
        lines.extend(f"    {command}" for command in commands)
        lines.append("")
    if not lines:
        return f"No changes required: every device is already compliant for {feature}."
    return f"netops {feature}, mode={mode}\n\n" + "\n".join(lines).rstrip()


def summarize_outcome(records: Mapping[str, Mapping[str, Any]]) -> str:
    """Work notes: what happened to each device, failures named."""
    lines: List[str] = []
    for name in sorted(records):
        record = records[name]
        status = record.get("status")
        if status == "failed":
            lines.append(f"{name}: FAILED -- {record.get('error')}")
        elif status == "unverified":
            missing = ", ".join(record.get("missing_after") or [])
            lines.append(f"{name}: APPLIED BUT NOT VERIFIED -- still missing {missing}")
        elif status == "changed":
            lines.append(f"{name}: changed ({len(record.get('commands') or [])} commands)")
        elif status == "skipped":
            lines.append(f"{name}: not applicable -- {record.get('skip_reason')}")
        else:
            lines.append(f"{name}: already compliant")
    return "\n".join(lines)
