import pytest

from netops.inventory import CSVInventory, InventoryError


def write_csv(tmp_path, text, name="hosts.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def load(tmp_path, text):
    return CSVInventory(csv_file=write_csv(tmp_path, text)).load()


def test_minimal_csv(tmp_path):
    inventory = load(tmp_path, "host\n10.1.1.1\n10.1.1.2\n")
    assert sorted(inventory.hosts) == ["10.1.1.1", "10.1.1.2"]
    # with no name column the address is the name
    assert inventory.hosts["10.1.1.1"].hostname == "10.1.1.1"


@pytest.mark.parametrize("column", ["host", "hostname", "ip", "ip_address", "address", "mgmt_ip"])
def test_address_column_aliases(tmp_path, column):
    inventory = load(tmp_path, f"{column},name\n10.1.1.1,sw1\n")
    assert inventory.hosts["sw1"].hostname == "10.1.1.1"


def test_headers_are_case_and_bom_tolerant(tmp_path):
    inventory = load(tmp_path, "﻿Host,Name,Platform\n10.1.1.1,sw1,IOS\n")
    assert inventory.hosts["sw1"].platform == "cisco_ios"


def test_platform_aliases_are_canonicalized(tmp_path):
    inventory = load(tmp_path, "host,name,platform\n10.1.1.1,a,eos\n10.1.1.2,b,ios-xe\n")
    assert inventory.hosts["a"].platform == "arista_eos"
    assert inventory.hosts["b"].platform == "cisco_ios"


def test_blank_platform_is_none_for_autodetection(tmp_path):
    inventory = load(tmp_path, "host,platform\n10.1.1.1,\n")
    assert inventory.hosts["10.1.1.1"].platform is None


def test_unknown_columns_become_filterable_data(tmp_path):
    inventory = load(tmp_path, "host,name,site,role\n10.1.1.1,sw1,atl,core\n")
    assert inventory.hosts["sw1"].data["site"] == "atl"
    assert inventory.hosts["sw1"].data["role"] == "core"
    assert inventory.hosts["sw1"].data["source_line"] == 2


def test_per_host_credentials_and_port(tmp_path):
    inventory = load(tmp_path, "host,name,username,password,port\n10.1.1.1,sw1,local,pw,2222\n")
    host = inventory.hosts["sw1"]
    assert (host.username, host.password, host.port) == ("local", "pw", 2222)


def test_per_host_secret_becomes_a_netmiko_extra(tmp_path):
    inventory = load(tmp_path, "host,name,secret\n10.1.1.1,sw1,enable-me\n")
    assert inventory.hosts["sw1"].connection_options["netmiko"].extras["secret"] == "enable-me"


def test_blank_rows_are_skipped(tmp_path):
    inventory = load(tmp_path, "host,name\n10.1.1.1,sw1\n\n,\n")
    assert list(inventory.hosts) == ["sw1"]


def test_duplicate_device_is_rejected(tmp_path):
    with pytest.raises(InventoryError, match="duplicate device"):
        load(tmp_path, "host,name\n10.1.1.1,sw1\n10.1.1.2,sw1\n")


def test_row_without_an_address_is_rejected(tmp_path):
    with pytest.raises(InventoryError, match="line 4|:4:"):
        load(tmp_path, "host,name\n10.1.1.1,sw1\n10.1.1.2,sw2\n,sw3\n")


def test_missing_address_column_is_rejected(tmp_path):
    with pytest.raises(InventoryError, match="needs one of these columns"):
        load(tmp_path, "device,name\nsw1,sw1\n")


def test_missing_file_is_rejected(tmp_path):
    with pytest.raises(InventoryError, match="not found"):
        CSVInventory(csv_file=str(tmp_path / "nope.csv")).load()


def test_empty_file_is_rejected(tmp_path):
    with pytest.raises(InventoryError, match="no header row"):
        load(tmp_path, "")


def test_header_only_file_is_rejected(tmp_path):
    with pytest.raises(InventoryError, match="no devices"):
        load(tmp_path, "host,name\n")


def test_defaults_supply_credentials_and_key_file(tmp_path):
    inventory = CSVInventory(
        csv_file=write_csv(tmp_path, "host,name\n10.1.1.1,sw1\n"),
        username="netauto",
        password="pw",
        secret="en",
        key_file="/home/netauto/.ssh/id_ed25519",
        port=830,
    ).load()
    host = inventory.hosts["sw1"]
    # nornir does not push defaults into hosts by itself; the plugin must
    assert host.defaults is inventory.defaults
    assert host.username == "netauto"
    assert host.password == "pw"
    assert host.port == 830
    extras = inventory.defaults.connection_options["netmiko"].extras
    assert extras["secret"] == "en"
    assert extras["use_keys"] is True
    assert extras["key_file"] == "/home/netauto/.ssh/id_ed25519"


def test_per_host_secret_keeps_the_fleet_key_file(tmp_path):
    """nornir replaces `extras` instead of merging, so the plugin merges."""
    inventory = CSVInventory(
        csv_file=write_csv(tmp_path, "host,name,secret\n10.1.1.1,sw1,enable-me\n"),
        username="netauto",
        key_file="/home/netauto/.ssh/id_ed25519",
    ).load()
    extras = inventory.hosts["sw1"].get_connection_parameters("netmiko").extras
    assert extras["secret"] == "enable-me"
    assert extras["key_file"] == "/home/netauto/.ssh/id_ed25519"
    assert extras["use_keys"] is True


def test_host_without_overrides_inherits_the_fleet_extras(tmp_path):
    inventory = CSVInventory(
        csv_file=write_csv(tmp_path, "host,name\n10.1.1.1,sw1\n"),
        username="netauto",
        password="pw",
        secret="en",
    ).load()
    parameters = inventory.hosts["sw1"].get_connection_parameters("netmiko")
    assert parameters.username == "netauto"
    assert parameters.password == "pw"
    assert parameters.extras["secret"] == "en"


def test_missing_credentials_reports_only_incomplete_hosts(tmp_path):
    from netops.inventory import init_nornir, missing_credentials

    csv_file = write_csv(
        tmp_path,
        "host,name,username,password\n"
        "10.1.1.1,has-own,local,localpw\n"
        "10.1.1.2,needs-fleet,,\n",
    )
    without = init_nornir(csv_file, None, None, None, None, 22, 1)
    assert missing_credentials(without) == ["needs-fleet"]

    with_fleet = init_nornir(csv_file, "netauto", "pw", None, None, 22, 1)
    assert missing_credentials(with_fleet) == []


def test_missing_credentials_accepts_key_auth(tmp_path):
    from netops.inventory import init_nornir, missing_credentials

    csv_file = write_csv(tmp_path, "host,name\n10.1.1.1,sw1\n")
    nr = init_nornir(csv_file, "netauto", None, None, "/key", 22, 1)
    assert missing_credentials(nr, key_file="/key") == []
    assert missing_credentials(nr) == ["sw1"]  # no password and no key: cannot log in
