"""Persistent, multipath-aware route monitoring. Python standard library only."""

import asyncio
from collections import Counter
from dataclasses import dataclass
import ipaddress
import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path


DEFAULTS = {
    "interval_seconds": 1,
    "max_hops": 30,
    "probes_per_hop": 3,
    "probe_wait_seconds": 0.5,
    "trace_timeout_seconds": 3,
    "simultaneous_probes": 32,
    "learning_samples": 30,
    "confirmation_samples": 3,
    "confirmation_window": 10,
    "multipath_grace_seconds": 300,
    "trace_retention_hours": 24,
    "event_retention_days": 90,
}


def load_config(path):
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or set(data) - {"settings", "destinations"}:
        raise ValueError("config must contain settings and destinations only")
    settings = data.get("settings", {})
    if not isinstance(settings, dict) or set(settings) - DEFAULTS.keys():
        raise ValueError("unknown settings")
    settings = DEFAULTS | settings
    limits = {
        "interval_seconds": (1, 60), "max_hops": (1, 64),
        "probes_per_hop": (1, 5), "probe_wait_seconds": (0.05, 5),
        "trace_timeout_seconds": (0.1, 30), "simultaneous_probes": (1, 128),
        "learning_samples": (1, 3600), "confirmation_samples": (2, 100),
        "confirmation_window": (2, 100), "multipath_grace_seconds": (1, 86400),
        "trace_retention_hours": (1, 8760), "event_retention_days": (1, 3650),
    }
    integers = set(limits) - {
        "interval_seconds", "probe_wait_seconds", "trace_timeout_seconds",
        "multipath_grace_seconds", "trace_retention_hours", "event_retention_days",
    }
    for key, (low, high) in limits.items():
        value = settings[key]
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not low <= value <= high or (key in integers and type(value) is not int)):
            raise ValueError(f"{key} must be {'an integer' if key in integers else 'a number'} in {low}..{high}")
    if settings["confirmation_samples"] > settings["confirmation_window"]:
        raise ValueError("confirmation_samples cannot exceed confirmation_window")
    destinations = data.get("destinations")
    if not isinstance(destinations, list) or not 1 <= len(destinations) <= 64:
        raise ValueError("configure 1..64 destinations")
    normalized = []
    for item in destinations:
        if not isinstance(item, dict) or set(item) - {"address", "name", "expected_hops", "protocol", "port"}:
            raise ValueError("destination fields: address, name, expected_hops, protocol, port")
        address = str(ipaddress.ip_address(item["address"]))
        if "%" in address or ipaddress.ip_address(address).is_multicast or ipaddress.ip_address(address).is_unspecified:
            raise ValueError("destinations must be unicast IP literals without a scope suffix")
        name = item.get("name", address)
        if not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise ValueError("destination name must contain 1..120 characters")
        protocol = item.get("protocol", "icmp")
        if not isinstance(protocol, str) or protocol not in {"icmp", "tcp"}:
            raise ValueError("destination protocol must be icmp or tcp")
        options = {"protocol": protocol}
        if protocol == "tcp":
            port = item.get("port", 443)
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("TCP destination port must be an integer in 1..65535")
            options["port"] = port
        elif "port" in item:
            raise ValueError("destination port requires protocol tcp")
        expected = item.get("expected_hops", {})
        if not isinstance(expected, dict):
            raise ValueError("expected_hops must map TTL numbers to address lists")
        clean = {}
        for ttl, addresses in expected.items():
            if not str(ttl).isdigit() or not 1 <= int(ttl) <= settings["max_hops"]:
                raise ValueError("expected hop TTL outside max_hops")
            if not isinstance(addresses, list) or not addresses:
                raise ValueError("expected hop addresses must be nonempty lists")
            clean[str(int(ttl))] = sorted({str(ipaddress.ip_address(a)) for a in addresses})
            if any(ipaddress.ip_address(a).version != ipaddress.ip_address(address).version or "%" in a for a in clean[str(int(ttl))]):
                raise ValueError("expected hop addresses must match the destination IP family")
        normalized.append({"address": address, "name": name, "expected_hops": clean, **options})
    if len({d["address"] for d in normalized}) != len(normalized):
        raise ValueError("duplicate destination address")
    return {"settings": settings, "destinations": normalized}


