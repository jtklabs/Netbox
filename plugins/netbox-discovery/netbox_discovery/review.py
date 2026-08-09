"""Decide whether a scan needs a person to look at it.

Most devices are unremarkable: they report a model and a serial, they sit in a
prefix that names a site, and nothing about them contradicts what NetBox
already holds. Making somebody click Apply on each of those teaches them to
click Apply without reading, which is worse than not asking at all.

So review is spent where it earns something — the cases below, each of which is
either a data problem that will bite later or a physical event somebody should
know about. Everything else applies itself.

The one rule this must not break: a device is never created from a scan that
did not actually identify it.
"""

from __future__ import annotations

from dcim.models import Device

__all__ = ('evaluate',)


def evaluate(entry, payload):
    """Return (needs_review, reason) for a reported scan.

    `payload` is the discovered dict as the poller sent it.
    """
    devices = payload.get('devices') or []
    if not devices:
        return True, 'The scan reported no devices, so there is nothing to create.'

    primary = _primary(devices)

    if not (primary.get('model') or '').strip():
        # Without a model there is no device type, and inventing one is the
        # habit this whole tool exists to break.
        return True, (
            'The device did not report a model, so no device type can be chosen. '
            'Enter its details by hand, or fix SNMP on it and scan again.'
        )

    if entry.target_site is None:
        return True, (
            'No prefix placed this address, so there is nowhere to create the '
            'device. Choose a site.'
        )

    clash = _serial_clash(devices, entry)
    if clash:
        return True, clash

    return False, ''


def _primary(devices):
    for device in devices:
        if device.get('is_master'):
            return device
    return devices[0]


def _serial_clash(devices, entry):
    """Is a reported serial already on some other device in NetBox?

    Either the same box has been onboarded twice under two addresses, or a
    serial was typed in wrongly somewhere. Both want a person: creating a second
    device for hardware that already exists is exactly the kind of duplicate
    that makes an inventory stop being trusted.

    A serial on the device this request already produced is not a clash — that
    is a re-run, and it is fine.
    """
    for reported in devices:
        serial = (reported.get('serial') or '').strip()
        if not serial:
            continue
        existing = Device.objects.filter(serial__iexact=serial).exclude(
            pk=entry.device_id or 0
        ).first()
        if existing is not None:
            return (
                'Serial %s is already on %s. Either this device has been '
                'onboarded before under another address, or one of the two '
                'serials is wrong.' % (serial, existing)
            )
    return ''
