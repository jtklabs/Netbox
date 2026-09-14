# Running and maintaining the tests

From `scripts/nornir-netops`, run the complete suite with:

```bash
python -m pytest -q
```

To measure where time goes:

```bash
python -m pytest -q --durations=20
```

Use the same environment and several runs when comparing total times. Profiling
adds overhead; compare ordinary pytest runs for the final timing.

## Where coverage lives

Parser, planner and template cases live in the feature's unit test module. CLI
scenarios exercise the actual inventory, argument parsing, planning, reporting
and verification against simulated devices.

| Behavior | Test modules |
| --- | --- |
| Shared CLI behavior, SSH apply/save, selftest | `test_run.py` |
| Archive lifecycle, action provenance, checkpoints, interruption, output paths | `test_archive.py` |
| Switch syslog policy, audit, failures, archived source/destination restoration | `test_syslog_netbox.py` |
| F5 WAF policy, failures, original configuration and archived REST restoration | `test_waf.py` |
| F5 system syslog policy and archived REST restoration | `test_f5_syslog.py` |
| F5 banner apply, verification, idempotence and archived restoration | `test_f5_banner.py` |
| F5 SNMP configuration, redaction and incomplete restoration of old secrets | `test_f5_snmp.py` |
| Poller ownership, prefix membership, scanner parity, CLI selection and archives | `test_poller.py` |
| TLS startup and import order in fresh Python processes | `test_entrypoint.py` |

For example, while changing syslog:

```bash
python -m pytest tests/test_syslog.py tests/test_syslog_netbox.py tests/test_f5_syslog.py -q
```

Run the complete suite before merging. Archive assertions also live in the
feature scenarios above, so `test_archive.py` alone does not cover every archive.

## Keeping the suite efficient

Add assertions to an existing scenario when the same CLI invocation already
produces the required state. For example, assert saved configuration in the
per-platform apply test, and exercise a report's restoration steps after the
existing apply/idempotence checks. Keep independent failure cases and input
matrices parameterized so pytest identifies each failing case separately.

`conftest.py` reuses installed Nornir entry-point metadata for the test session.
Each `InitNornir` call still loads and registers the real plugins and creates
fresh inventory and runner objects. Only Nornir's metadata lookup is cached;
other libraries and fresh subprocesses use normal package discovery. This
assumes the installed package set is fixed during a test session.

Every test gets a fresh environment snapshot, working directory, log/report
paths and platform cache. State directories use the already-unique `tmp_path`
name under one session directory, avoiding a repeated scan for the next numbered
state directory. They stay outside the test's working directory so atomic-write
tests can inspect that directory's exact contents.

Archive serialization, atomic replacement, file permissions and filesystem
syncs are exercised by the suite. Device simulators retain their state within a
test so readback and idempotence checks can detect incorrect changes.
