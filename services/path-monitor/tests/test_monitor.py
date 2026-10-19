import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from monitor import Collector, DEFAULTS, Detector, Observation, Store, load_config, parse_trace, probe, trace_command

DEST = "192.0.2.80"
A, B, C = "198.51.100.1", "198.51.100.2", "203.0.113.1"


def observation(ips=(A,), now=1, reached=True, error=None, end=3):
    hops = [{"ttl": 1, "addresses": ["192.0.2.1"], "rtt_ms": [1.2], "timeouts": 0, "annotations": []},
            {"ttl": 2, "addresses": list(ips), "rtt_ms": [2.1], "timeouts": 0, "annotations": []}]
    if reached:
        hops.append({"ttl": end, "addresses": [DEST], "rtt_ms": [3.2], "timeouts": 0, "annotations": []})
    return Observation(now, 30, hops, reached, error)


def settings(**kw):
    return DEFAULTS | {"learning_samples": 4, "confirmation_samples": 3, "confirmation_window": 5,
                       "multipath_grace_seconds": 20} | kw


def route(addresses, now, reached=True):
    return Observation(now, 10, [
        {"ttl": ttl, "addresses": [ip] if isinstance(ip, str) else list(ip),
         "rtt_ms": [float(ttl)] if ip else [], "timeouts": 0 if ip else 3, "annotations": []}
        for ttl, ip in enumerate(addresses, 1)], reached)


class ConfigTests(unittest.TestCase):
    def config(self, **overrides):
        data = {"destinations": [{"address": DEST}], **overrides}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "targets.json"
            path.write_text(json.dumps(data))
            return load_config(path)

    def test_normalizes_ipv6_and_expected_hops(self):
        result = self.config(destinations=[{"address": "2001:0db8::1", "expected_hops": {"2": ["2001:db8::2", "2001:0db8::2"]}}])
        self.assertEqual(result["destinations"][0]["address"], "2001:db8::1")
        self.assertEqual(result["destinations"][0]["expected_hops"], {"2": ["2001:db8::2"]})

    def test_rejects_invalid_config(self):
        for data in ({"destinations": []}, {"destinations": [{"address": "example.com"}]},
                     {"destinations": [{"address": "--help"}]}, {"destinations": [{"address": "fe80::1%eth0"}]},
                     {"destinations": [{"address": "224.0.0.1"}]}, {"destinations": [{"address": DEST}, {"address": DEST}]},
                     {"settings": {"interval_seconds": True}}, {"settings": {"probe_wait_seconds": float("nan")}},
                     {"settings": {"max_hops": 2.5}}, {"settings": {"typo": 1}},
                     {"settings": {"confirmation_samples": 11}},
                     {"destinations": [{"address": DEST, "expected_hops": {"2": ["::1"]}}]}):
            with self.subTest(data=data), self.assertRaises((ValueError, KeyError, TypeError)):
                self.config(**data)

    def test_mixes_default_icmp_and_per_destination_tcp(self):
        targets = self.config(destinations=[
            {"address": DEST},
            {"address": "192.0.2.81", "protocol": "tcp"},
            {"address": "2001:db8::80", "protocol": "tcp", "port": 8443},
        ])["destinations"]
        self.assertEqual(targets[0]["protocol"], "icmp")
        self.assertNotIn("port", targets[0])
        self.assertEqual((targets[1]["protocol"], targets[1]["port"]), ("tcp", 443))
        self.assertEqual(targets[2]["port"], 8443)
        for port in (1, 65535):
            self.assertEqual(self.config(destinations=[{"address": DEST, "protocol": "tcp", "port": port}])
                             ["destinations"][0]["port"], port)

    def test_rejects_invalid_protocol_and_port(self):
        for options in ({"protocol": "udp"}, {"protocol": "-T"}, {"protocol": []},
                        {"protocol": None}, {"port": 443}, {"protocol": "icmp", "port": 443},
                        *({"protocol": "tcp", "port": port} for port in
                          (0, -1, 65536, 443.0, True, "443", "443; echo invalid", None))):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.config(destinations=[{"address": DEST, **options}])