@dataclass
class Observation:
    started: float
    duration_ms: float
    hops: list
    reached: bool
    error: str | None = None
    protocol: str = "icmp"
    port: int | None = None

    @property
    def path(self):
        return {str(h["ttl"]): h["addresses"] for h in self.hops if h["addresses"]}

    def as_dict(self):
        return self.__dict__.copy()


def parse_trace(output, address, started, duration_ms, error=None):
    """Parse Linux traceroute numeric output, including repeated-address shorthand."""
    hops = []
    for line in output.splitlines():
        match = re.match(r"^\s*(\d+)\s+(.+)$", line)
        if not match:
            continue
        ttl = int(match[1])
        if not 1 <= ttl <= 255:
            continue
        addresses, rtts, annotations = set(), [], []
        tokens = match[2].split()
        for i, token in enumerate(tokens):
            try:
                # ip_address(int) would accept RTTs; only dotted/colon literals here.
                if "." in token or ":" in token:
                    addresses.add(str(ipaddress.ip_address(token)))
                    continue
            except ValueError:
                pass
            if token.startswith("!"):
                annotations.append(token)
            if i + 1 < len(tokens) and tokens[i + 1] == "ms":
                try:
                    rtts.append(float(token))
                except ValueError:
                    pass
        hops.append({"ttl": ttl, "addresses": sorted(addresses), "rtt_ms": rtts,
                     "timeouts": tokens.count("*"), "annotations": annotations})
    # ICMP unreachable replies from the destination do not establish reachability.
    reached = any(address in h["addresses"] and not h["annotations"] for h in hops)
    if not hops and not error:
        error = "traceroute produced no hop records"
    return Observation(started, duration_ms, hops, reached, error)


def trace_command(address, settings):
    method = ["-T", "-p", str(settings.get("port", 443))] if settings.get("protocol", "icmp") == "tcp" else ["-I"]
    return ["traceroute", "-4" if ipaddress.ip_address(address).version == 4 else "-6",
            *method, "-n", "-q", str(settings["probes_per_hop"]),
            "-m", str(settings["max_hops"]), "-N", str(settings["simultaneous_probes"]),
            "-w", str(settings["probe_wait_seconds"]), address]


