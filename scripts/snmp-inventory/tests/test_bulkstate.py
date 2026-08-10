"""Stepping GETBULK down, and remembering where it landed.

A device whose replies are too big for the path answers no GETBULK at the
default size. Finding the size it *can* manage costs a timeout per rung, which
is acceptable once and absurd every six hours — hence the cache.
"""

from __future__ import annotations

import json
import time

import pytest

from snmpinv.bulkstate import GETNEXT_ONLY, BulkState
from snmpinv.snmp import (
    BULK_LADDER,
    Credential,
    CredentialSession,
    SnmpTimeoutError,
)

_ONE_BIND = ".1.3.6.1.2.1.1.5.0 = STRING: sw1\n"


def _session(answers_at: int, configured: int = 25):
    """A session whose device only answers GETBULK at or below `answers_at`."""
    session = CredentialSession(
        Credential(name="t", security_name="u", auth_passphrase="a"),
        timeout=1, retries=0, max_repetitions=configured,
    )
    session.answered = True
    tried = []

    def fake_run(tool, host, oid, numeric_timeticks=True):
        if tool == "snmpbulkwalk":
            tried.append(session.max_repetitions)
            if session.max_repetitions > answers_at:
                raise SnmpTimeoutError("no response")
        else:
            tried.append("getnext")
        return _ONE_BIND

    session._run = fake_run
    return session, tried


class TestSteppingDown:
    def test_it_lands_on_the_first_rung_the_device_can_manage(self):
        session, tried = _session(answers_at=10)
        session.walk("192.0.2.1", "1.3.6.1.2.1.2")

        assert tried == [25, 10]
        assert session.settled_repetitions() == 10

    def test_it_keeps_stepping_when_the_first_rung_is_still_too_big(self):
        session, tried = _session(answers_at=4)
        session.walk("192.0.2.1", "1.3.6.1.2.1.2")

        assert tried == [25, 10, 4]
        assert session.settled_repetitions() == 4

    def test_a_device_that_answers_no_getbulk_ends_on_getnext(self):
        session, tried = _session(answers_at=0)
        session.walk("192.0.2.1", "1.3.6.1.2.1.2")

        assert tried == [25, 10, 4, "getnext"]
        assert session.use_bulk is False
        assert session.settled_repetitions() == GETNEXT_ONLY

    def test_the_ladder_is_short_enough_to_be_worth_walking(self):
        """Each rung costs a full timeout, so this is a real constraint and
        not just a style preference."""
        assert len(BULK_LADDER) <= 3
        assert list(BULK_LADDER) == sorted(BULK_LADDER, reverse=True)

    def test_what_it_settles_on_is_reused_for_later_tables(self):
        """Otherwise every table pays the discovery again."""
        session, tried = _session(answers_at=10)
        session.walk("192.0.2.1", "1.3.6.1.2.1.2")
        session.walk("192.0.2.1", "1.3.6.1.2.1.4")
        session.walk("192.0.2.1", "1.3.6.1.2.1.31")

        assert tried == [25, 10, 10, 10]

    def test_rungs_at_or_above_the_configured_size_are_skipped(self):
        """Configuring a low max_repetitions must not be quietly raised."""
        session, tried = _session(answers_at=4, configured=8)
        session.walk("192.0.2.1", "1.3.6.1.2.1.2")

        assert tried == [8, 4]


