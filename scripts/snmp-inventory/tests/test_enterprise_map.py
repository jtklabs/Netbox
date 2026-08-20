"""The sysObjectID enterprise-arc -> manufacturer table, against reality.

The first audit of this table (2026-08-20, against the IANA Private
Enterprise Numbers registry) found eight numbers labelled "Aruba Networks"
of which only one was Aruba's. The worst was 14179 — Airespace, which CISCO
acquired — so every Cisco AireOS wireless controller in the estate would
have been created in NetBox as Aruba hardware. These tests pin the
identifications that were wrong once, so they cannot quietly rot back.
"""

from snmpinv import mibs


class TestEnterpriseNumbersMatchTheirRegistrants:
    def test_airespace_is_cisco_not_aruba(self):
        """14179 = Airespace, acquired by Cisco: AireOS WLCs answer here."""
        assert mibs.ENTERPRISE_MANUFACTURERS[14179] == "Cisco"

    def test_trapeze_is_juniper_not_aruba(self):
        assert mibs.ENTERPRISE_MANUFACTURERS[14525] == "Juniper Networks"

    def test_arubas_own_arc_is_the_only_aruba(self):
        aruba_arcs = [n for n, name in mibs.ENTERPRISE_MANUFACTURERS.items()
                      if "aruba" in name.lower()]
        assert aruba_arcs == [14823]

    def test_unisphere_is_juniper_not_adtran(self):
        assert mibs.ENTERPRISE_MANUFACTURERS[4874] == "Juniper Networks"
        assert mibs.ENTERPRISE_MANUFACTURERS[664] == "Adtran"

    def test_neoteris_lineage_is_not_sonicwall(self):
        assert mibs.ENTERPRISE_MANUFACTURERS[12532] == "Pulse Secure"
        assert mibs.ENTERPRISE_MANUFACTURERS[8741] == "SonicWall"

    def test_every_profile_enterprise_is_consistent_with_the_map(self):
        """A VendorProfile and the map must never disagree about a vendor.

        The profile's manufacturer wins at collection time, so a mismatch
        would not misfile a device — but it would mean one of the two is
        wrong, and nothing else would ever surface it.
        """
        from snmpinv import vendors

        for enterprise, profile in vendors.PROFILES.items():
            mapped = mibs.ENTERPRISE_MANUFACTURERS.get(enterprise)
            if mapped is None:
                continue
            assert profile.manufacturer.split()[0].lower().startswith(
                mapped.split()[0].lower().rstrip('.,')
            ) or mapped.split()[0].lower().startswith(
                profile.manufacturer.split()[0].lower()
            ), f"{enterprise}: profile says {profile.manufacturer!r}, map says {mapped!r}"