async def probe(address, settings):
    started, tick = time.time(), time.monotonic()
    proc = None
    output, error = b"", None
    try:
        proc = await asyncio.create_subprocess_exec(
            *trace_command(address, settings), stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env={"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
        # Shield communicate so timeout/cancellation can kill and reap the subprocess.
        communicate = asyncio.create_task(proc.communicate())
        try:
            output, stderr = await asyncio.wait_for(asyncio.shield(communicate), settings["trace_timeout_seconds"])
            if proc.returncode:
                error = f"traceroute exit {proc.returncode}: {stderr.decode(errors='replace').strip()[:300]}"
        except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
            if proc.returncode is None:
                proc.kill()
            output, _ = await communicate
            if isinstance(exc, asyncio.CancelledError):
                raise
            error = "traceroute exceeded trace_timeout_seconds"
    except OSError as exc:
        error = str(exc)
    result = parse_trace(output.decode(errors="replace"), address, started,
                         round((time.monotonic() - tick) * 1000, 3), error)
    result.protocol = settings.get("protocol", "icmp")
    result.port = settings.get("port", 443) if result.protocol == "tcp" else None
    return result


class Detector:
    """Accepted address sets per TTL; fresh evidence is required for additions."""

    def __init__(self, settings, expected=None, state=None):
        self.settings = settings
        self.expected = expected or {}
        self.state = state or {"samples": 0, "accepted": {}, "recent": [], "end_ttl": None,
                               "reachability": None, "last_started": None}
        self.state["recent"] = self.state["recent"][-settings["confirmation_window"]:]
        if "positions" not in self.state:
            # Upgrade existing databases from the latest positive observations.
            self.state["positions"] = {}
            for sample in reversed(self.state["recent"]):
                for ip, ttls in self.positions(sample["path"]).items():
                    self.state["positions"].setdefault(ip, {"ttls": ttls, "confirmed_at": sample["started"]})

    @staticmethod
    def positions(path):
        positions = {}
        for ttl, addresses in path.items():
            for ip in addresses:
                positions.setdefault(ip, []).append(int(ttl))
        return {ip: sorted(ttls) for ip, ttls in positions.items()}

    def moves(self, path, learning, now):
        moves = []
        positions = self.state["positions"]
        recent_positions = [(r["started"], self.positions(r["path"])) for r in self.state["recent"]]
        for ip, ttls in self.positions(path).items():
            previous = positions.get(ip)
            if previous and previous["ttls"] == ttls:
                continue
            # A missing reply at one of several positions does not prove a move.
            if previous and not set(ttls) - set(previous["ttls"]):
                continue
            evidence = [started for started, observed in recent_positions
                        if (not previous or started > previous["confirmed_at"])
                        and observed.get(ip) == ttls]
            if learning or len(evidence) >= self.settings["confirmation_samples"]:
                positions[ip] = {"ttls": ttls, "confirmed_at": now}
                if previous and not learning:
                    moves.append({"address": ip, "from_hops": previous["ttls"],
                                  "to_hops": ttls, "first_observed_at": min(evidence)})
        return moves

    def snapshot(self):
        return {"hops": {ttl: sorted(ips) for ttl, ips in self.state["accepted"].items()},
                "destination_ttl": self.state["end_ttl"]}

    def observe(self, observation, destination):
        s, cfg = self.state, self.settings
        now = observation.started
        events = []
        # After a long collection gap, old evidence must not confirm new changes.
        if s["last_started"] is not None and now - s["last_started"] > cfg["interval_seconds"] * cfg["confirmation_window"]:
            s["recent"] = []
        s["last_started"] = now
        usable = not observation.error
        path = observation.path if usable else {}
        end = next((int(ttl) for ttl, ips in path.items() if destination in ips), None) if observation.reached and usable else None
        recent = s["recent"]
        recent.append({"path": path, "end": end, "reached": observation.reached if usable else None, "started": now})
        del recent[:-cfg["confirmation_window"]]
        # Execution failures have no bearing on topology or reachability.
        if not usable:
            return events
        status = "reached" if observation.reached else "no_reply"
        confirmations = recent[-cfg["confirmation_samples"]:]
        if s["reachability"] != status and len(confirmations) == cfg["confirmation_samples"] and all(r["reached"] == observation.reached for r in confirmations):
            responding = [h for h in observation.hops if h["addresses"]]
            context = s.get("reply_loss_context", {}) if status == "reached" else {}
            last_reached = context.get("last_reached_observation", s.get("last_reached_observation"))
            events.append({"kind": "reachability", "before": s["reachability"], "after": status,
                           "first_observed_at": confirmations[0]["started"],
                           "last_reached_observation": last_reached,
                           "no_reply_since": context.get("first_observed_at"),
                           "last_responding_hop": max(responding, key=lambda h: h["ttl"]) if responding else None,
                           "accepted_path": self.snapshot()})
            if status == "no_reply":
                s["reply_loss_context"] = {"last_reached_observation": last_reached,
                                          "first_observed_at": confirmations[0]["started"]}
            else:
                s.pop("reply_loss_context", None)
            s["reachability"] = status
        if observation.reached:
            # Retain an actual successful trace independently of raw retention.
            s["last_reached_observation"] = observation.as_dict()
        if not path:
            return events
        before = self.snapshot()
        learning = s["samples"] < cfg["learning_samples"]
        moves = self.moves(path, learning, now)
        s["samples"] += 1
        accepted = s["accepted"]
        for ttl, ips in self.expected.items():
            for ip in ips:
                accepted.setdefault(ttl, {}).setdefault(ip, now)
        for ttl, ips in path.items():
            for ip in ips:
                known = accepted.setdefault(ttl, {})
                hits = sum(ip in r["path"].get(ttl, []) for r in recent)
                if learning or ip in known or hits >= cfg["confirmation_samples"]:
                    known[ip] = now
        if learning:
            if end is not None:
                s["end_ttl"] = end
            if s["samples"] == cfg["learning_samples"]:
                events.append({"kind": "baseline", "before": None, "after": self.snapshot()})
            return events
        # Age an address only when this TTL has positively observed alternatives.
        for ttl, known in list(accepted.items()):
            if not path.get(ttl):
                continue
            confirmed_alternative = any(ip in known and sum(ip in r["path"].get(ttl, []) for r in recent) >= cfg["confirmation_samples"] for ip in path[ttl])
            if confirmed_alternative:
                for ip, seen in list(known.items()):
                    if ip not in self.expected.get(ttl, []) and now - seen >= cfg["multipath_grace_seconds"]:
                        del known[ip]
        if end is not None and sum(r["end"] == end for r in recent) >= cfg["confirmation_samples"]:
            s["end_ttl"] = end
            for ttl in list(accepted):
                if int(ttl) > end and ttl not in self.expected:
                    del accepted[ttl]
        # Do not expose empty TTL entries created by unconfirmed candidates.
        s["accepted"] = {ttl: ips for ttl, ips in accepted.items() if ips}
        active_addresses = {ip for ips in s["accepted"].values() for ip in ips}
        s["positions"] = {ip: position for ip, position in s["positions"].items() if ip in active_addresses}
        after = self.snapshot()
        if before != after or moves:
            events.append({"kind": "path_change", "before": before, "after": after,
                           "hop_moves": moves})
        return events


class Store:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS states (target TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS traces (
                id INTEGER PRIMARY KEY, target TEXT NOT NULL, started REAL NOT NULL,
                data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS traces_target_time ON traces(target, started DESC, id DESC);
            CREATE INDEX IF NOT EXISTS traces_target_id ON traces(target, id DESC);
            CREATE INDEX IF NOT EXISTS traces_time ON traces(started);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, target TEXT NOT NULL, started REAL NOT NULL,
                kind TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_target_time ON events(target, started DESC, id DESC);
            CREATE INDEX IF NOT EXISTS events_target_id ON events(target, id DESC);
            CREATE INDEX IF NOT EXISTS events_time ON events(started);
        """)

    def state(self, target):
        with self.lock:
            row = self.db.execute("SELECT state FROM states WHERE target=?", (target,)).fetchone()
            return json.loads(row[0]) if row else None

    def record(self, target, observation, detector):
        with self.lock, self.db:
            events = detector.observe(observation, target)
            self.db.execute("INSERT INTO traces(target,started,data) VALUES(?,?,?)",
                            (target, observation.started, json.dumps(observation.as_dict())))
            for event in events:
                event |= {"observed": observation.as_dict(), "evidence_window": detector.state["recent"],
                          "detected_at": time.time(), "policy": detector.settings,
                          "expected_hops": detector.expected}
                self.db.execute("INSERT INTO events(target,started,kind,data) VALUES(?,?,?,?)",
                                (target, observation.started, event["kind"], json.dumps(event)))
            self.db.execute("INSERT INTO states VALUES(?,?) ON CONFLICT(target) DO UPDATE SET state=excluded.state",
                            (target, json.dumps(detector.state)))

    def rows(self, table, target=None, since=0, until=1e20, limit=100, before_id=None):
        if table not in {"traces", "events"}:
            raise ValueError("invalid table")
        where, params = ["1=1"], []
        if since > 0:
            where.append("started>=?")
            params.append(since)
        if until < 1e20:
            where.append("started<=?")
            params.append(until)
        if target:
            where.append("target=?")
            params.append(target)
        if before_id is not None:
            where.append("id<?")
            params.append(before_id)
        with self.lock:
            rows = self.db.execute(f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", params + [limit]).fetchall()
            return [{"id": r["id"], "target": r["target"], "started": r["started"], **json.loads(r["data"])} for r in rows]

    def prune(self, settings, now=None):
        now = time.time() if now is None else now
        # Bounded batches keep one-second collection responsive; runs every minute.
        with self.lock, self.db:
            for table, cutoff in (("traces", now - settings["trace_retention_hours"] * 3600),
                                  ("events", now - settings["event_retention_days"] * 86400)):
                self.db.execute(f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE started<? LIMIT 10000)", (cutoff,))

    def close(self):
        with self.lock:
            self.db.close()


class Collector:
    def __init__(self, config, store, probe_fn=probe):
        self.config, self.store, self.probe_fn = config, store, probe_fn
        self.stop = threading.Event()
        self.error = None
        self.missed = Counter()
        self.started = time.time()

    async def target_loop(self, target):
        """Start each second; bounded overlapping probes are consumed in start order."""
        address, settings = target["address"], self.config["settings"]
        protocol = target.get("protocol", "icmp")
        port = target.get("port", 443) if protocol == "tcp" else None
        probe_settings = settings | {"protocol": protocol, "port": port}
        detector = Detector(settings, target["expected_hops"], self.store.state(address))
        interval = settings["interval_seconds"]
        capacity = math.ceil(settings["trace_timeout_seconds"] / interval) + 2
        queue = asyncio.Queue(capacity)
        pending = set()

        async def consume():
            while True:
                task = await queue.get()
                result = await task
                result.protocol, result.port = protocol, port
                self.store.record(address, result, detector)
                queue.task_done()

        consumer = asyncio.create_task(consume())
        deadline = time.monotonic()
        try:
            while not self.stop.is_set():
                if consumer.done():
                    await consumer
                if queue.full() or len(pending) >= capacity:
                    self.missed[address] += 1
                else:
                    task = asyncio.create_task(self.probe_fn(address, probe_settings))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
                    queue.put_nowait(task)
                deadline += interval
                now = time.monotonic()
                if deadline < now:
                    skipped = math.floor((now - deadline) / interval) + 1
                    self.missed[address] += skipped
                    deadline += skipped * interval
                await asyncio.sleep(max(0, deadline - time.monotonic()))
        finally:
            consumer.cancel()
            for task in pending:
                task.cancel()
            await asyncio.gather(consumer, *pending, return_exceptions=True)

    async def run(self):
        async def cleanup():
            while not self.stop.is_set():
                self.store.prune(self.config["settings"])
                await asyncio.sleep(60)

        tasks = [asyncio.create_task(self.target_loop(t)) for t in self.config["destinations"]]
        tasks.append(asyncio.create_task(cleanup()))
        try:
            while not self.stop.is_set():
                for task in tasks:
                    if task.done():
                        await task
                await asyncio.sleep(0.1)
        except Exception as exc:
            self.error = str(exc)
            raise
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def snapshot(self):
        targets = []
        for target in self.config["destinations"]:
            address = target["address"]
            state = self.store.state(address)
            rows = self.store.rows("traces", address, limit=1)
            latest = rows[0] if rows else None
            stale_after = self.config["settings"]["trace_timeout_seconds"] + 3 * self.config["settings"]["interval_seconds"]
            fresh = bool(latest and time.time() - latest["started"] < stale_after)
            targets.append({**target, "latest": latest, "state": state,
                            "fresh": fresh, "missed_ticks": self.missed[address]})
        return {"targets": targets, "settings": self.config["settings"],
                "collector_error": self.error, "started": self.started,
                "now": time.time(), "healthy": not self.error and all(t["fresh"] and not t["latest"]["error"] for t in targets)}