class TestTheCache:
    def test_a_limit_survives_to_the_next_run(self, tmp_path):
        path = str(tmp_path / "limits.json")

        first = BulkState(path)
        first.remember("10.0.0.1", 10, configured=25)
        first.save()

        assert BulkState(path).limit_for("10.0.0.1", 25) == 10

    def test_getnext_only_survives_too(self, tmp_path):
        path = str(tmp_path / "limits.json")
        first = BulkState(path)
        first.remember("10.0.0.1", GETNEXT_ONLY, configured=25)
        first.save()

        assert BulkState(path).limit_for("10.0.0.1", 25) == GETNEXT_ONLY

    def test_a_device_that_managed_the_full_size_is_not_recorded(self, tmp_path):
        """Keeps the file to the exceptions, and means raising the fleet-wide
        setting later actually reaches devices that were always fine."""
        path = str(tmp_path / "limits.json")
        state = BulkState(path)
        state.remember("10.0.0.1", 25, configured=25)
        state.save()

        assert len(state) == 0
        # Nothing changed, so there was nothing to write.
        assert not (tmp_path / "limits.json").exists()

    def test_lowering_the_configured_size_wins_over_a_cheerier_measurement(self, tmp_path):
        path = str(tmp_path / "limits.json")
        state = BulkState(path)
        state.remember("10.0.0.1", 10, configured=25)

        assert state.limit_for("10.0.0.1", 4) == 4

    def test_a_device_recovering_is_recorded(self, tmp_path):
        """A path that gets fixed should stop being scanned pessimistically."""
        path = str(tmp_path / "limits.json")
        state = BulkState(path)
        state.remember("10.0.0.1", 4, configured=25)
        state.remember("10.0.0.1", 25, configured=25)
        state.save()

        assert BulkState(path).limit_for("10.0.0.1", 25) == 25

    def test_an_expired_entry_is_re_measured(self, tmp_path):
        path = tmp_path / "limits.json"
        path.write_text(json.dumps({
            "10.0.0.1": {"max_repetitions": 4,
                         "measured_at": time.time() - 30 * 86400},
        }))
        assert BulkState(str(path), ttl_days=7).limit_for("10.0.0.1", 25) == 25

    def test_a_fresh_entry_is_not(self, tmp_path):
        path = tmp_path / "limits.json"
        path.write_text(json.dumps({
            "10.0.0.1": {"max_repetitions": 4, "measured_at": time.time()},
        }))
        assert BulkState(str(path), ttl_days=7).limit_for("10.0.0.1", 25) == 4


class TestTheCacheNeverBreaksAScan:
    """It is an optimisation. Every failure mode must be survivable."""

    def test_a_corrupt_file_is_ignored(self, tmp_path):
        path = tmp_path / "limits.json"
        path.write_text("{ this is not json")
        assert BulkState(str(path)).limit_for("10.0.0.1", 25) == 25

    def test_a_file_of_the_wrong_shape_is_ignored(self, tmp_path):
        path = tmp_path / "limits.json"
        path.write_text(json.dumps(["not", "a", "map"]))
        assert BulkState(str(path)).limit_for("10.0.0.1", 25) == 25

    def test_junk_entries_are_skipped_without_losing_good_ones(self, tmp_path):
        path = tmp_path / "limits.json"
        path.write_text(json.dumps({
            "bad": {"max_repetitions": "not a number", "measured_at": time.time()},
            "alsobad": "a string",
            "good": {"max_repetitions": 4, "measured_at": time.time()},
        }))
        state = BulkState(str(path))
        assert state.limit_for("good", 25) == 4
        assert state.limit_for("bad", 25) == 25

    def test_an_unwritable_path_does_not_raise(self, tmp_path):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("")
        state = BulkState(str(blocker / "sub" / "limits.json"))
        state.remember("10.0.0.1", 4, configured=25)
        state.save()          # must not raise

    def test_no_path_means_no_file_and_no_error(self, tmp_path):
        state = BulkState()
        state.remember("10.0.0.1", 4, configured=25)
        state.save()
        assert state.limit_for("10.0.0.1", 25) == 4    # in memory only

    def test_the_write_is_atomic(self, tmp_path):
        """A killed poller must not leave a half-written file behind that the
        next run then refuses to parse."""
        path = tmp_path / "limits.json"
        state = BulkState(str(path))
        state.remember("10.0.0.1", 4, configured=25)
        state.save()
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".bulkstate-")]
        assert leftovers == []
        assert json.loads(path.read_text())["10.0.0.1"]["max_repetitions"] == 4