class ParserTests(unittest.TestCase):
    def test_multipath_shorthand_rtt_and_timeouts(self):
        result = parse_trace(f"traceroute to {DEST} ({DEST}), 30 hops max\n 1 192.0.2.1 1.2 ms 1.3 ms *\n 2 {A} 3.0 ms {B} 4.0 ms 4.1 ms\n 3 {DEST} 5.0 ms 5.1 ms 5.2 ms\n", DEST, 0, 10)
        self.assertTrue(result.reached)
        self.assertEqual(result.hops[0]["timeouts"], 1)
        self.assertEqual(result.hops[0]["rtt_ms"], [1.2, 1.3])
        self.assertEqual(result.hops[1]["addresses"], [A, B])

    def test_ipv6_stars_and_unreachable(self):
        result = parse_trace("1 2001:db8::1 1.2 ms\n2 * * *\n3 2001:db8::2 2.4 ms !H", "2001:db8::2", 0, 10)
        self.assertFalse(result.reached)
        self.assertEqual(result.hops[1]["addresses"], [])
        self.assertEqual(result.hops[2]["annotations"], ["!H"])

    def test_empty_output_and_command(self):
        self.assertIsNotNone(parse_trace("permission denied", DEST, 0, 0).error)
        command = trace_command(DEST, settings())
        self.assertIn("-I", command)
        self.assertIn("-4", command)
        self.assertEqual(command[-1], DEST)
        self.assertIn("-6", trace_command("::1", settings()))

    def test_tcp_command_keeps_the_configured_port_constant(self):
        for address, family, port in ((DEST, "-4", 443), ("2001:db8::80", "-6", 8443)):
            command = trace_command(address, settings(protocol="tcp", port=port))
            self.assertEqual(command, ["traceroute", family, "-T", "-p", str(port), "-n",
                "-q", "3", "-m", "30", "-N", "32", "-w", "0.5", address])
            self.assertNotIn("-I", command)
        self.assertEqual(trace_command(DEST, settings(protocol="tcp"))[4], "443")
        self.assertNotIn("-p", trace_command(DEST, settings()))


