import json

import pytest

from netops import credentials as creds
from netops.credentials import (
    AwsSecretSpec,
    CredentialError,
    fetch_aws_secret,
    find_env_file,
    load_env_file,
    parse_env_file,
    resolve,
)


def write_env(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_env_file_handles_comments_quotes_and_export(tmp_path):
    path = write_env(
        tmp_path,
        """
# fleet login
NET_USER=netauto
export NET_PASS="p@ss word#1"
NET_ENABLE='en:ble$'
NETOPS_CSV=inventory/prod.csv
""",
    )
    assert parse_env_file(path) == {
        "NET_USER": "netauto",
        # quotes stripped, no interpolation, '#' inside quotes preserved
        "NET_PASS": "p@ss word#1",
        "NET_ENABLE": "en:ble$",
        "NETOPS_CSV": "inventory/prod.csv",
    }


def test_parse_env_file_rejects_a_bad_line(tmp_path):
    path = write_env(tmp_path, "NET_USER netauto\n")
    with pytest.raises(CredentialError, match="expected KEY=VALUE"):
        parse_env_file(path)


def test_load_env_file_does_not_clobber_the_real_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("NET_USER", "from-shell")
    monkeypatch.delenv("NET_PASS", raising=False)
    load_env_file(write_env(tmp_path, "NET_USER=from-file\nNET_PASS=from-file\n"))
    import os

    assert os.environ["NET_USER"] == "from-shell"
    assert os.environ["NET_PASS"] == "from-file"


def test_find_env_file_prefers_cwd_then_project(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("A=1\n")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert find_env_file(None, project) == project / ".env"

    (cwd / ".env").write_text("A=2\n")
    assert find_env_file(None, project) == cwd / ".env"


def test_find_env_file_missing_explicit_is_an_error(tmp_path):
    with pytest.raises(CredentialError, match="env file not found"):
        find_env_file(str(tmp_path / "nope.env"), tmp_path)


class FakeSecretsClient:
    def __init__(self, payload, expected_region=None):
        self.payload = payload
        self.expected_region = expected_region
        self.requested = None

    def get_secret_value(self, SecretId):  # noqa: N803 - boto3's spelling
        self.requested = SecretId
        return {"SecretString": self.payload}


@pytest.fixture
def fake_boto3(monkeypatch):
    """Stand in for boto3 so no AWS call (or credential chain) is involved."""
    holder = {}

    def client(service, region_name=None, config=None):
        assert service == "secretsmanager"
        holder["region"] = region_name
        holder["config"] = config
        return holder["client"]

    import boto3

    monkeypatch.setattr(boto3, "client", client)
    return holder


def test_fetch_aws_secret_uses_the_configured_keys(fake_boto3):
    fake_boto3["client"] = FakeSecretsClient(
        json.dumps({"user": "netauto", "pw": "sekrit", "enable": "enabler", "other": "x"})
    )
    values = fetch_aws_secret(
        AwsSecretSpec(
            name="prod/network/readwrite",
            region="us-east-1",
            username_key="user",
            password_key="pw",
            enable_key="enable",
        )
    )
    assert values == {"username": "netauto", "password": "sekrit", "secret": "enabler"}
    assert fake_boto3["region"] == "us-east-1"
    assert fake_boto3["client"].requested == "prod/network/readwrite"
    config = fake_boto3["config"]
    assert config.connect_timeout == 5
    assert config.read_timeout == 10
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}


@pytest.mark.parametrize("error_name", ["ConnectTimeoutError", "ReadTimeoutError", "EndpointConnectionError"])
@pytest.mark.parametrize("recover", [False, True])
def test_secret_network_failures_retry_then_recover_or_stop(monkeypatch, error_name, recover):
    import io

    import boto3
    import botocore.endpoint
    import botocore.exceptions
    from botocore.awsrequest import AWSResponse
    from botocore.httpsession import URLLib3Session

    # Exercise botocore's actual retry loop without AWS access or real sleeps.
    real_client = boto3.client

    def client(service, **kwargs):
        return real_client(
            service,
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
            **kwargs,
        )

    error_type = getattr(botocore.exceptions, error_name)
    attempts = []

    class ResponseBody(io.BytesIO):
        def stream(self, amt=None, decode_content=False):
            yield self.read()

    def send(self, request):
        attempts.append(request)
        if not recover or len(attempts) == 1:
            raise error_type(endpoint_url=request.url)
        payload = json.dumps({"SecretString": '{"username": "a", "password": "b"}'})
        return AWSResponse(request.url, 200, {}, ResponseBody(payload.encode()))

    monkeypatch.setattr(boto3, "client", client)
    monkeypatch.setattr(URLLib3Session, "send", send)
    monkeypatch.setattr(botocore.endpoint.time, "sleep", lambda seconds: None)
    # Explicit client settings must bound retries even if the host asks for more.
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "20")
    spec = AwsSecretSpec(name="s", region="us-east-1")
    if recover:
        assert fetch_aws_secret(spec) == {"username": "a", "password": "b"}
        assert len(attempts) == 2
    else:
        with pytest.raises(CredentialError, match="could not read secret 's'") as exc:
            fetch_aws_secret(spec)
        assert isinstance(exc.value.__cause__, error_type)
        assert len(attempts) == 3


