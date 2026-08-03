"""Example custom collector for orb-agent's `worker` backend.

This is the answer to "can I run my own Python on the remote box and have the
results land in NetBox": yes. orb-agent's worker backend loads a pip-installed
package, calls run() on your Backend subclass on whatever schedule you set, and
ships whatever entities you return through Diode into NetBox.

Your run() is ordinary Python. It can SSH to devices, hit a vendor API, read a
local file, shell out — anything the remote box can do. The one constraint is
the OUTPUT: entities must be NetBox-shaped (Device, Interface, IPAddress, ...).
Data that has no NetBox field of its own goes in custom_fields, which includes
a json= variant for arbitrary structure.

Install by listing this package in the file INSTALL_WORKERS_PATH points at
(see ../workers.txt), then reference it from a worker policy:

    worker:
      my_collector:
        config:
          package: example_collector
          schedule: "0 * * * *"     # omit to run once per agent start
          site: Branch A
        scope:
          targets:
            - 10.20.0.5
"""

from collections.abc import Iterable

from netboxlabs.diode.sdk.ingester import Device, Entity
from worker.backend import Backend
from worker.models import Metadata, Policy


class ExampleCollector(Backend):
    """Collects whatever you need and returns NetBox entities."""

    @classmethod
    def describe(cls) -> Metadata:
        # Must be a classmethod. The older instance-level setup() still works
        # but is deprecated and goes away in worker 2.0.
        return Metadata(
            name="example_collector",
            app_name="example-collector",
            app_version="0.1.0",
        )

    def run(self, policy_name: str, policy: Policy, **kwargs) -> Iterable[Entity]:
        # Everything under `config:` in the policy is on policy.config, and the
        # Config model allows extra keys, so add whatever you need there.
        site = getattr(policy.config, "site", None)
        targets = []
        scope = policy.scope
        if isinstance(scope, dict):
            targets = scope.get("targets") or []
        elif isinstance(scope, list):
            targets = scope

        entities: list[Entity] = []
        for target in targets:
            facts = self.collect(str(target))
            if not facts:
                continue
            entities.append(
                Entity(
                    device=Device(
                        name=facts["name"],
                        site=site,
                        role=facts.get("role") or "undefined",
                        serial=facts.get("serial") or "",
                        # Anything without a native NetBox field goes here.
                        # custom_fields={"last_seen_by": CustomFieldValue(text=policy_name)},
                    )
                )
            )
        return entities

    def collect(self, target: str) -> dict:
        """Replace this with the real work.

        Runs on the remote box, so it has that box's network reach — which is
        the whole point. Raising here marks the policy run failed and is
        reported through the agent's OpenTelemetry metrics; returning an empty
        dict just skips the target.
        """
        return {
            "name": f"device-{target.replace('.', '-')}",
            "role": "switch",
            "serial": "",
        }
