from netbox_compliance.models import ConfigStandard


def feature_standards():
    return [ConfigStandard.objects.create(
        name=name, check_type='netops', definition_yaml=definition,
        auto_remediable=True, allow_enforce=True,
    ) for name, definition in (
        ('NTP', 'ntp:\n  servers: [192.0.2.10]\n'),
        ('Syslog', 'syslog:\n  destinations: [192.0.2.20]\n'),
    )]
