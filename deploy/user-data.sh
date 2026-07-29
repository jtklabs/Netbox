#!/bin/bash
# EC2 user-data for the monthly AMI redeploy: run the idempotent bootstrap and
# keep a log on the instance. The AMI bakes the repo at /opt/netbox and docker.
exec /opt/netbox/deploy/bootstrap.sh >>/var/log/netbox-bootstrap.log 2>&1
