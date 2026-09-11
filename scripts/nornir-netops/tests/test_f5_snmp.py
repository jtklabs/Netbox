"""SNMP through real CLI, inventory and Nornir dispatch with a simulated BIG-IP."""

import copy
import json
from types import SimpleNamespace

import pytest

from netops import cli, f5_snmp as snmp, f5_waf
from netops.features.snmp import FEATURE
from test_snmp import DOCUMENT, AUTH, PRIV, parse_args
from test_waf import setup as waf_setup


@pytest.fixture
def setup(waf_setup, monkeypatch):
    setup = waf_setup
    setup.standards.write_text(json.dumps(DOCUMENT))
    monkeypatch.setenv("NETOPS_SNMP_AUTH_NMSUSER", AUTH)
    monkeypatch.setenv("NETOPS_SNMP_PRIV_NMSUSER", PRIV)
    setup.box.responses[snmp.SNMP] = {
        "allowedAddresses": ["127.", "192.0.2.0/24"],
        "snmpv1": "enabled", "snmpv2c": "enabled", "sysContact": "old",
        "sysLocation": "old", "agentAddresses": ["udp:161"], "bigipTraps": "enabled",
    }
    setup.box.responses[snmp.USERS] = {"items": [
        {"name": "old-resource", "username": "old-user", "access": "ro", "oidSubset": ".1",
         "authPasswordEncrypted": "encrypted-old-auth", "privacyPasswordEncrypted": "encrypted-old-privacy"}]}
    setup.box.responses[snmp.COMMUNITIES] = {"items": [
        {"name": "comm-public", "communityName": "old-community-password"}]}

    def request(method, path, body=None):
        setup.box.writes.append((method, path, copy.deepcopy(body)))
        if path in setup.box.reject:
            raise RuntimeError("rejected " + AUTH + " " + PRIV + " old-community-password")
        if path in setup.box.ignore:
            return {}
        if path == snmp.SNMP:
            assert method == "PATCH"
            setup.box.responses[path].update(body)
        elif method == "POST":
            assert path == snmp.USERS
            setup.box.responses[path]["items"].append(copy.deepcopy(body))
        else:
            collection = snmp.USERS if path.startswith(snmp.USERS + "/") else snmp.COMMUNITIES
            items = setup.box.responses[collection]["items"]
            found = next(item for item in items if snmp.resource(item, collection) == path)
            if method == "DELETE":
                items.remove(found)
            else:
                assert method == "PATCH"
                found.update(body)
        return {}

    setup.box.request = request
    original = setup.run
    setup.run = lambda *args, **kwargs: original(*args, feature="snmp", **kwargs)
    return setup


def test_dry_run_netbox_and_redaction(setup, capsys):
    code, report = setup.run("--replace", "--fail-on-diff", netbox_inventory=True)
    assert code == cli.EXIT_DIFF
    row = report["devices"]["f5"]
    assert row["status"] == "pending"
    assert "community:comm-public" in row["remove"]
    assert "user:old-user" in row["remove"]
    assert any("authPassword" in command and "<redacted>" in command for command in row["commands"])
    assert setup.box.writes == setup.nb.writes == []
    assert setup.box.saves == 0
    output = json.dumps(report) + capsys.readouterr().out
    for secret in (AUTH, PRIV, "old-community-password", "encrypted-old-auth", "encrypted-old-privacy"):
        assert secret not in output
    assert all(path.startswith(snmp.SNMP) for path in setup.box.reads)
    assert "syslog_compliant" not in row

    assert not row["backout"]["complete"]
    steps = row["backout"]["steps"]
    assert any(step.get("method") == "DELETE" and "/users/" in step["path"] for step in steps)
    assert any("communityName" in step.get("requires_secret_fields", []) for step in steps)
    assert any(step["body"].get("snmpv2c") == "enabled" for step in steps if step.get("body"))


