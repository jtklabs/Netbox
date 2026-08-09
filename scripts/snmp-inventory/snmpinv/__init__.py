"""SNMPv3 inventory scanner for NetBox.

Collects hardware and interface inventory over SNMPv3 and writes it into NetBox
through the REST API. See the README for deployment and the module docstrings
for the reasoning behind each layer:

    mibs.py       numeric OIDs and the ifType -> NetBox interface type map
    vendors.py    per-vendor OIDs, software version extraction, Aruba AP table
    snmp.py       net-snmp subprocess wrapper and output parsing
    collect.py    walks a device into structured facts
    model.py      turns facts into NetBox-shaped records (stacks, modules, APs)
    selection.py  works out which addresses this poller owns, from NetBox tags
    netbox.py     REST client with lookup-or-create and dry-run
    sync.py       idempotent writes
    config.py     poller config and SNMPv3 credential sets
"""

__version__ = "1.0.0"