class TestTheCollectorUsesIt:
    def test_a_remembered_limit_is_applied_to_a_new_session(self, tmp_path):
        from snmpinv.collect import Collector

        state = BulkState()
        state.remember("10.0.0.1", 4, configured=25)
        collector = Collector(
            [Credential(name="t", security_name="u", auth_passphrase="a")],
            max_repetitions=25, bulk_state=state,
        )
        session = collector._session_for(collector.credentials[0], "10.0.0.1")

        assert session.max_repetitions == 4
        assert session.use_bulk is True

    def test_a_remembered_getnext_only_device_skips_getbulk_entirely(self, tmp_path):
        """The point of caching it: no timeout is paid to rediscover this."""
        from snmpinv.collect import Collector

        state = BulkState()
        state.remember("10.0.0.1", GETNEXT_ONLY, configured=25)
        collector = Collector(
            [Credential(name="t", security_name="u", auth_passphrase="a")],
            max_repetitions=25, bulk_state=state,
        )
        session = collector._session_for(collector.credentials[0], "10.0.0.1")

        assert session.use_bulk is False

    def test_an_unknown_host_gets_the_configured_size(self):
        from snmpinv.collect import Collector

        collector = Collector(
            [Credential(name="t", security_name="u", auth_passphrase="a")],
            max_repetitions=25, bulk_state=BulkState(),
        )
        session = collector._session_for(collector.credentials[0], "10.9.9.9")

        assert session.max_repetitions == 25
        assert session.use_bulk is True


class TestTheMeasurementSurvivesAFailedScan:
    """The pathological device is exactly the one worth remembering.

    Discovery costs a timeout per rung. If a scan that then fails throws that
    measurement away, the worst device on the fleet re-pays the whole ladder
    every run — which for a six-hourly rescan is the most expensive case
    landing in the one place the cache was supposed to help.
    """

    def _collector(self, state, walk_outcome):
        from snmpinv import collect as collect_module

        class Session:
            def __init__(self, *a, **k):
                self.answered = False
                self.use_bulk = True
                self.max_repetitions = 25

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def probe(self, host):
                self.answered = True
                return True

            def walk(self, host, oid):
                # Stands in for the ladder having run all the way down.
                self.use_bulk = False
                return walk_outcome()

            def settled_repetitions(self):
                return self.max_repetitions if self.use_bulk else GETNEXT_ONLY

        collect_module.CredentialSession = Session
        return collect_module.Collector(
            [Credential(name="t", security_name="u", auth_passphrase="a")],
            max_repetitions=25, bulk_state=state,
        )

    def test_a_timeout_after_the_ladder_still_records_the_limit(self, monkeypatch):
        from snmpinv import collect as collect_module

        original = collect_module.CredentialSession
        state = BulkState()
        try:
            def times_out():
                raise SnmpTimeoutError("even GETNEXT did not fit")

            collector = self._collector(state, times_out)
            with pytest.raises(SnmpTimeoutError):
                collector.collect("10.0.0.1")
        finally:
            collect_module.CredentialSession = original

        assert state.limit_for("10.0.0.1", 25) == GETNEXT_ONLY

    def test_a_host_that_never_answered_records_nothing(self, monkeypatch):
        """Its 'settled' value would just be the starting value, which says
        nothing about the device."""
        from snmpinv import collect as collect_module

        original = collect_module.CredentialSession
        state = BulkState()

        class Silent:
            def __init__(self, *a, **k):
                self.answered = False
                self.use_bulk = True
                self.max_repetitions = 25

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def probe(self, host):
                raise SnmpTimeoutError("no response")

            def settled_repetitions(self):
                return GETNEXT_ONLY

        try:
            collect_module.CredentialSession = Silent
            collector = collect_module.Collector(
                [Credential(name="t", security_name="u", auth_passphrase="a")],
                max_repetitions=25, bulk_state=state,
            )
            with pytest.raises(SnmpTimeoutError):
                collector.collect("10.0.0.1")
        finally:
            collect_module.CredentialSession = original

        assert len(state) == 0
