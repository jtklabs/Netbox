from netbox.choices import ChoiceSet


class UpgradeOperationChoices(ChoiceSet):
    CHOICES = [('audit', 'Pre-upgrade audit', 'cyan'),
               ('stage', 'Stage image only', 'blue'),
               ('upgrade', 'Install upgrade', 'orange'),
               ('remediate', 'Remediate configuration', 'purple')]


# configure.py features a queued remediation may run; the poller checks the same list.
REMEDIATION_FEATURES = [('ntp', 'NTP'), ('syslog', 'Syslog'), ('banner', 'Banner'), ('acl', 'ACLs'),
                        ('users', 'Local users'), ('snmp', 'SNMP'), ('snmp_packetsize', 'SNMP packet size')]
REMEDIATION_MODES = [('add', 'Add missing entries only'), ('replace', 'Replace: add missing and remove the rest')]


class UpgradeGroupSourceChoices(ChoiceSet):
    CHOICES = [('manual', 'Manual', 'gray'), ('fhrp', 'FHRP group (HSRP/VRRP)', 'blue'), ('cable', 'Cabled uplink', 'teal')]


class UpgradeStatusChoices(ChoiceSet):
    CHOICES = [('pending', 'Scheduled', 'cyan'), ('held', 'Held', 'yellow'), ('claimed', 'Prechecks', 'blue'),
               ('running', 'Applying', 'purple'), ('completed', 'Completed', 'green'),
               ('completed_with_warnings', 'Completed with warnings', 'orange'),
               ('failed', 'Failed / blocked', 'red'), ('recovery_required', 'Recovery required', 'red'),
               ('expired', 'Start window missed', 'orange'), ('cancelled', 'Cancelled', 'gray')]


ACTIVE = ('claimed', 'running', 'recovery_required')
WAITING = ('pending', 'held')
TERMINAL = ('completed', 'completed_with_warnings', 'failed', 'expired', 'cancelled')