class DetectorTests(unittest.TestCase):
    def learned(self):
        detector = Detector(settings())
        for i, ip in enumerate((A, B, A, B), 1):
            detector.observe(observation([ip], i), DEST)
        return detector

    def changes(self, events):
        return [e for e in events if e["kind"] == "path_change"]

    def test_known_multipath_does_not_flap(self):
        detector = self.learned()
        for i in range(5, 100):
            self.assertFalse(self.changes(detector.observe(observation([A if i % 2 else B], i), DEST)))
        self.assertEqual(detector.snapshot()["hops"]["2"], [A, B])

    def test_new_branch_needs_evidence_then_old_branches_age_out(self):
        detector = self.learned()
        self.assertFalse(self.changes(detector.observe(observation([C], 5), DEST)))
        self.assertFalse(self.changes(detector.observe(observation([C], 6), DEST)))
        event = self.changes(detector.observe(observation([C], 7), DEST))[0]
        self.assertEqual(event["before"]["hops"]["2"], [A, B])
        self.assertEqual(event["after"]["hops"]["2"], [A, B, C])
        for i in range(8, 25):
            events = self.changes(detector.observe(observation([C], i), DEST))
        self.assertEqual(detector.snapshot()["hops"]["2"], [C])
        self.assertTrue(events)

    def test_transient_address_does_not_change_path(self):
        detector = self.learned()
        for i, ip in enumerate((C, A, B, A, B, A), 5):
            self.assertFalse(self.changes(detector.observe(observation([ip], i), DEST)))
        self.assertEqual(detector.snapshot()["hops"]["2"], [A, B])

    def test_errors_and_nonresponding_hop_never_remove_addresses(self):
        detector = self.learned()
        before = detector.snapshot()
        for i in range(5, 45):
            self.assertFalse(self.changes(detector.observe(observation([], i, reached=False), DEST)))
            self.assertFalse(self.changes(detector.observe(observation([C], i + 0.1, error="timeout"), DEST)))
        self.assertEqual(detector.snapshot(), before)

    def test_expected_alternative_does_not_age_out(self):
        detector = Detector(settings(learning_samples=1), {"2": [B]})
        detector.observe(observation([A], 1), DEST)
        for i in range(2, 40):
            self.assertFalse(self.changes(detector.observe(observation([A], i), DEST)))
        self.assertEqual(detector.snapshot()["hops"]["2"], [A, B])

    def test_restart_preserves_learning(self):
        restored = Detector(settings(), state=json.loads(json.dumps(self.learned().state)))
        self.assertFalse(self.changes(restored.observe(observation([A], 5), DEST)))
        self.assertEqual(restored.state["samples"], 5)

    def test_old_evidence_not_reused_after_gap(self):
        detector = self.learned()
        detector.observe(observation([C], 5), DEST)
        detector.observe(observation([C], 6), DEST)
        detector.observe(observation([C], 100), DEST)
        self.assertNotIn(C, detector.snapshot()["hops"]["2"])

    def test_reachability_needs_consecutive_results(self):
        detector = self.learned()
        events = []
        for i in range(5, 15):
            events += detector.observe(observation([A], i, reached=bool(i % 2)), DEST)
        self.assertFalse([e for e in events if e["kind"] == "reachability"])
        events = []
        for i in range(15, 18):
            events += detector.observe(observation([A], i, reached=False), DEST)
        self.assertEqual([e["after"] for e in events if e["kind"] == "reachability"], ["no_reply"])

    def test_confirmed_shorter_route_removes_trailing_hops(self):
        detector = self.learned()
        for i in range(5, 8):
            events = detector.observe(Observation(i, 1, [{"ttl": 1, "addresses": [DEST]}], True), DEST)
        self.assertEqual(detector.snapshot()["destination_ttl"], 1)
        self.assertNotIn("2", detector.snapshot()["hops"])
        self.assertTrue(self.changes(events))

    def test_alternating_destination_ttls_do_not_duplicate_or_flap(self):
        detector = Detector(settings())
        def terminal(ttl, now):
            return Observation(now, 1, [{"ttl": 1, "addresses": [A]},
                                       {"ttl": ttl, "addresses": [DEST]}], True)
        for now in range(1, 5):
            detector.observe(terminal(12, now), DEST)
        for now in range(5, 45):
            self.assertFalse(self.changes(detector.observe(terminal(11 if now % 2 else 12, now), DEST)))
            self.assertEqual(detector.snapshot()["destination_ttl"], 12)
            self.assertEqual([ttl for ttl, ips in detector.snapshot()["hops"].items() if DEST in ips], ["12"])
        # A stable change is still reported, in either direction, after fresh
        # consecutive evidence rather than hits left in the rolling window.
        for ttl, start in ((11, 45), (12, 48)):
            for now in (start, start + 1):
                self.assertFalse(self.changes(detector.observe(terminal(ttl, now), DEST)))
            change = self.changes(detector.observe(terminal(ttl, start + 2), DEST))
            self.assertEqual(len(change), 1)
            self.assertEqual(change[0]["after"]["destination_ttl"], ttl)
            self.assertEqual([hop for hop, ips in change[0]["after"]["hops"].items() if DEST in ips], [str(ttl)])
            self.assertEqual(change[0]["hop_moves"][0]["address"], DEST)
            detector = Detector(settings(), state=json.loads(json.dumps(detector.state)))

    def test_duplicate_terminal_replies_are_ambiguous_not_moves(self):
        detector = Detector(settings())
        for now in range(1, 5):
            detector.observe(route([A, B, DEST], now), DEST)
        before = detector.snapshot()
        for now in range(5, 40):
            self.assertFalse(self.changes(detector.observe(route([A, B, DEST, DEST], now), DEST)))
            self.assertEqual(detector.snapshot(), before)
        self.assertEqual(detector.state["positions"][DEST]["ttls"], [3])

    def test_unreachable_annotation_does_not_compete_with_a_valid_terminal_reply(self):
        detector = Detector(settings())
        for now in range(1, 5):
            detector.observe(route([A, B, DEST], now), DEST)
        for now in range(5, 8):
            trace = route([A, B, DEST, DEST], now)
            trace.hops[2]["annotations"] = ["!H"]
            events = detector.observe(trace, DEST)
        change = self.changes(events)[0]
        self.assertEqual(change["after"]["destination_ttl"], 4)
        self.assertEqual(change["hop_moves"][0]["from_hops"], [3])
        self.assertEqual(change["hop_moves"][0]["to_hops"], [4])

    def test_ambiguous_timeout_and_error_traces_break_terminal_confirmation(self):
        detector = self.learned()
        for now in (5, 6):
            detector.observe(route([A, B, C, DEST], now), DEST)
        detector.observe(route([A, B, DEST, DEST], 7), DEST)
        detector.observe(route([A, B, C, DEST], 8), DEST)
        detector.observe(route([A, B, [], []], 9, reached=False), DEST)
        detector.observe(route([A, B, C, DEST], 10), DEST)
        failed = route([A, B, C, DEST], 11); failed.error = "timeout"
        detector.observe(failed, DEST)
        for now in (12, 13):
            detector.observe(route([A, B, C, DEST], now), DEST)
            self.assertEqual(detector.snapshot()["destination_ttl"], 3)
        detector.observe(route([A, B, C, DEST], 14), DEST)
        self.assertEqual(detector.snapshot()["destination_ttl"], 4)

    def test_legacy_duplicate_destination_state_is_cleaned_without_event(self):
        detector = self.learned()
        detector.state["accepted"]["4"] = {DEST: 4}
        detector.state["positions"][DEST]["ttls"] = [3, 4]
        restored = Detector(settings(), state=json.loads(json.dumps(detector.state)))
        events = restored.observe(observation([A], 5), DEST)
        self.assertFalse(self.changes(events))
        self.assertNotIn("4", restored.snapshot()["hops"])
        self.assertEqual(restored.state["positions"][DEST]["ttls"], [3])

    def test_legacy_ambiguous_evidence_cannot_confirm_a_terminal_move(self):
        detector = self.learned()
        for now in (5, 6):
            detector.state["recent"].append({"path": {"1": [A], "3": [DEST], "4": [DEST]},
                "end": 4, "reached": True, "started": now})
        detector.state["last_started"] = 6
        restored = Detector(settings(), state=json.loads(json.dumps(detector.state)))
        for now in (7, 8):
            restored.observe(route([A, B, [], DEST], now), DEST)
            self.assertEqual(restored.snapshot()["destination_ttl"], 3)
        restored.observe(route([A, B, [], DEST], 9), DEST)
        self.assertEqual(restored.snapshot()["destination_ttl"], 4)

    def test_inserted_hop_reports_familiar_addresses_at_new_positions(self):
        detector = Detector(settings())
        for now in range(1, 5):
            detector.observe(route([A, B, DEST], now), DEST)
        for now in (5, 6):
            self.assertFalse(self.changes(detector.observe(route([C, A, B, DEST], now), DEST)))
        event = self.changes(detector.observe(route([C, A, B, DEST], 7), DEST))[0]
        self.assertEqual(event["hop_moves"], [
            {"address": A, "from_hops": [1], "to_hops": [2], "first_observed_at": 5},
            {"address": B, "from_hops": [2], "to_hops": [3], "first_observed_at": 5},
            {"address": DEST, "from_hops": [3], "to_hops": [4], "first_observed_at": 5}])

    def test_return_to_cached_positions_is_still_a_difference(self):
        detector = Detector(settings())
        for now in range(1, 5):
            detector.observe(route([A, B, DEST], now), DEST)
        for now in range(5, 8):
            events = detector.observe(route([B, A, DEST], now), DEST)
        self.assertEqual(len(self.changes(events)[0]["hop_moves"]), 2)
        # Both addresses are now accepted at both TTLs during the grace period.
        detector = Detector(settings(), state=json.loads(json.dumps(detector.state)))
        for now in (8, 9):
            self.assertFalse(self.changes(detector.observe(route([A, B, DEST], now), DEST)))
        event = self.changes(detector.observe(route([A, B, DEST], 10), DEST))[0]
        self.assertEqual(event["before"], event["after"])
        self.assertEqual(event["hop_moves"][0]["address"], A)
        self.assertEqual(event["hop_moves"][0]["from_hops"], [2])
        self.assertEqual(event["hop_moves"][0]["to_hops"], [1])

    def test_missing_reply_at_one_loop_position_is_not_a_move(self):
        detector = Detector(settings())
        for now in range(1, 5):
            detector.observe(route([A, A, DEST], now), DEST)
        for now in range(5, 10):
            self.assertFalse(self.changes(detector.observe(route([[], A, DEST], now), DEST)))

    def test_upgrade_seeds_positions_from_actual_recent_observations(self):
        old_state = self.learned().state
        del old_state["positions"]
        restored = Detector(settings(), state=old_state)
        self.assertEqual(restored.state["positions"][A]["ttls"], [2])
        self.assertFalse(self.changes(restored.observe(observation([A], 5), DEST)))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.detector = Detector(settings())

    def tearDown(self):
        self.store.close()

    def test_persistence_filter_pagination_and_evidence(self):
        for i in range(1, 9):
            self.store.record(DEST, observation([A if i < 5 else C], i), self.detector)
        self.assertEqual(self.store.state(DEST), self.detector.state)
        change = next(e for e in self.store.rows("events", DEST) if e["kind"] == "path_change")
        self.assertIn("evidence_window", change)
        self.assertEqual(change["observed"]["hops"][1]["addresses"], [C])
        page = self.store.rows("traces", DEST, since=2, until=6, limit=2)
        self.assertEqual([r["started"] for r in page], [6, 5])
        next_page = self.store.rows("traces", DEST, since=2, until=6, limit=2, before_id=page[-1]["id"])
        self.assertEqual([r["started"] for r in next_page], [4, 3])
        self.assertFalse(self.store.rows("traces", "192.0.2.99"))

    def test_ambiguous_terminal_replies_keep_raw_evidence_and_one_accepted_end(self):
        for now in range(1, 5):
            self.store.record(DEST, route([A, B, DEST], now), self.detector)
        old_events = self.store.rows("events", DEST)
        ambiguous = route([A, B, DEST, DEST], 5)
        original = json.loads(json.dumps(ambiguous.as_dict()))
        self.store.record(DEST, ambiguous, self.detector)
        raw = self.store.rows("traces", DEST)[0]
        self.assertEqual(raw["hops"], original["hops"])
        self.assertEqual(ambiguous.as_dict(), original)
        self.assertEqual(self.store.rows("events", DEST), old_events)
        restored = Detector(settings(), state=self.store.state(DEST))
        self.assertEqual([ttl for ttl, ips in restored.snapshot()["hops"].items() if DEST in ips], ["3"])
        self.assertIsNone(restored.state["recent"][-1]["end"])
        self.assertEqual(restored.state["recent"][-1]["terminal_ttls"], [3, 4])
        self.assertEqual(restored.state["recent"][-1]["path"]["4"], [DEST])

    def test_retention_keeps_state_and_longer_event_history(self):
        for i in range(1, 5):
            self.store.record(DEST, observation([A], i), self.detector)
        self.store.prune(settings(trace_retention_hours=1), now=10000)
        self.assertFalse(self.store.rows("traces"))
        self.assertTrue(self.store.rows("events"))
        self.assertIsNotNone(self.store.state(DEST))
        self.store.prune(settings(), now=100000000)
        self.assertFalse(self.store.rows("events"))

    def test_database_survives_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "paths.sqlite3")
            store = Store(path)
            store.record(DEST, observation(), self.detector)
            store.close()
            store = Store(path)
            self.assertEqual(store.state(DEST), self.detector.state)
            self.assertEqual(len(store.rows("traces")), 1)
            store.close()

    def test_loss_and_recovery_keep_pre_loss_trace_after_restart_and_pruning(self):
        for now in range(1, 5):
            self.store.record(DEST, route([A, B, DEST], now), self.detector)
        for now in range(5, 8):
            self.store.record(DEST, route([A, [], []], now, reached=False), self.detector)
        event = self.store.rows("events", DEST)[0]
        self.assertEqual(event["after"], "no_reply")
        self.assertEqual(event["first_observed_at"], 5)
        self.assertEqual(event["last_reached_observation"]["started"], 4)
        self.assertEqual(event["last_responding_hop"]["ttl"], 1)
        self.assertEqual(event["observed"]["hops"][1]["addresses"], [])
        detector = Detector(settings(), state=self.store.state(DEST))
        for now in range(8, 11):
            self.store.record(DEST, route([A, C, DEST], now), detector)
        recovery = next(e for e in self.store.rows("events", DEST) if e["kind"] == "reachability" and e["before"] == "no_reply")
        self.assertEqual(recovery["first_observed_at"], 8)
        self.assertEqual(recovery["no_reply_since"], 5)
        self.assertEqual(recovery["last_reached_observation"]["started"], 4)
        self.assertEqual(recovery["observed"]["hops"][1]["addresses"], [C])
        self.store.prune(settings(trace_retention_hours=1), now=10000)
        self.assertFalse(self.store.rows("traces"))
        self.assertEqual(next(e for e in self.store.rows("events") if e["id"] == event["id"])["last_reached_observation"]["started"], 4)
