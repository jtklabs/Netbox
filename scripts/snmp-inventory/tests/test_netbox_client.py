"""The NetBox client, without needing a NetBox.

Construction-time behaviour only — anything that talks to a real instance
lives in test_netbox_live.py, which skips when there is nothing to talk to.
These must always run: the thing they cover is what an operator sees on a
poller that is misconfigured, which is exactly when nothing else is available.
"""

from __future__ import annotations


class TestInsecureTlsIsSaidOnceNotPerRequest:
    """verify_ssl = false made urllib3 warn on every single request.

    A sweep issues hundreds, which buries the scan's own output entirely. The
    warning is still worth making — it just belongs in the log once, not
    interleaved with everything the operator is trying to read.
    """

    def setup_method(self):
        from snmpinv import netbox as netbox_module

        netbox_module._warned_about_tls = False

    def test_it_warns_once_however_many_clients_are_built(self, caplog):
        import logging

        from snmpinv.netbox import NetBox

        with caplog.at_level(logging.WARNING, logger="snmpinv.netbox"):
            for _ in range(5):
                NetBox("https://example.invalid/netbox", "nbt_x.y", verify_ssl=False)

        tls = [r for r in caplog.records if "verification is disabled" in r.message]
        assert len(tls) == 1, f"expected one warning, got {len(tls)}"

    def test_the_warning_still_names_the_risk(self, caplog):
        """Silencing it outright would make an insecure poller look identical
        to a secure one, which is worse than the noise."""
        import logging

        from snmpinv.netbox import NetBox

        with caplog.at_level(logging.WARNING, logger="snmpinv.netbox"):
            NetBox("https://example.invalid/netbox", "nbt_x.y", verify_ssl=False)

        text = " ".join(r.message for r in caplog.records)
        assert "verify_ssl" in text and "token" in text

    def test_urllib3_is_actually_silenced(self):
        """The point of the exercise: no per-request warning reaches stderr."""
        import warnings

        import urllib3

        from snmpinv.netbox import NetBox

        with warnings.catch_warnings(record=True) as seen:
            # From a known-empty filter state, so what is asserted is the
            # filter the client installs and not one inherited from pytest.
            # Note the client is built *inside* the context: simplefilter or
            # resetwarnings afterwards would undo the very thing under test.
            warnings.resetwarnings()
            NetBox("https://example.invalid/netbox", "nbt_x.y", verify_ssl=False)
            warnings.warn("per-request", urllib3.exceptions.InsecureRequestWarning)

        assert not [w for w in seen
                    if issubclass(w.category, urllib3.exceptions.InsecureRequestWarning)]

    def test_a_verifying_client_leaves_the_warning_in_place(self):
        """Suppression must be scoped to having actually asked for it."""
        import warnings

        import urllib3

        from snmpinv.netbox import NetBox

        with warnings.catch_warnings(record=True) as seen:
            warnings.resetwarnings()
            NetBox("https://example.invalid/netbox", "nbt_x.y", verify_ssl=True)
            warnings.warn("per-request", urllib3.exceptions.InsecureRequestWarning)

        assert [w for w in seen
                if issubclass(w.category, urllib3.exceptions.InsecureRequestWarning)]

    def test_a_verifying_client_says_nothing(self, caplog):
        import logging

        from snmpinv.netbox import NetBox

        with caplog.at_level(logging.WARNING, logger="snmpinv.netbox"):
            NetBox("https://example.invalid/netbox", "nbt_x.y", verify_ssl=True)

        assert not [r for r in caplog.records if "verification is disabled" in r.message]
