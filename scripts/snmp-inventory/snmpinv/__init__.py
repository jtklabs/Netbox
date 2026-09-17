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
    rules.py      fills in what a device does not report, from rules in NetBox
    naming.py     hostname -> device name: which domains come off, from NetBox
    config.py     poller config and SNMPv3 credential sets

The version is reported to the Discovery plugin at every check-in and shown
against the poller in NetBox, so bump it with every change a poller must be
running to benefit from -- it is how "is that poller on the new build yet?" is
answered without logging in to it.
"""

__version__ = "1.3.0"
