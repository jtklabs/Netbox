import json

import pytest

from netops import servicenow
from netops.servicenow import (
    CLOSE_FAILED,
    CLOSE_ISSUES,
    CLOSE_SUCCESS,
    ChangeNotApprovedError,
    Client,
    ServiceNowError,
    Settings,
    close_code_for,
    describe_plan,
    ensure_implementable,
    summarize_outcome,
)


# --------------------------------------------------------------------------- #
# a fake transport -- these tests never open a socket
# --------------------------------------------------------------------------- #


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})
        self.headers = headers or {}

    @property
    def content(self):
        return self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Records every call and replays queued responses."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0) if self.responses else FakeResponse(200, {"result": {}})

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


def client(responses=None, **overrides):
    settings = Settings(instance="acme", username="svc", password="pw", **overrides)
    return Client(settings, session=FakeSession(responses))


# --------------------------------------------------------------------------- #
# settings
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "instance,expected",
    [
        ("acme", "https://acme.service-now.com"),
        ("https://acme.service-now.com", "https://acme.service-now.com"),
        ("https://acme.service-now.com/", "https://acme.service-now.com"),
    ],
)
def test_base_url_accepts_a_name_or_a_url(instance, expected):
    assert Settings(instance=instance).base_url == expected


def test_a_missing_instance_says_how_to_set_it():
    with pytest.raises(ServiceNowError, match="SNOW_INSTANCE"):
        Settings().base_url


def test_state_values_can_be_overridden_per_instance():
    """Instances customise the change state model constantly."""
    settings = Settings(instance="acme", states={**servicenow.DEFAULT_STATES, "closed": "7"})
    assert settings.state("closed") == "7"
    assert settings.state_name("7") == "closed"


def test_an_unknown_state_name_is_an_error():
    with pytest.raises(ServiceNowError, match="no state value configured"):
        Settings(instance="acme").state("banana")


# --------------------------------------------------------------------------- #
# approval -- the line the tool does not cross
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("state", ["-2", "-1"])
def test_scheduled_and_implement_may_be_implemented(state):
    ensure_implementable({"number": "CHG1", "state": state}, Settings(instance="a"))


@pytest.mark.parametrize("state,name", [("-5", "new"), ("-4", "assess"), ("-3", "authorize")])
def test_an_unapproved_change_is_refused_by_name(state, name):
    with pytest.raises(ChangeNotApprovedError) as caught:
        ensure_implementable({"number": "CHG0012345", "state": state}, Settings(instance="a"))
    message = str(caught.value)
    assert "CHG0012345" in message
    assert name in message
    assert "Approve it in ServiceNow" in message


def test_refusal_uses_the_configured_state_names():
    settings = Settings(instance="a", states={**servicenow.DEFAULT_STATES, "assess": "99"})
    with pytest.raises(ChangeNotApprovedError, match="assess"):
        ensure_implementable({"number": "CHG1", "state": "99"}, settings)


# --------------------------------------------------------------------------- #
# close codes
# --------------------------------------------------------------------------- #


def test_everything_worked():
    code, reason = close_code_for({"changed": 3, "ok": 1})
    assert code == CLOSE_SUCCESS
    assert "3 device(s) changed" in reason


def test_some_failed_is_successful_with_issues():
    """Reporting a partial failure as wholly successful or wholly failed would
    both be false."""
    code, reason = close_code_for({"changed": 2, "failed": 1})
    assert code == CLOSE_ISSUES
    assert "1 device(s) failed" in reason


def test_an_unverified_device_counts_as_a_failure():
    assert close_code_for({"changed": 2, "unverified": 1})[0] == CLOSE_ISSUES


def test_nothing_worked_is_unsuccessful():
    assert close_code_for({"failed": 4})[0] == CLOSE_FAILED


def test_a_compliant_run_that_changed_nothing_is_successful():
    assert close_code_for({"ok": 5})[0] == CLOSE_SUCCESS


# --------------------------------------------------------------------------- #
# the client
# --------------------------------------------------------------------------- #


def test_create_change_posts_and_flattens_the_result():
    api = client([FakeResponse(200, {"result": {
        "number": {"value": "CHG0012345", "display_value": "CHG0012345"},
        "sys_id": {"value": "abc123"},
        "state": {"value": "-5", "display_value": "New"},
    }})])
    created = api.create_change({"short_description": "x", "state": "-5"})
    assert created["number"] == "CHG0012345"
    assert created["state"] == "-5"           # the raw value, for comparisons
    assert created["state_display"] == "New"  # kept for messages


