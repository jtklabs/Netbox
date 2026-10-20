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

## October 20 retest

[`results-2026-10-20.json`](results-2026-10-20.json) records a live retest of
worker **0.2.3**, pinned to commit
`602f9325fde730f1695a2c27c860006f39272783`. The standby completed the upgrade
after an operator retry of a failed UCS save. The active member's subsequent
preflight rejected `Changes Pending`, so no failover or second-member upgrade
was performed. The rolling HA sequence remains unverified.

This run used per-device CLI commands with explicitly selected lab targets. It
did not validate automatic pair selection, queue ordering, or the UI. The exact
target installation already existed on the inactive volume, so fresh software
installation was not exercised. The report includes readiness timings, HTTP
probe failures and final recovery verification. Later worker releases are not
covered by this evidence.

## October 20 guarded handoff retest

[`results-2026-10-20-confirmation.json`](results-2026-10-20-confirmation.json)
records the worker **0.2.7** run pinned to
`9e9ff1655862c0ed86e8900f2014d6c2ba1d331d`. The unattended test **did not pass**.
The standby upgraded and passed two clean health checks. The original active
member then passed the guarded pending-sync exception, failed traffic over to
the upgraded peer, verified ownership, and rebooted only after a fresh standby,
peer-health and configuration-revision check. Both UCS downloads and both
configuration copies were verified.

During the second member's startup, its iControl REST child received a shutdown
signal and remained absent for over 20 minutes. Login returned HTTP 400 because
`/tm/auth/source` was unavailable. The worker kept retrying; its configured
reconnect timeout had not expired. After recording the failure, the operator
restarted only `restjavad` on the verified standby. This manual recovery is part
of the test result and must not be omitted when describing it.

After API recovery, the existing job resumed, passed both health comparisons,
performed normal configuration sync, and required two clean pair health/sync
checks. An independent final collection confirmed both members on
**17.5.1.10 / 0.0.35 / HD1.1**, with **20 WAF policies each**, ASM ready, no
baseline errors, and **In Sync**. Lab02 remained active; lab01 remained standby.
There was no manual failover, manual sync, validation override, or rollback in
this attempt. All **2,742 sampled HTTP requests** through the two shared VIPs
succeeded; this does not establish uninterrupted or long-lived connections.

The source remained unchanged across both jobs. These were two explicit CLI
jobs on the same poller, using the existing target installations. Queue/UI
ordering and fresh image installation were not tested. Both WAF request-log
gates skipped because each upgraded member was standby at its final check.
The remaining blocker is unattended recovery of the appliance's REST backend;
the shutdown trigger has not been established. The successful post-recovery
gates do not turn this into an unattended pass.