@pytest.mark.parametrize("replace", [False, True])
def test_apply_preserves_unrelated_config_and_second_run_is_idempotent(setup, replace):
    flags = ["--apply", "--f5-insecure"] + (["--replace"] if replace else [])
    code, report = setup.run(*flags, netbox_inventory=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["f5"]
    assert row["verified"] and row["saved"]
    root = setup.box.responses[snmp.SNMP]
    assert root["snmpv1"] == root["snmpv2c"] == "disabled"
    assert root["agentAddresses"] == ["udp:161"] and root["bigipTraps"] == "enabled"
    assert root["allowedAddresses"] == (["127."] if replace else ["127.", "192.0.2.0/24"]) + ["10.1.1.0/24"]
    assert setup.box.responses[snmp.COMMUNITIES]["items"] == []
    users = setup.box.responses[snmp.USERS]["items"]
    assert len(users) == (1 if replace else 2)
    user = next(user for user in users if user["username"] == "nmsuser")
    assert user["access"] == "ro" and user["oidSubset"] == ".1"
    assert user["authProtocol"] == "sha" and user["privacyProtocol"] == "aes"
    assert user["securityLevel"] == "auth-privacy"
    assert user["authPassword"] == AUTH and user["privacyPassword"] == PRIV
    assert setup.box.logins[0][4]["verify_tls"] is False
    assert setup.nb.writes == []
    count = len(setup.box.writes)
    assert setup.run(*flags, netbox_inventory=True)[1]["devices"]["f5"]["status"] == "ok"
    assert len(setup.box.writes) == count and setup.box.saves == 1


def test_rewrite_users_preserves_resource_identity_and_description(setup):
    setup.run("--apply")
    user = setup.box.responses[snmp.USERS]["items"][-1]
    user.update(name="record-id", description="keep me")
    user["selfLink"] = "https://localhost" + snmp.USERS + "/record-id?ver=17.1.0"
    user.pop("authPassword")
    user.pop("privacyPassword")
    setup.box.writes.clear()
    code, report = setup.run("--apply", "--rewrite-users")
    assert code == cli.EXIT_OK and report["devices"]["f5"]["verified"]
    assert len(setup.box.writes) == 1
    assert setup.box.writes[0][:2] == ("PATCH", snmp.USERS + "/record-id")
    assert user["description"] == "keep me" and user["authPassword"] == AUTH


@pytest.mark.parametrize("field,value", [("access", "rw"), ("oidSubset", ".1.3.6"),
                                        ("authProtocol", "md5"), ("securityLevel", "auth-no-privacy")])
def test_user_metadata_drift_updates_without_deleting_user(setup, field, value):
    setup.run("--apply")
    user = setup.box.responses[snmp.USERS]["items"][-1]
    user[field] = value
    setup.box.writes.clear()
    code, report = setup.run("--apply")
    assert code == cli.EXIT_OK and report["devices"]["f5"]["verified"]
    assert [operation[0] for operation in setup.box.writes] == ["PATCH"]
    assert user[field] != value


@pytest.mark.parametrize("level,auth,priv", [("noauth", None, None), ("auth", "sha", None),
                                          ("priv", "sha256", "aes192")])
def test_security_levels_and_read_write_view_mapping(setup, level, auth, priv):
    doc = copy.deepcopy(DOCUMENT)
    doc["snmp"]["groups"][0].update(security=level, write="NMS-VIEW")
    doc["snmp"]["users"][0].update(auth=auth, priv=priv)
    setup.standards.write_text(json.dumps(doc))
    assert setup.run("--apply")[0] == cli.EXIT_OK
    user = setup.box.responses[snmp.USERS]["items"][-1]
    assert user["securityLevel"] == snmp.LEVELS[level] and user["access"] == "rw"
    assert ("authPassword" in user) is (auth is not None)
    assert ("privacyPassword" in user) is (priv is not None)


def test_duplicate_username_fails_before_mutation(setup):
    duplicate = copy.deepcopy(setup.box.responses[snmp.USERS]["items"][0])
    duplicate["name"] = "another-resource"
    setup.box.responses[snmp.USERS]["items"].append(duplicate)
    assert setup.run("--apply")[0] == cli.EXIT_FAILED
    assert setup.box.writes == []


@pytest.mark.parametrize("failure", ["reject", "ignore", "save", "read"])
def test_failure_does_not_claim_verification_or_save(setup, failure):
    if failure == "reject":
        setup.box.reject.add(snmp.USERS)
    elif failure == "ignore":
        setup.box.ignore.add(snmp.SNMP)
    elif failure == "save":
        setup.box.save_failure = True
    else:
        setup.box.responses[snmp.USERS] = RuntimeError("cannot read users")
    code, report = setup.run("--apply")
    assert code == cli.EXIT_FAILED
    row = report["devices"]["f5"]
    assert setup.box.saves == 0
    assert AUTH not in json.dumps(report) and PRIV not in json.dumps(report)
    if failure == "read":
        assert setup.box.writes == []
    if failure == "ignore":
        assert row["verified"] is False and row["saved"] is False


def test_no_verify_no_save_and_tls_env(setup, monkeypatch):
    monkeypatch.setenv("NETOPS_F5_VERIFY_TLS", "false")
    code, report = setup.run("--apply", "--no-verify", "--no-save", direct=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["192.0.2.1"]
    assert row["verified"] is None and row["saved"] is None
    assert setup.box.saves == 0
    assert setup.box.logins[0][4]["verify_tls"] is False


def test_aws_login_and_snmp_secrets_are_separate(setup, monkeypatch):
    from netops import credentials
    calls = []

    def fetch(name, region):
        calls.append((name, region))
        return ({"login": "aws-admin", "password-key": "aws-login-pass"} if name == "device-secret"
                else {"nmsuser": {"auth": "aws-auth-password", "priv": "aws-priv-password"}})

    monkeypatch.setattr(credentials, "fetch_json_secret", fetch)
    code, report = setup.run("--apply", "--aws-secret", "device-secret", "--aws-username-key", "login",
                             "--aws-password-key", "password-key", "--passphrase-secret", "snmp-secret",
                             "--aws-region", "us-east-1", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert set(calls) == {("device-secret", "us-east-1"), ("snmp-secret", "us-east-1")}
    assert setup.box.logins[0][2:4] == ("aws-admin", "aws-login-pass")
    assert setup.box.responses[snmp.USERS]["items"][-1]["authPassword"] == "aws-auth-password"
    assert "aws-auth-password" not in json.dumps(report)


def test_undefined_communities_leaves_protocols_and_communities_alone(setup):
    doc = copy.deepcopy(DOCUMENT)
    del doc["snmp"]["communities"]
    setup.standards.write_text(json.dumps(doc))
    assert setup.run("--apply", "--replace")[0] == cli.EXIT_OK
    assert setup.box.responses[snmp.SNMP]["snmpv2c"] == "enabled"
    assert setup.box.responses[snmp.COMMUNITIES]["items"]
    assert snmp.COMMUNITIES not in setup.box.reads


def test_explicit_empty_allow_with_no_localhost(setup):
    doc = copy.deepcopy(DOCUMENT)
    doc["snmp"]["allow"] = []
    setup.standards.write_text(json.dumps(doc))
    assert setup.run("--apply", "--replace", "--f5-no-localhost")[0] == cli.EXIT_OK
    assert setup.box.responses[snmp.SNMP]["allowedAddresses"] == []


@pytest.mark.parametrize("change,expected", [
    (lambda s: s.pop("allow"), "snmp.allow"),
    (lambda s: s.update(allow=["all"]), "network"),
    (lambda s: s["users"][0].update(auth="sha384"), "authentication"),
    (lambda s: s["users"][0].update(priv="3des"), "privacy"),
    (lambda s: s["groups"][0].update(security="auth"), "protocols"),
    (lambda s: s["views"][0].update(action="excluded"), "included"),
    (lambda s: s["views"][0].update(oid="unknownMib"), "numeric OID"),
    (lambda s: s["groups"][0].update(write="missing-view"), "subsets"),
    (lambda s: s["users"][0].update(group="undefined"), "define its group"),
])
def test_unsupported_standard_fails_before_login(setup, change, expected):
    doc = copy.deepcopy(DOCUMENT)
    change(doc["snmp"])
    setup.standards.write_text(json.dumps(doc))
    code, report = setup.run("--apply")
    assert code == cli.EXIT_FAILED
    assert expected in report["devices"]["f5"]["error"]
    assert setup.box.logins == setup.box.writes == []


def test_allow_normalizes_netmasks_ipv6_and_duplicate_entries(setup):
    root, users = snmp.desired_state(FEATURE.build_desired(parse_args()).variables)
    root["allowedAddresses"] = ["127.0.0.0/8", "10.1.1.0/24", "2001:db8::/64"]
    setup.box.responses[snmp.SNMP]["allowedAddresses"] = ["127.", "10.1.1.0/255.255.255.0",
        "2001:0db8:0:0::/64", "10.1.1.0/24", "ALL"]
    ops, add, remove, _, _ = snmp.plan(setup.box, root, users, True, True, False, [])
    assert not any(value.startswith("allow:") for value in add)
    assert "allow:ALL" in remove and "allow:10.1.1.0/24" in remove
    assert ops[0][2]["allowedAddresses"] == ["127.", "10.1.1.0/255.255.255.0", "2001:0db8:0:0::/64"]


def test_collection_pagination_and_scope_validation(setup):
    next_path = snmp.USERS + "?$skip=1"
    setup.box.responses[snmp.USERS]["nextLink"] = "https://localhost" + next_path
    setup.box.responses[next_path] = {"items": [{"name": "other", "username": "other"}]}
    assert len(snmp.collection(setup.box, snmp.USERS, [])) == 2
    setup.box.responses[next_path]["nextLink"] = snmp.USERS
    with pytest.raises(ValueError, match="repeated"):
        snmp.collection(setup.box, snmp.USERS, [])
    setup.box.responses[next_path]["nextLink"] = "/mgmt/tm/sys/config"
    with pytest.raises(ValueError, match="unexpected"):
        snmp.collection(setup.box, snmp.USERS, [])


@pytest.mark.parametrize("bad", [{"items": "bad"}, {"items": [{"username": "no-name"}]}])
def test_malformed_collection_fails_before_writes(setup, bad):
    setup.box.responses[snmp.USERS] = bad
    assert setup.run("--apply")[0] == cli.EXIT_FAILED
    assert setup.box.writes == []


@pytest.mark.parametrize("status,text", [(204, ""), (200, "")])
def test_client_accepts_empty_delete_response(status, text):
    client = f5_waf.Client(SimpleNamespace(hostname="192.0.2.1", username="u", password="p"))
    client.session.request = lambda *a, **kw: SimpleNamespace(status_code=status, text=text)
    assert client.request("DELETE", snmp.COMMUNITIES + "/old") == {}
    client.session.close()