def test_none_valued_fields_are_not_sent():
    api = client()
    api.create_change({"short_description": "x", "assignment_group": None})
    assert "assignment_group" not in api.session().calls[0]["json"]


def test_get_change_queries_by_number():
    api = client([FakeResponse(200, {"result": [{"number": {"value": "CHG1"}}]})])
    assert api.get_change("CHG1")["number"] == "CHG1"
    assert api.session().calls[0]["params"]["sysparm_query"] == "number=CHG1"


def test_a_change_that_does_not_exist():
    api = client([FakeResponse(200, {"result": []})])
    with pytest.raises(ServiceNowError, match="no change found"):
        api.get_change("CHG9")


def test_an_error_body_is_shortened_to_its_message():
    body = json.dumps({"error": {"message": "Insufficient rights", "detail": "x" * 500}})
    api = client([FakeResponse(403, None, body)])
    with pytest.raises(ServiceNowError, match="Insufficient rights"):
        api.get_change("CHG1")


def test_rate_limiting_is_named_as_such():
    api = client([FakeResponse(429, None, "", {"X-RateLimit-Reset": "60"})])
    with pytest.raises(ServiceNowError, match="rate limit"):
        api.get_change("CHG1")


def test_basic_auth_is_used_when_there_is_no_client_id():
    api = client([FakeResponse(200, {"result": [{"number": {"value": "CHG1"}}]})])
    api.get_change("CHG1")
    assert api.session().calls[0]["auth"] == ("svc", "pw")


def test_oauth_fetches_a_bearer_token_once():
    settings = Settings(instance="acme", client_id="id", client_secret="secret")
    session = FakeSession(
        [
            FakeResponse(200, {"access_token": "tok"}),
            FakeResponse(200, {"result": []}),
            FakeResponse(200, {"result": []}),
        ]
    )
    api = Client(settings, session=session)
    for _ in range(2):
        try:
            api.get_change("CHG1")
        except ServiceNowError:
            pass
    token_calls = [c for c in session.calls if c["url"].endswith("/oauth_token.do")]
    assert len(token_calls) == 1
    assert session.calls[-1]["headers"]["Authorization"] == "Bearer tok"


def test_missing_credentials_says_which_to_set():
    api = Client(Settings(instance="acme"), session=FakeSession())
    with pytest.raises(ServiceNowError, match="SNOW_USER"):
        api.get_change("CHG1")


def test_attach_names_the_table_and_record():
    api = client()
    api.attach("abc123", "report.json", b"{}", "application/json")
    call = api.session().calls[0]
    assert call["params"] == {
        "table_name": "change_request",
        "table_sys_id": "abc123",
        "file_name": "report.json",
    }
    assert call["data"] == b"{}"


# --------------------------------------------------------------------------- #
# what goes into the change
# --------------------------------------------------------------------------- #


RECORDS = {
    "sw1": {
        "hostname": "10.1.1.1",
        "platform": "cisco_ios",
        "status": "changed",
        "commands": ["ntp server 10.50.0.10"],
        "save_command": "write memory",
    },
    "leaf1": {"hostname": "10.1.1.2", "platform": "arista_eos", "status": "ok", "commands": []},
    "rtr1": {
        "hostname": "10.2.1.1",
        "platform": "cisco_ios",
        "status": "failed",
        "error": "timed out connecting",
        "commands": [],
    },
}


def test_the_plan_lists_the_commands_per_device():
    plan = describe_plan("ntp", "add", RECORDS)
    assert "sw1 (10.1.1.1) [cisco_ios]" in plan
    assert "    ntp server 10.50.0.10" in plan
    assert "    write memory" in plan
    assert "leaf1" not in plan  # nothing to do there


def test_a_plan_with_nothing_to_do_says_so():
    assert "already compliant" in describe_plan("ntp", "add", {"a": {"commands": []}})


def test_work_notes_name_the_failures():
    notes = summarize_outcome(RECORDS)
    assert "rtr1: FAILED -- timed out connecting" in notes
    assert "sw1: changed (1 commands)" in notes
    assert "leaf1: already compliant" in notes


def test_work_notes_call_out_an_unverified_device():
    records = {"sw1": {"status": "unverified", "missing_after": ["10.50.0.10"]}}
    assert "APPLIED BUT NOT VERIFIED" in summarize_outcome(records)


def test_the_plan_carries_no_secrets():
    """It is built from the payload's commands, which are already scrubbed."""
    records = {"sw1": {"commands": ["username admin secret <redacted>"], "platform": "ios"}}
    assert "<redacted>" in describe_plan("users", "add", records)
