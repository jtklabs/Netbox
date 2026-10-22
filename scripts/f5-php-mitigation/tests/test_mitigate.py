import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import mitigate as job


def config():
    return job.Config("root", "private-password", None, None, "/unused/known_hosts",
                      1, 10, "https://hooks.example.test/secret-path", "private-token",
                      True, 1, 3)


class InventoryTests(unittest.TestCase):
    def load(self, content, *options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "devices.csv"
            path.write_text(content)
            return job.load_devices(job.parser().parse_args(["--csv", str(path), *options]))

    def test_csv_optional_fields_and_override(self):
        devices = self.load("host,name,port,httpd_sam\n192.0.2.1,one,,false\n2001:db8::1,two,2222,true\n", "--httpd-sam")
        self.assertEqual(devices[0], job.Device("192.0.2.1", "one", 22, False))
        self.assertTrue(devices[1].httpd_sam)
        self.assertEqual(devices[1].port, 2222)

    def test_minimal_csv_uses_defaults(self):
        self.assertEqual(self.load("host\n192.0.2.1\n", "--port", "2222", "--httpd-sam"),
                         [job.Device("192.0.2.1", "", 2222, True)])

    def test_auto_csv_overrides_required_default(self):
        devices = self.load("host,httpd_sam\n192.0.2.1,auto\n192.0.2.2,\n", "--require-httpd-sam")
        self.assertFalse(devices[0].httpd_sam)
        self.assertTrue(devices[1].httpd_sam)

    def test_rejects_bad_rows_and_duplicates(self):
        bad = ["host\n", "ip\n192.0.2.1\n", "host\n192.0.2.1\n192.0.2.1\n",
               "host\n192.0.2.1;id\n", "host,port\n192.0.2.1,0\n",
               "host,httpd_sam\n192.0.2.1,maybe\n", "host,host\n192.0.2.1,192.0.2.2\n",
               "host,name\n192.0.2.1\n", "host\n192.0.2.1,extra\n"]
        for content in bad:
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.load(content)

    def test_single_host_and_default_check(self):
        args = job.parser().parse_args(["--host", "192.0.2.9"])
        self.assertFalse(args.apply)
        self.assertEqual(job.load_devices(args), [job.Device("192.0.2.9")])


class ConfigTests(unittest.TestCase):
    def test_literal_password_relative_paths_and_no_environment_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "known_hosts").write_text("")
            path = root / ".env"
            path.write_text("F5_USERNAME=root\nF5_PASSWORD='x${HOME}#y'\n"
                            "F5_KNOWN_HOSTS=known_hosts\nWEBHOOK_URL=https://hooks.example.test/path\n"
                            "WEBHOOK_BEARER_TOKEN='token${LITERAL}'\nF5_WORKERS=4\n")
            with patch.dict(os.environ, {"F5_PASSWORD": "do-not-use", "WEBHOOK_URL": "https://wrong.example.test",
                                         "WEBHOOK_BEARER_TOKEN": "wrong-token", "F5_WORKERS": "9"}):
                loaded = job.load_config(path)
            self.assertEqual(loaded.password, "x${HOME}#y")
            self.assertEqual(loaded.known_hosts, str((root / "known_hosts").resolve()))
            self.assertEqual(loaded.webhook_url, "https://hooks.example.test/path")
            self.assertEqual(loaded.webhook_token, "token${LITERAL}")
            self.assertEqual(loaded.workers, 4)
            with patch.object(job.requests, "Session") as factory:
                response = factory.return_value.post.return_value.__enter__.return_value
                response.status_code = 204
                job.notify(loaded, {"event_id": "env-test"})
                call = factory.return_value.post.call_args
                self.assertEqual(call.args[0], loaded.webhook_url)
                self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer token${LITERAL}")

    def test_worker_count_bounds(self):
        for value in ("0", "33", "-1", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                job.worker_count(value)
        self.assertEqual(job.worker_count("1"), 1)
        self.assertEqual(job.worker_count("32"), 32)


class ProtocolTests(unittest.TestCase):
    def test_success_requires_complete_marker(self):
        output = "F5JOB\tstatus\tapplied\n"
        self.assertEqual(job.parse_result(0, output, "apply")["status"], "unknown")
        output += "F5JOB\tstage\tcomplete\nF5JOB\tchanged\ttrue\n"
        result = job.parse_result(0, output, "apply")
        self.assertEqual(result["status"], "applied")
        self.assertTrue(result["changed"])
        self.assertEqual(job.parse_result(1, output, "apply")["status"], "unknown")

    def test_drops_raw_output_and_unsafe_protocol_values(self):
        result = job.parse_result(1, "a secret\nF5JOB\terror\ttoken=secret\nF5JOB\tstatus\tfailed\n", "apply")
        self.assertNotIn("secret", json.dumps(result))

    def test_failure_contains_rollback_and_stage(self):
        result = job.parse_result(1, "F5JOB\tstatus\tfailed_rolled_back\nF5JOB\tfailed_stage\trestart\n", "apply")
        self.assertEqual(result["status"], "failed_rolled_back")
        self.assertEqual(result["failed_stage"], "restart")

    def test_sam_discovery_protocol_fields(self):
        output = "F5JOB\tstatus\tneeds_change\nF5JOB\thttpd_sam_state\tabsent\nF5JOB\thttpd_sam_decision\tskip\n"
        result = job.parse_result(0, output, "check")
        self.assertEqual(result["httpd_sam_state"], "absent")
        self.assertEqual(result["httpd_sam_decision"], "skip")
        invalid = output + "F5JOB\thttpd_sam_state\ttoken=secret\nF5JOB\thttpd_sam_decision\ttoken=secret\n"
        self.assertEqual(job.parse_result(0, invalid, "check"), result)

    def test_script_and_ini_valid(self):
        template, content = job.load_assets()
        script = job.remote_script(template, content, "apply", True)
        result = subprocess.run(["/bin/bash", "-n"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("@@", script)


class ChannelTests(unittest.TestCase):
    def test_drains_stderr_alongside_stdout(self):
        channel = MagicMock()
        channel.recv_ready.side_effect = [True, False]
        channel.recv_stderr_ready.side_effect = [True, False]
        channel.recv.return_value = b"result"
        channel.recv_stderr.return_value = b"private remote error"
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        self.assertEqual(job.read_channel(channel, 1), (0, "result"))

    def test_timeout_after_dispatch_is_unknown(self):
        with patch.object(job.paramiko, "SSHClient") as constructor, patch.object(job, "read_channel", side_effect=TimeoutError):
            result = job.execute_device(job.Device("192.0.2.1"), config(), "apply", "exit 0")
            client = constructor.return_value
            self.assertIsInstance(client.set_missing_host_key_policy.call_args.args[0], job.paramiko.RejectPolicy)
            self.assertFalse(client.connect.call_args.kwargs["allow_agent"])
            self.assertEqual(result["status"], "unknown")
            client.close.assert_called_once()

    def test_authentication_failure_is_not_unknown(self):
        with patch.object(job.paramiko, "SSHClient") as constructor:
            constructor.return_value.connect.side_effect = job.paramiko.AuthenticationException("secret")
            result = job.execute_device(job.Device("192.0.2.1"), config(), "apply", "exit 0")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error"], "ssh_authentication_failed")
            self.assertNotIn("secret", json.dumps(result))


class WebhookTests(unittest.TestCase):
    def responses(self, *codes):
        results = []
        for code in codes:
            response = MagicMock()
            response.__enter__.return_value = response
            response.status_code = code
            results.append(response)
        return results

    def test_retries_reuse_id_and_preserve_tls(self):
        with patch.object(job.requests, "Session") as factory, patch.object(job.time, "sleep"):
            session = factory.return_value
            session.post.side_effect = self.responses(503, 429, 204)
            result = job.notify(config(), {"event_id": "fixed-event"})
            self.assertEqual(result["status"], "delivered")
            self.assertEqual(result["attempts"], 3)
            self.assertFalse(session.trust_env)
            for call in session.post.call_args_list:
                self.assertEqual(call.kwargs["headers"]["Idempotency-Key"], "fixed-event")
                self.assertTrue(call.kwargs["verify"])
                self.assertFalse(call.kwargs["allow_redirects"])

    def test_redirect_and_client_error_are_not_retried(self):
        for code in (302, 400, 401):
            with self.subTest(code=code), patch.object(job.requests, "Session") as factory:
                factory.return_value.post.side_effect = self.responses(code)
                self.assertEqual(job.notify(config(), {"event_id": "id"})["status"], "failed")
                factory.return_value.post.assert_called_once()

    def test_timeout_is_redacted_and_retried(self):
        with patch.object(job.requests, "Session") as factory, patch.object(job.time, "sleep"):
            factory.return_value.post.side_effect = job.requests.Timeout("secret URL private-token")
            result = job.notify(config(), {"event_id": "id"})
            self.assertEqual(result["attempts"], 3)
            self.assertNotIn("private-token", json.dumps(result))


class BatchTests(unittest.TestCase):
    def run_batch(self, outcomes, deliveries=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "devices.csv"
            inventory.write_text("host\n192.0.2.1\n192.0.2.2\n")
            with patch.object(job, "load_config", return_value=config()), \
                 patch.object(job, "execute_device", side_effect=outcomes) as execute, \
                 patch.object(job, "notify", side_effect=deliveries,
                              return_value={"status": "delivered", "attempts": 1}) as notify, \
                 contextlib.redirect_stdout(io.StringIO()) as terminal:
                code = job.main(["--csv", str(inventory), "--apply", "--report-dir", str(root / "reports")])
            path = next((root / "reports").glob("*.json"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            return code, json.loads(path.read_text()), terminal.getvalue(), execute.call_count, notify.call_args_list

    def test_failure_still_notifies_and_next_host_runs(self):
        code, report, text, calls, notifications = self.run_batch([
            {"status": "failed", "error": "ssh_authentication_failed"},
            {"status": "applied", "changed": True},
        ])
        self.assertEqual(code, 1)
        self.assertEqual(calls, 2)
        self.assertEqual(len(notifications), 2)
        self.assertEqual(report["unprocessed_count"], 0)
        self.assertIn("APPLY REPORT", text)
        self.assertNotIn("private-password", text + json.dumps(report))
        self.assertNotIn("webhook", notifications[0].args[1])

    def test_webhook_failure_does_not_repeat_change(self):
        code, report, _, calls, notifications = self.run_batch(
            [{"status": "applied"}, {"status": "applied"}],
            [{"status": "failed", "attempts": 3}, {"status": "delivered", "attempts": 1}])
        self.assertEqual(code, 1)
        self.assertEqual(calls, 2)
        self.assertEqual(len(notifications), 2)
        self.assertEqual(report["results"][0]["status"], "applied")

    def test_unknown_stops_batch(self):
        code, report, _, calls, notifications = self.run_batch([{"status": "unknown"}])
        self.assertEqual(code, 1)
        self.assertEqual(calls, 1)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(report["unprocessed_count"], 1)

    def test_success_exit_code(self):
        code, _, _, _, _ = self.run_batch([{"status": "applied"}, {"status": "applied"}])
        self.assertEqual(code, 0)

    def test_unrecovered_restart_failure_stops_batch(self):
        code, report, _, calls, _ = self.run_batch([
            {"status": "failed", "changed": False, "failed_stage": "restart"}])
        self.assertEqual(code, 1)
        self.assertEqual(calls, 1)
        self.assertEqual(report["unprocessed_count"], 1)

    def test_skip_details_in_webhook_and_printed_report(self):
        outcome = {"status": "applied", "httpd_sam_state": "stopped", "httpd_sam_decision": "skip"}
        code, report, text, _, notifications = self.run_batch([outcome, outcome])
        self.assertEqual(code, 0)
        self.assertEqual(notifications[0].args[1]["httpd_sam_decision"], "skip")
        self.assertEqual(report["results"][0]["httpd_sam_state"], "stopped")
        self.assertIn("httpd_sam decision: skip (detected state: stopped)", text)


class ConcurrencyTests(unittest.TestCase):
    def invoke(self, execute, notify, *, workers=2, configured_workers=1, wait_override=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / "devices.csv"
            inventory.write_text("host\n192.0.2.1\n192.0.2.2\n192.0.2.3\n192.0.2.4\n")
            args = ["--csv", str(inventory), "--apply", "--report-dir", str(root / "reports")]
            if workers is not None:
                args += ["--workers", str(workers)]
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(job, "load_config", return_value=replace(config(), workers=configured_workers)))
                execution = stack.enter_context(patch.object(job, "execute_device", side_effect=execute))
                delivery = stack.enter_context(patch.object(job, "notify", side_effect=notify))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                if wait_override:
                    stack.enter_context(patch.object(job, "wait", side_effect=wait_override))
                code = job.main(args)
            report = json.loads(next((root / "reports").glob("*.json")).read_text())
            return code, report, execution.call_count, delivery.call_count

    def test_parallel_execution_delivery_and_complete_report(self):
        execution_barrier = threading.Barrier(2)
        delivery_barrier = threading.Barrier(2)
        mutex = threading.Lock()
        active = 0
        peak = 0
        seen = []

        def execute(device, *_):
            nonlocal active, peak
            with mutex:
                active += 1
                peak = max(peak, active)
                seen.append(device.host)
            execution_barrier.wait(timeout=5)
            with mutex:
                active -= 1
            return {"status": "applied", "changed": True, "stage": "complete"}

        def notify(_config, payload):
            self.assertNotIn("webhook", payload)
            delivery_barrier.wait(timeout=5)
            return {"status": "delivered", "attempts": 1}

        code, report, executions, notifications = self.invoke(execute, notify, configured_workers=4)
        self.assertEqual(code, 0)
        self.assertEqual(peak, 2)
        self.assertEqual(report["workers"], 2)  # CLI overrides the .env setting.
        self.assertEqual(executions, 4)
        self.assertEqual(notifications, 4)
        self.assertEqual(len(set(seen)), 4)
        self.assertEqual(len({r["event_id"] for r in report["results"]}), 4)
        self.assertEqual(report["unprocessed_count"], 0)
        self.assertTrue(all(r["webhook"]["status"] == "delivered" for r in report["results"]))

    def test_uncertain_host_stops_new_work_but_drains_in_flight(self):
        barrier = threading.Barrier(2)
        failure_notified = threading.Event()

        def execute(device, *_):
            barrier.wait(timeout=5)
            if device.host == "192.0.2.1":
                return {"status": "unknown", "changed": None, "error": "ssh_timeout"}
            self.assertTrue(failure_notified.wait(5))
            return {"status": "applied", "changed": True}

        def notify(_config, payload):
            if payload["status"] == "unknown":
                failure_notified.set()
            return {"status": "delivered", "attempts": 1}

        code, report, executions, notifications = self.invoke(execute, notify)
        self.assertEqual(code, 1)
        self.assertEqual(executions, 2)
        self.assertEqual(notifications, 2)
        self.assertEqual(report["unprocessed_count"], 2)
        self.assertEqual({r["status"] for r in report["results"]}, {"unknown", "applied"})

    def test_env_worker_default(self):
        code, report, executions, notifications = self.invoke(
            lambda *_: {"status": "applied"},
            lambda *_: {"status": "delivered", "attempts": 1},
            workers=None, configured_workers=3)
        self.assertEqual(code, 0)
        self.assertEqual(report["workers"], 3)
        self.assertEqual(executions, notifications)
        self.assertEqual(executions, 4)

    def test_interrupt_stops_scheduling_and_finishes_notification(self):
        release = threading.Event()
        original_wait = job.wait
        interrupted = False

        def execute(*_):
            self.assertTrue(release.wait(5))
            return {"status": "applied"}

        def interrupt_once(*args, **kwargs):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                release.set()
                raise KeyboardInterrupt
            return original_wait(*args, **kwargs)

        code, report, executions, notifications = self.invoke(
            execute, lambda *_: {"status": "delivered", "attempts": 1},
            workers=1, wait_override=interrupt_once)
        self.assertEqual(code, 130)
        self.assertTrue(report["interrupted"])
        self.assertEqual(executions, 1)
        self.assertEqual(notifications, 1)
        self.assertEqual(report["unprocessed_count"], 3)


class PayloadExampleTests(unittest.TestCase):
    def test_documented_examples_match_files_and_required_event_fields(self):
        import re
        import uuid
        required = {"event", "schema_version", "event_id", "run_id", "advisory", "mode",
                    "device", "started_at", "finished_at", "duration_seconds", "status", "changed", "stage"}
        blocks = re.findall(r"```json\n(.*?)\n```", (job.HERE / "README.md").read_text(), re.S)
        names = ["webhook-applied.json", "webhook-failed.json"]
        self.assertEqual(len(blocks), len(names))
        for name, block in zip(names, blocks):
            payload = json.loads((job.HERE / "examples" / name).read_text())
            self.assertEqual(payload, json.loads(block))
            self.assertTrue(required <= payload.keys())
            self.assertEqual(payload["event"], "f5.mitigation.device.completed")
            self.assertEqual(payload["schema_version"], 1)
            uuid.UUID(payload["event_id"])
            uuid.UUID(payload["run_id"])
            self.assertNotIn("webhook", payload)
            self.assertTrue({"host", "name", "port", "httpd_sam"} <= payload["device"].keys())


class RemoteWorkflowTests(unittest.TestCase):
    """Run the real Bash orchestration against a temporary simulated appliance."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ini = self.root / "etc/php.ini.d"
        self.ini.mkdir(parents=True)
        (self.root / "var/tmp").mkdir(parents=True)
        (self.root / "var/run").mkdir(parents=True)
        (self.root / "VERSION").write_text("Product: BIG-IP\nVersion: 17.1.3\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.target = self.ini / "K000163821.ini"
        self.template, self.content = job.load_assets()
        # Translate only known fixture paths. No commands address real /etc or /var.
        for prefix in ("/etc/php.ini.d", "/var/tmp", "/var/run", "/VERSION"):
            self.template = self.template.replace(prefix, str(self.root) + prefix)
        stub = '''import base64, hashlib, os, pathlib, shutil, sys
name = pathlib.Path(sys.argv[0]).name
root = pathlib.Path(os.environ["SIM_ROOT"])
target = root / "etc/php.ini.d/K000163821.ini"
if name == "id": print("0")
elif name == "sleep": pass
elif name == "sha256sum":
    p = pathlib.Path(sys.argv[1]); print(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + str(p))
elif name == "base64": sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))
elif name == "install":
    shutil.copyfile(sys.argv[-2], sys.argv[-1]); os.chmod(sys.argv[-1], 0o644)
elif name == "php":
    if os.environ.get("SIM_PHP_FAIL") or not target.is_file(): sys.exit(1)
    text = target.read_text()
    sys.exit(0 if all(s in text for s in ["memory_limit = -1", "post_max_size = 8M", "upload_max_filesize = 7M", "session.upload_progress.enabled = 0"]) else 1)
elif name == "bigstart":
    action = sys.argv[1]
    service = sys.argv[2] if len(sys.argv) > 2 else None
    if action == "status":
        if service is None:
            if os.environ.get("SIM_DISCOVERY_FAIL"): sys.exit(1)
            if os.environ.get("SIM_DISCOVERY_EMPTY"): sys.exit(0)
            print("httpd run (pid 123) 10 seconds")
            state = os.environ.get("SIM_SAM", "absent")
            if state == "running": print("httpd_sam run (pid 456) 10 seconds")
            elif state == "stopped": print("httpd_sam down, normally up")
            elif state == "disabled": print("httpd_sam disabled")
            elif state == "unknown": print("httpd_sam unexpected-state")
            elif state == "duplicate": print("httpd_sam down\\nhttpd_sam run (pid 456) 10 seconds")
            sys.exit(0)
        if os.environ.get("SIM_DOWN"): print(service + " down"); sys.exit(1)
        if service == "httpd_sam" and (root / "sam_restart_failed").exists():
            print("httpd_sam down"); sys.exit(1)
        print(service + " run (pid 123) 10 seconds")
    elif action == "restart":
        with (root / "restarts").open("a") as f: f.write(service + "\\n")
        if service == "httpd_sam" and os.environ.get("SIM_SAM_RESTART_FAIL"):
            (root / "sam_restart_failed").touch(); sys.exit(1)
        marker = root / "restart_failed"
        if os.environ.get("SIM_RESTART_FAIL_ONCE") and not marker.exists(): marker.touch(); sys.exit(1)
        if os.environ.get("SIM_RESTART_FAIL_ALWAYS"): sys.exit(1)
'''
        for command in ("id", "sleep", "sha256sum", "base64", "install", "php", "bigstart"):
            path = self.bin / command
            path.write_text(f"#!{sys.executable}\n" + stub)
            path.chmod(0o700)

    def run_remote(self, mode="apply", sam=False, **environment):
        script = job.remote_script(self.template, self.content, mode, sam)
        result = subprocess.run(["/bin/bash", "-s"], input=script, text=True,
                                capture_output=True, timeout=15,
                                env={**os.environ, "PATH": str(self.bin) + ":/usr/bin:/bin",
                                     "SIM_ROOT": str(self.root), **environment})
        return result

    def test_check_is_read_only(self):
        result = self.run_remote("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("status\tneeds_change", result.stdout)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / "restarts").exists())
        self.assertEqual(list((self.root / "var/tmp").iterdir()), [])
        self.assertEqual(list((self.root / "var/run").iterdir()), [])

    def test_apply_backup_and_both_restarts(self):
        self.target.write_text("old configuration\n")
        result = self.run_remote(sam=True, SIM_SAM="running")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.target.read_bytes(), self.content)
        backup = next((self.root / "var/tmp").glob("*/original.ini"))
        self.assertEqual(backup.read_text(), "old configuration\n")
        self.assertEqual((self.root / "restarts").read_text(), "httpd\nhttpd_sam\n")
        self.assertFalse((self.root / "var/run/K000163821.lock").exists())

    def test_auto_selects_running_sam_without_flag(self):
        result = self.run_remote(SIM_SAM="running")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("httpd_sam_decision\trestart", result.stdout)
        self.assertEqual((self.root / "restarts").read_text(), "httpd\nhttpd_sam\n")

    def test_auto_check_selects_running_sam_without_restarting(self):
        result = self.run_remote("check", SIM_SAM="running")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("httpd_sam_decision\tcheck", result.stdout)
        self.assertIn("services\thttpd httpd_sam", result.stdout)
        self.assertFalse((self.root / "restarts").exists())
        self.assertFalse(self.target.exists())

    def test_auto_skips_stopped_or_absent_sam(self):
        for state in ("stopped", "disabled", "absent"):
            with self.subTest(state=state):
                (self.root / "restarts").unlink(missing_ok=True)
                result = self.run_remote(SIM_SAM=state)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertIn("httpd_sam_decision\tskip", result.stdout)
                self.assertIn("httpd_sam_state\t" + ("stopped" if state == "disabled" else state), result.stdout)
                self.assertEqual((self.root / "restarts").read_text(), "httpd\n")

    def test_required_sam_stopped_or_absent_blocks_all_changes(self):
        for mode in ("apply", "check"):
            for state in ("stopped", "absent"):
                with self.subTest(mode=mode, state=state):
                    result = self.run_remote(mode, sam=True, SIM_SAM=state)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("error\texpected_httpd_sam_not_running", result.stdout)
                    self.assertIn("httpd_sam_decision\tblocked", result.stdout)
                    self.assertFalse(self.target.exists())
                    self.assertFalse((self.root / "restarts").exists())
                    self.assertEqual(list((self.root / "var/tmp").iterdir()), [])

    def test_discovery_failures_are_not_silently_skipped(self):
        for environment in ({"SIM_DISCOVERY_FAIL": "1"}, {"SIM_DISCOVERY_EMPTY": "1"},
                            {"SIM_SAM": "unknown"}, {"SIM_SAM": "duplicate"}):
            with self.subTest(environment=environment):
                result = self.run_remote(**environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("httpd_sam_decision\tblocked", result.stdout)
                self.assertNotIn("httpd_sam_decision\tskip", result.stdout)
                self.assertFalse(self.target.exists())
                self.assertFalse((self.root / "restarts").exists())

    def test_auto_selected_sam_restart_failure_is_not_ignored(self):
        result = self.run_remote(SIM_SAM="running", SIM_SAM_RESTART_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status\tfailed_rollback_incomplete", result.stdout)
        self.assertFalse(self.target.exists())
        self.assertEqual((self.root / "restarts").read_text(), "httpd\nhttpd_sam\nhttpd\nhttpd_sam\n")

    def test_compliant_check_and_repeat_apply(self):
        self.target.write_bytes(self.content)
        self.assertIn("status\tcompliant", self.run_remote("check").stdout)
        result = self.run_remote()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changed\tfalse", result.stdout)
        self.assertEqual(list((self.root / "var/tmp").iterdir()), [])
        self.assertEqual((self.root / "restarts").read_text(), "httpd\n")

    def test_restart_failure_restores_existing_file(self):
        self.target.write_text("old configuration\n")
        result = self.run_remote(SIM_RESTART_FAIL_ONCE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status\tfailed_rolled_back", result.stdout)
        self.assertEqual(self.target.read_text(), "old configuration\n")
        self.assertEqual((self.root / "restarts").read_text(), "httpd\nhttpd\n")

    def test_failed_verification_removes_new_file(self):
        result = self.run_remote(SIM_PHP_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status\tfailed_rolled_back", result.stdout)
        self.assertFalse(self.target.exists())
        self.assertTrue(list((self.root / "var/tmp").glob("*/original_absent")))

    def test_rollback_failure_is_distinct(self):
        result = self.run_remote(SIM_RESTART_FAIL_ALWAYS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status\tfailed_rollback_incomplete", result.stdout)

    def test_existing_lock_stops_write_and_remains(self):
        lock = self.root / "var/run/K000163821.lock"
        lock.mkdir()
        result = self.run_remote()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error\tdevice_locked", result.stdout)
        self.assertTrue(lock.exists())
        self.assertFalse(self.target.exists())

    def test_symlink_refused_and_referent_untouched(self):
        outside = self.root / "outside"
        outside.write_text("preserve me")
        self.target.symlink_to(outside)
        result = self.run_remote()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error\ttarget_is_symlink", result.stdout)
        self.assertEqual(outside.read_text(), "preserve me")

    def test_stopped_service_prevents_writes(self):
        result = self.run_remote(SIM_DOWN="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / "restarts").exists())


if __name__ == "__main__":
    unittest.main()
