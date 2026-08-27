import json

import pytest

from netops.standards import (
    StandardsError,
    find_standards,
    host_and_port,
    load,
    of,
)


def write(tmp_path, text, name="standards.yaml"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


SAMPLE = """
ntp:
  servers:
    - 10.50.0.10
    - host: 10.50.0.11
      prefer: true
snmp:
  allow:
    - 10.1.1.0/24
    - 10.2.0.0/16
  communities: []
acls:
  - name: SNMP-POLLERS
    permit: snmp.allow
"""


def test_loads_a_yaml_file(tmp_path):
    standards = load(None, write(tmp_path, SAMPLE))
    assert standards.loaded
    assert standards.value("snmp.allow") == ["10.1.1.0/24", "10.2.0.0/16"]


def test_loads_a_json_file(tmp_path):
    document = {"ntp": {"servers": ["10.50.0.10"]}}
    (tmp_path / "standards.json").write_text(json.dumps(document), encoding="utf-8")
    assert load(None, tmp_path).value("ntp.servers") == ["10.50.0.10"]


def test_a_missing_file_is_not_an_error(tmp_path):
    """The flags still work without one."""
    standards = load(None, tmp_path)
    assert not standards.loaded
    assert standards.entries("ntp.servers") == []


def test_an_explicitly_named_missing_file_is_an_error(tmp_path):
    with pytest.raises(StandardsError, match="standards file not found"):
        find_standards(str(tmp_path / "nope.yaml"), tmp_path)


def test_the_search_stops_at_the_project_directory(tmp_path, monkeypatch):
    """Self-contained: a standards file in the parent tree is not picked up."""
    parent = tmp_path
    project = tmp_path / "tool"
    project.mkdir()
    (parent / "standards.yaml").write_text("ntp: {servers: [10.9.9.9]}\n", encoding="utf-8")
    monkeypatch.chdir(project)
    assert find_standards(None, project) is None


def test_broken_yaml_is_reported_with_the_path(tmp_path):
    with pytest.raises(StandardsError, match="not valid YAML"):
        load(None, write(tmp_path, "ntp:\n  servers: [1,\n"))


def test_a_non_mapping_document_is_rejected(tmp_path):
    with pytest.raises(StandardsError, match="mapping of standard name"):
        load(None, write(tmp_path, "- just\n- a list\n"))


def test_unknown_sections_warn_rather_than_fail(tmp_path):
    """So the file can carry a section this tool does not own yet."""
    standards = load(None, write(tmp_path, "ntp: {servers: [10.1.1.1]}\nbigip: {thing: 1}\n"))
    assert standards.value("ntp.servers") == ["10.1.1.1"]
    assert any("unknown standard 'bigip'" in w for w in standards.warnings)


def test_unknown_keys_within_a_known_section_warn(tmp_path):
    standards = load(None, write(tmp_path, "ntp: {servers: [10.1.1.1], serverz: []}\n"))
    assert any("ntp.serverz" in w for w in standards.warnings)


def test_a_dotted_reference_resolves(tmp_path):
    standards = load(None, write(tmp_path, SAMPLE))
    acl = standards.value("acls")[0]
    assert standards.resolve(acl["permit"]) == ["10.1.1.0/24", "10.2.0.0/16"]


def test_a_hostname_is_not_mistaken_for_a_reference(tmp_path):
    standards = load(None, write(tmp_path, "ntp: {servers: [time.example.net]}\n"))
    assert standards.entries("ntp.servers") == ["time.example.net"]


def test_an_empty_list_is_a_statement_not_an_omission(tmp_path):
    """`communities: []` means 'there must be none', which is not the same as
    saying nothing about communities at all."""
    standards = load(None, write(tmp_path, SAMPLE))
    assert standards.defined("snmp.communities") is True
    assert standards.entries("snmp.communities") == []
    assert standards.defined("snmp.location") is False


def test_a_scalar_where_a_list_belongs_is_rejected(tmp_path):
    standards = load(None, write(tmp_path, "ntp: {servers: 10.1.1.1}\n"))
    with pytest.raises(StandardsError, match="must be a list"):
        standards.entries("ntp.servers")


@pytest.mark.parametrize(
    "item,expected",
    [
        ("10.1.1.50", {"host": "10.1.1.50", "port": 514}),
        ({"host": "10.1.1.51", "port": 1514}, {"host": "10.1.1.51", "port": 1514}),
        ({"host": "10.1.1.52"}, {"host": "10.1.1.52", "port": 514}),
    ],
)
def test_host_and_port_accepts_both_spellings(item, expected):
    assert host_and_port(item, default_port=514) == expected


def test_a_destination_without_a_host_is_rejected():
    with pytest.raises(StandardsError, match="no 'host' key"):
        host_and_port({"port": 514})


def test_of_tolerates_a_namespace_without_standards():
    import argparse

    assert of(argparse.Namespace()).loaded is False


def test_the_shipped_example_parses():
    """The example is what ships, so it must always be valid."""
    from netops.cli import PROJECT_ROOT

    standards = load(None, PROJECT_ROOT, allow_example=True)
    assert standards.loaded, "standards.yaml.example should ship with the tool"
    assert standards.path.name == "standards.yaml.example"
    assert standards.warnings == []
    assert standards.entries("ntp.servers")
    assert standards.defined("snmp.communities")


def test_a_real_run_never_falls_back_to_the_example(tmp_path):
    """Its addresses are placeholders. Converging a fleet onto them would be a
    great deal worse than stopping."""
    from netops.cli import PROJECT_ROOT

    assert find_standards(None, PROJECT_ROOT) is None
    assert load(None, PROJECT_ROOT).loaded is False


def test_the_example_is_found_only_when_asked_for():
    from netops.cli import PROJECT_ROOT

    assert find_standards(None, PROJECT_ROOT, allow_example=True) is not None
