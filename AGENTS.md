# Poller releases

- Increment the affected poller's numeric version for every deployable code, template or dependency change: `scripts/nornir-netops/netops/__init__.py` for the upgrade / standards worker, and `scripts/snmp-inventory/snmpinv/__init__.py` for the SNMP scanner. Use at least a patch increment above current main; documentation-only and test-only changes need no bump.
- Run `python3 scripts/check-poller-versions.py --base origin/main` before merging. The Poller versions CI check enforces the same rule. Keep package metadata and CLI/reporting tied to that single version source.

# Repository workflow

- Jason authorizes automatically creating and merging pull requests for completed feature and bug-fix work in `jtklabs/Netbox`. Run the appropriate checks and merge when they pass, without asking for another merge confirmation.
- Keep each change scoped to the requested work and preserve unrelated workspace changes. Follow repository protections and required checks.
- Use the repository's configured Git identity and descriptive branch names. Do not add assistant attribution or co-author trailers.
