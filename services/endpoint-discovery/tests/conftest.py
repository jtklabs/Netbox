import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SERVICE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE))
sys.path.insert(0, str(SERVICE.parents[1] / "scripts" / "snmp-inventory"))

from fastapi.testclient import TestClient

from discovery.api import create_app
from discovery.config import Settings, digest_token
from discovery.schemas import Target
from discovery.store import migrate, provision

TOKENS = {"reader": "r" * 48, "poller": "p" * 48, "foreign": "f" * 48, "global": "g" * 48}


@pytest.fixture
def api(tmp_path):
    postgres = os.getenv("TEST_DATABASE_URL")
    schema = "test_" + uuid4().hex
    if postgres:
        root = create_engine(postgres)
        with root.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = make_url(postgres).update_query_dict({"options": "-csearch_path=" + schema}).render_as_string(hide_password=False)
    else:
        url = "sqlite:///" + str(tmp_path / "discovery.sqlite3")
    principals = [
        {"name": "portal", "role": "reader", "scopes": ["a/site"], "token_sha256": digest_token(TOKENS["reader"])},
        {"name": "poller-a", "role": "poller", "scopes": ["a/site"], "token_sha256": digest_token(TOKENS["poller"])},
        {"name": "poller-b", "role": "poller", "scopes": ["b/site"], "token_sha256": digest_token(TOKENS["foreign"])},
        {"name": "global", "role": "reader", "scopes": ["a/site", "b/site"], "token_sha256": digest_token(TOKENS["global"])},
    ]
    access = tmp_path / "access.json"
    access.write_text(json.dumps({"principals": principals}))
    app = create_app(Settings(url, str(access)))
    migrate(app.state.engine)
    migrate(app.state.engine)  # deployment reruns must be harmless
    targets = [Target(key="switch-a", scope="a/site", poller="poller-a", address="192.0.2.10", name="access-a",
                      netbox_device_id=42, interface_ids={"Gi1": 101}),
               Target(key="router-a", scope="a/site", poller="poller-a", address="192.0.2.1", name="router-a"),
               Target(key="switch-b", scope="b/site", poller="poller-b", address="192.0.2.20", name="access-b")]
    provision(app.state.factory, targets)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + TOKENS["reader"]
        yield client, app
    if postgres:
        with root.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root.dispose()


def authorization(role):
    return {"Authorization": "Bearer " + TOKENS[role]}
