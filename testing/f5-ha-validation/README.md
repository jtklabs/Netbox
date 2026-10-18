# F5 HA validation evidence

`results-2026-10-15.json` is the historical outcome of the October 15 lab run
against the recorded source commit and file hashes. It is not a runnable test,
a deployment configuration, or a statement about the current code or devices.
Management and VIP addresses are documentation placeholders, and private
artifact locations are redacted. The job links are illustrative, not live URLs.

The unattended pair upgrade **failed**. The standby reached the target version
with a manual REST-service restart; the second member stopped at the pending
configuration-sync check. Both devices were then recovered to the original
version and verified healthy and in sync. A separate manual failover/failback
passed. Those recovery results do not turn the automated upgrade into a pass.

The report retains the limitations, interventions, observations and engineering
follow-up from that run. Its follow-up list describes findings at the recorded
revision; it does not assert that later releases still have those limitations.
