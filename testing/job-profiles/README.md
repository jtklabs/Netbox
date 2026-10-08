# Saved profile review

Run `seed.py` through `manage.py shell` in an isolated NetBox development
database with the Discovery, Lifecycle and Config Compliance plugins enabled.
It creates four dummy devices on documentation-only addresses, two device
models, three profiles, their assignments, and site-scoped NTP/Syslog YAML
standards. Image paths and checksums are
illustrative. Do not connect a device worker to this lab.

For the local review stack created for this change:

```sh
docker compose -p profile-lab -f /private/tmp/netbox-profile-lab/compose.yaml up -d
docker exec -i profile-lab-netbox-1 /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell < testing/job-profiles/seed.py
```

Visit http://127.0.0.1:8091/plugins/discovery/job-profiles/.

Review flow:

1. Edit the remediation profile, toggle a feature and save.
2. Open Model Profiles and inspect both models' upgrade and remediation defaults.
   Edit a job profile and verify **Assigned models** shows those same device types;
   the upgrade settings YAML should not contain a manually entered `models` list.
   Assign a model to another upgrade profile: the first save must identify the
   previous default and require replacement confirmation. After confirming,
   verify both screens agree and the remediation default is unchanged.
3. Add an upgrade job for the Profile review lab site with Device model defaults.
4. Preview an upgrade: Cisco and Arista devices should resolve different profiles.
5. Preview remediation: both models should resolve the shared remediation profile.
6. Schedule the dummy batch, then edit the saved profile. Existing job snapshots
   must remain unchanged.
7. Edit Lab NTP under Config Compliance -> Config Standards. Change an example
   server address and save. The revision should increment once, with a named
   author and a diff available from Revision History.
8. Run `seed_results.py` in this same isolated lab to add explicitly illustrative
   results. The fleet report should distinguish a pass against the first
   revision from a pass against the current revision. No device is contacted.

Regression suite:

```sh
docker exec profile-lab-netbox-1 /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py test netbox_discovery.tests netbox_compliance.tests --keepdb --no-input
```
