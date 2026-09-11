from netbox.choices import ChoiceSet


class UpgradeOperationChoices(ChoiceSet):
    CHOICES = [('audit', 'Pre-upgrade audit', 'cyan'),
               ('stage', 'Stage image only', 'blue'),
               ('upgrade', 'Install upgrade', 'orange')]


class UpgradeStatusChoices(ChoiceSet):
    CHOICES = [('pending', 'Scheduled', 'cyan'), ('claimed', 'Prechecks', 'blue'),
               ('running', 'Applying', 'purple'), ('completed', 'Completed', 'green'),
               ('completed_with_warnings', 'Completed with warnings', 'orange'),
               ('failed', 'Failed / blocked', 'red'), ('recovery_required', 'Recovery required', 'red'),
               ('expired', 'Start window missed', 'orange'), ('cancelled', 'Cancelled', 'gray')]


ACTIVE = ('claimed', 'running', 'recovery_required')
TERMINAL = ('completed', 'completed_with_warnings', 'failed', 'expired', 'cancelled')