def test_fetch_aws_secret_defaults_to_username_password(fake_boto3):
    fake_boto3["client"] = FakeSecretsClient(json.dumps({"username": "a", "password": "b"}))
    assert fetch_aws_secret(AwsSecretSpec(name="s")) == {"username": "a", "password": "b"}


def test_fetch_aws_secret_names_the_missing_key(fake_boto3):
    fake_boto3["client"] = FakeSecretsClient(json.dumps({"username": "a"}))
    with pytest.raises(CredentialError, match="no 'password' key"):
        fetch_aws_secret(AwsSecretSpec(name="s"))


def test_fetch_aws_secret_rejects_non_json(fake_boto3):
    fake_boto3["client"] = FakeSecretsClient("just-a-password")
    with pytest.raises(CredentialError, match="not JSON"):
        fetch_aws_secret(AwsSecretSpec(name="s"))


def test_fetch_aws_secret_rejects_binary(fake_boto3):
    class Binary(FakeSecretsClient):
        def get_secret_value(self, SecretId):  # noqa: N803
            return {"SecretBinary": b"\x00"}

    fake_boto3["client"] = Binary("")
    with pytest.raises(CredentialError, match="binary"):
        fetch_aws_secret(AwsSecretSpec(name="s"))


def test_resolve_precedence_flag_beats_aws_beats_env(fake_boto3, monkeypatch):
    fake_boto3["client"] = FakeSecretsClient(
        json.dumps({"username": "aws-user", "password": "aws-pass"})
    )
    monkeypatch.setenv("NET_USER", "env-user")
    monkeypatch.setenv("NET_PASS", "env-pass")
    monkeypatch.setenv("NET_ENABLE", "env-enable")

    resolved = resolve(
        username="flag-user",
        password=None,
        secret=None,
        aws=AwsSecretSpec(name="s"),
        prompt=False,
    )
    assert resolved.username == "flag-user"  # flag wins
    assert resolved.password == "aws-pass"  # aws beats env
    assert resolved.secret == "env-enable"  # env fills what aws lacks
    assert "aws secret s" in resolved.source


def test_resolve_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("NET_USER", "env-user")
    monkeypatch.setenv("NET_PASS", "env-pass")
    resolved = resolve(username=None, password=None, secret=None, aws=None, prompt=False)
    assert (resolved.username, resolved.password) == ("env-user", "env-pass")
    assert resolved.source == "environment"


def test_resolve_prompts_only_when_asked(monkeypatch):
    monkeypatch.delenv("NET_PASS", raising=False)
    monkeypatch.setattr(creds, "ENV_PASSWORD", "NET_PASS")
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "typed")

    quiet = resolve(username="u", password=None, secret=None, aws=None, prompt=False)
    assert quiet.password is None

    asked = resolve(username="u", password=None, secret=None, aws=None, prompt=True)
    assert asked.password == "typed"
    assert "prompt" in asked.source


def test_resolve_does_not_prompt_when_using_a_key_file(monkeypatch):
    monkeypatch.delenv("NET_PASS", raising=False)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": pytest.fail("prompted"))
    resolved = resolve(
        username="u", password=None, secret=None, aws=None, prompt=True, key_file="~/.ssh/id"
    )
    assert resolved.password is None


def test_describe_never_includes_the_password():
    resolved = resolve(
        username="netauto", password="sekrit", secret=None, aws=None, prompt=False
    )
    assert "sekrit" not in resolved.describe()
    assert resolved.describe() == "netauto via command line"
