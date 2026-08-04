#!/bin/bash
# EC2 user-data for the monthly AMI redeploy: run the idempotent bootstrap and
# keep a log on the instance. The AMI bakes the repo at /opt/netbox and docker.
# Hand off to systemd rather than running bootstrap directly: one owner for the
# stack, output captured by journald, and no risk of cloud-init and the unit
# both running it. cloud-init's user-data is per-instance anyway, so it would
# not re-run on reboot — the unit is what makes this survive restarts.
exec systemctl start --no-block netbox-compose
