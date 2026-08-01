from utilities.choices import ChoiceSet


class LifecycleSourceChoices(ChoiceSet):
    key = 'ModelLifecycle.source'

    SOURCE_MANUAL = 'manual'
    SOURCE_CISCO = 'cisco-eox'

    CHOICES = [
        (SOURCE_MANUAL, 'Manual', 'blue'),
        (SOURCE_CISCO, 'Cisco EoX', 'cyan'),
    ]


class LifecycleStatusChoices(ChoiceSet):
    """Derived status — computed from the dates, never stored."""

    STATUS_CURRENT = 'current'
    STATUS_EOS_ANNOUNCED = 'eos-announced'
    STATUS_END_OF_SALE = 'end-of-sale'
    STATUS_END_OF_SUPPORT = 'end-of-support'
    STATUS_UNKNOWN = 'unknown'

    CHOICES = [
        (STATUS_CURRENT, 'Current', 'green'),
        (STATUS_EOS_ANNOUNCED, 'EoL announced', 'cyan'),
        (STATUS_END_OF_SALE, 'Past end of sale', 'orange'),
        (STATUS_END_OF_SUPPORT, 'Past end of support', 'red'),
        (STATUS_UNKNOWN, 'Unknown', 'gray'),
    ]
