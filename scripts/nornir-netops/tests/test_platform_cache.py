import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from netops import platform_cache
from netops.platform_cache import MAX_AGE, PlatformCache, cache_path, load


def host(name="sw1", hostname="10.1.1.1", port=None):
    return SimpleNamespace(name=name, hostname=hostname, port=port)


def cache(tmp_path, ttl=24.0):
    return PlatformCache(tmp_path / "cache.json", ttl)


def test_the_key_is_the_address_not_the_name():
    """Renaming a device in the CSV does not make it a different box, and two
    rows pointing at the same address are the same box."""
    assert PlatformCache.key(host(name="sw1")) == PlatformCache.key(host(name="renamed"))
    assert PlatformCache.key(host(hostname="10.1.1.2")) != PlatformCache.key(host())


def test_the_port_is_part_of_the_key():
    assert PlatformCache.key(host(port=2222)) == "10.1.1.1:2222"
    assert PlatformCache.key(host()) == "10.1.1.1:22"  # the default is explicit


def test_a_platform_survives_a_round_trip(tmp_path):
    first = cache(tmp_path)
    first.put(host(), "cisco_ios")
    first.save()

    second = cache(tmp_path).load()
    assert second.get(host()) == "cisco_ios"
    assert second.hits == 1


def test_an_unknown_device_is_not_remembered(tmp_path):
    assert cache(tmp_path).load().get(host()) is None


def test_an_entry_past_its_ttl_is_ignored(tmp_path):
    first = cache(tmp_path)
    first.put(host(), "cisco_ios")
    first.save()

    expired = PlatformCache(tmp_path / "cache.json", ttl_hours=0).load()
    assert expired.get(host()) is None


def test_the_ttl_boundary(tmp_path, monkeypatch):
    first = cache(tmp_path)
    first.put(host(), "cisco_ios")
    first.save()

    later = datetime.now(timezone.utc) + timedelta(hours=23, minutes=59)
    monkeypatch.setattr(platform_cache, "_now", lambda: later)
    assert cache(tmp_path).load().get(host()) == "cisco_ios"

    monkeypatch.setattr(platform_cache, "_now", lambda: later + timedelta(minutes=2))
    assert cache(tmp_path).load().get(host()) is None


def test_a_clean_run_writes_no_file(tmp_path):
    """Nothing detected, nothing to save."""
    cache(tmp_path).save()
    assert not (tmp_path / "cache.json").exists()


def test_a_corrupt_cache_is_ignored_rather_than_fatal(tmp_path):
    """It would be rebuilt by detecting, which is what would have happened
    without a cache at all."""
    (tmp_path / "cache.json").write_text("{ this is not json", encoding="utf-8")
    loaded = cache(tmp_path).load()
    assert loaded.entries == {}
    assert loaded.get(host()) is None


def test_a_cache_of_the_wrong_shape_is_ignored(tmp_path):
    (tmp_path / "cache.json").write_text('["a", "list"]', encoding="utf-8")
    assert cache(tmp_path).load().entries == {}


def test_entries_without_a_platform_are_dropped(tmp_path):
    (tmp_path / "cache.json").write_text(
        json.dumps({"devices": {"10.1.1.1:22": {"detected_at": "2026-01-01T00:00:00Z"}}}),
        encoding="utf-8",
    )
    assert cache(tmp_path).load().entries == {}


def test_very_old_entries_are_pruned_on_write(tmp_path, monkeypatch):
    """So a cache does not accumulate every device that has ever been in a CSV."""
    old = datetime.now(timezone.utc) - MAX_AGE - timedelta(days=1)
    monkeypatch.setattr(platform_cache, "_now", lambda: old)
    first = cache(tmp_path)
    first.put(host(name="gone", hostname="10.9.9.9"), "cisco_ios")
    first.save()

    monkeypatch.undo()
    second = cache(tmp_path).load()
    second.put(host(), "arista_eos")
    second.save()

    written = json.loads((tmp_path / "cache.json").read_text())["devices"]
    assert "10.1.1.1:22" in written
    assert "10.9.9.9:22" not in written


def test_the_write_is_atomic(tmp_path):
    """Two runs finishing together must not leave half a file behind."""
    first = cache(tmp_path)
    first.put(host(), "cisco_ios")
    first.save()
    leftovers = [p for p in tmp_path.iterdir() if p.name != "cache.json"]
    assert leftovers == []
    assert json.loads((tmp_path / "cache.json").read_text())["devices"]


def test_a_disabled_cache_remembers_nothing(tmp_path):
    disabled = load(str(tmp_path / "cache.json"), tmp_path, 24.0, enabled=False)
    disabled.put(host(), "cisco_ios")
    disabled.save()
    assert disabled.get(host()) is None
    assert not (tmp_path / "cache.json").exists()


def test_the_path_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("NETOPS_PLATFORM_CACHE", str(tmp_path / "elsewhere.json"))
    assert cache_path(None, tmp_path) == tmp_path / "elsewhere.json"


def test_an_explicit_path_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("NETOPS_PLATFORM_CACHE", str(tmp_path / "env.json"))
    assert cache_path(str(tmp_path / "flag.json"), tmp_path) == tmp_path / "flag.json"