class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_mixed_methods_are_isolated_and_persisted_with_events(self):
        store = Store(":memory:")
        calls = {}
        async def fake_probe(address, config):
            calls[address] = trace_command(address, config)
            return Observation(time.time(), 5, [{"ttl": 1, "addresses": [address],
                "rtt_ms": [1.5], "timeouts": 0, "annotations": []}], True)
        global_settings = settings(learning_samples=1)
        config = {"settings": global_settings, "destinations": [
            {"address": DEST, "expected_hops": {}},
            {"address": "192.0.2.81", "expected_hops": {}, "protocol": "tcp", "port": 443},
        ]}
        collector = Collector(config, store, fake_probe)
        task = asyncio.create_task(collector.run())
        try:
            async def wait_for_both():
                while len(store.rows("traces")) < 2:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(wait_for_both(), 2)
            self.assertIn("-I", calls[DEST])
            self.assertIn("-T", calls["192.0.2.81"])
            self.assertEqual(calls["192.0.2.81"][4], "443")
            self.assertNotIn("protocol", global_settings)
            icmp = store.rows("traces", DEST)[0]
            tcp = store.rows("traces", "192.0.2.81")[0]
            self.assertEqual((icmp["protocol"], icmp["port"]), ("icmp", None))
            self.assertEqual((tcp["protocol"], tcp["port"]), ("tcp", 443))
            event = store.rows("events", "192.0.2.81")[0]
            self.assertEqual(event["observed"]["protocol"], "tcp")
            self.assertEqual(event["observed"]["port"], 443)
        finally:
            collector.stop.set()
            await task
            store.close()

    async def test_tcp_probe_passes_flags_and_parses_latency(self):
        calls = []
        class Process:
            returncode = 0
            async def communicate(self):
                return f"1 192.0.2.1 1.2 ms\n2 {DEST} 4.5 ms\n".encode(), b""
        async def create(*args, **kwargs):
            calls.append(args)
            return Process()
        with patch("monitor.asyncio.create_subprocess_exec", create):
            result = await probe(DEST, settings(protocol="tcp", port=443))
        self.assertIn("-T", calls[0])
        self.assertEqual(calls[0][4], "443")
        self.assertTrue(result.reached)
        self.assertEqual(result.hops[1]["rtt_ms"], [4.5])
        self.assertEqual((result.protocol, result.port), ("tcp", 443))

    async def test_cadence_overlaps_slow_probes_but_records_in_order(self):
        store = Store(":memory:")
        calls = []
        async def slow_probe(address, config):
            index = len(calls)
            calls.append(time.monotonic())
            await asyncio.sleep(1.4 if index == 0 else 0.01)
            return observation(now=index + 1)
        config = {"settings": settings(), "destinations": [{"address": DEST, "expected_hops": {}}]}
        collector = Collector(config, store, slow_probe)
        task = asyncio.create_task(collector.run())
        await asyncio.sleep(2.2)
        collector.stop.set()
        await task
        self.assertEqual(len(calls), 3)
        self.assertAlmostEqual(calls[1] - calls[0], 1, delta=0.15)
        self.assertEqual([r["started"] for r in store.rows("traces")], [3, 2, 1])
        store.close()

    async def test_timeout_kills_and_reaps_subprocess(self):
        class Process:
            returncode = None
            killed = False
            async def communicate(self):
                while not self.killed:
                    await asyncio.sleep(0.01)
                self.returncode = -9
                return b"1 192.0.2.1 1 ms\n", b""
            def kill(self):
                self.killed = True
        process = Process()
        async def create(*args, **kwargs):
            return process
        with patch("monitor.asyncio.create_subprocess_exec", create):
            result = await probe(DEST, settings(trace_timeout_seconds=0.02))
        self.assertTrue(process.killed)
        self.assertEqual(process.returncode, -9)
        self.assertIn("exceeded", result.error)
        self.assertEqual(result.hops[0]["addresses"], ["192.0.2.1"])


if __name__ == "__main__":
    unittest.main()
