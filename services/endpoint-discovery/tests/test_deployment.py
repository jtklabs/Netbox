import json
from pathlib import Path

import yaml

SERVICE = Path(__file__).resolve().parents[1]


def test_poller_deployment_has_no_database_credentials_or_inbound_ports():
    config = yaml.safe_load((SERVICE / "compose.poller.yaml").read_text())
    poller = config["services"]["poller"]
    assert "ports" not in poller
    assert all("DATABASE" not in key and "NETBOX" not in key for key in poller["environment"])
    assert poller["cap_drop"] == ["ALL"] and poller["read_only"] is True
    assert poller["entrypoint"] == ["python", "-m", "discovery.poller"]


def test_server_uses_independent_database_and_readonly_mounts():
    config = yaml.safe_load((SERVICE / "compose.yaml").read_text())
    api = config["services"]["api"]
    assert "DISCOVERY_DATABASE_URL" in api["environment"]
    assert all("NETBOX" not in key for key in api["environment"])
    assert all(volume.endswith(":ro") for volume in api["volumes"])


def test_examples_validate_without_real_credentials():
    from discovery.schemas import Target
    targets = [Target.model_validate(t) for t in json.loads((SERVICE / "examples/targets.json").read_text())]
    assert len(targets) == 1
    mapping = json.loads((SERVICE / "examples/netbox-sites.json").read_text())
    assert mapping[0]["scope"] == targets[0].scope
