from netbox.choices import ChoiceSet


class OnboardingStatusChoices(ChoiceSet):
    """Where an onboarding request has got to.

    More states than a boolean because the ways a request can be *not finished*
    are the useful part: waiting for a poller to wake up, waiting for a person
    to look at it, and failed are three different things to do something about,
    and collapsing them would hide which one you are looking at.
    """

    key = 'OnboardingRequest.status'

    STATUS_PENDING = 'pending'
    STATUS_SCANNING = 'scanning'
    STATUS_REVIEW = 'review'
    STATUS_APPROVED = 'approved'
    STATUS_APPLIED = 'applied'
    STATUS_REJECTED = 'rejected'
    STATUS_FAILED = 'failed'
    STATUS_UNRESOLVED = 'unresolved'

    CHOICES = [
        (STATUS_PENDING, 'Waiting for poller', 'cyan'),
        (STATUS_SCANNING, 'Scanning', 'blue'),
        (STATUS_REVIEW, 'Awaiting review', 'orange'),
        (STATUS_APPROVED, 'Approved, waiting to apply', 'purple'),
        (STATUS_APPLIED, 'Applied', 'green'),
        (STATUS_REJECTED, 'Rejected', 'gray'),
        (STATUS_FAILED, 'Scan failed', 'red'),
        (STATUS_UNRESOLVED, 'No poller found', 'red'),
    ]

    # States a poller may pick work up from, and what it should do with it.
    CLAIMABLE_FOR_SCAN = (STATUS_PENDING,)
    CLAIMABLE_FOR_APPLY = (STATUS_APPROVED,)

    # States where nothing further will happen without a person.
    TERMINAL = (STATUS_APPLIED, STATUS_REJECTED)

    # A request in one of these is waiting on us, not on anybody else, and is
    # what the "needs attention" count on the dashboard is drawn from.
    NEEDS_ATTENTION = (STATUS_REVIEW, STATUS_FAILED, STATUS_UNRESOLVED)


class ReplacementKindChoices(ChoiceSet):
    """What was swapped out."""

    key = 'HardwareReplacement.kind'

    KIND_CHASSIS = 'chassis'
    KIND_MODULE = 'module'

    CHOICES = [
        (KIND_CHASSIS, 'Chassis', 'orange'),
        (KIND_MODULE, 'Module', 'blue'),
    ]


class IssueKindChoices(ChoiceSet):
    """What a poller noticed that a person should settle."""

    key = 'DiscoveryIssue.kind'

    KIND_DUPLICATE_SERIAL = 'duplicate-serial'
    KIND_NO_MODEL = 'no-model'
    KIND_OTHER = 'other'

    CHOICES = [
        (KIND_DUPLICATE_SERIAL, 'Serial on another device', 'red'),
        (KIND_NO_MODEL, 'No model reported', 'orange'),
        (KIND_OTHER, 'Other', 'gray'),
    ]


class IssueStatusChoices(ChoiceSet):
    key = 'DiscoveryIssue.status'

    STATUS_OPEN = 'open'
    STATUS_RESOLVED = 'resolved'
    STATUS_IGNORED = 'ignored'

    CHOICES = [
        (STATUS_OPEN, 'Open', 'red'),
        (STATUS_RESOLVED, 'Resolved', 'green'),
        (STATUS_IGNORED, 'Ignored', 'gray'),
    ]


class RuleMatchFieldChoices(ChoiceSet):
    """What a discovery rule may look at on a scanned device."""

    key = 'DiscoveryRule.match_field'

    FIELD_NAME = 'name'
    FIELD_MODEL = 'model'
    FIELD_MANUFACTURER = 'manufacturer'
    FIELD_PLATFORM = 'platform'
    FIELD_SERIAL = 'serial'
    FIELD_SOFTWARE_VERSION = 'software_version'
    FIELD_SYS_DESCR = 'sys_descr'
    FIELD_ADDRESS = 'address'

    CHOICES = [
        (FIELD_NAME, 'Device name'),
        (FIELD_MODEL, 'Model'),
        (FIELD_MANUFACTURER, 'Manufacturer'),
        (FIELD_PLATFORM, 'Platform'),
        (FIELD_SERIAL, 'Serial'),
        (FIELD_SOFTWARE_VERSION, 'Software version'),
        (FIELD_SYS_DESCR, 'System description (sysDescr)'),
        (FIELD_ADDRESS, 'Scanned address'),
    ]


class RuleOperatorChoices(ChoiceSet):
    """How the matched field is compared. Every comparison is case-insensitive."""

    key = 'DiscoveryRule.match_operator'

    OP_CONTAINS = 'contains'
    OP_STARTS_WITH = 'starts_with'
    OP_ENDS_WITH = 'ends_with'
    OP_EQUALS = 'equals'
    OP_REGEX = 'regex'

    CHOICES = [
        (OP_CONTAINS, 'contains'),
        (OP_STARTS_WITH, 'starts with'),
        (OP_ENDS_WITH, 'ends with'),
        (OP_EQUALS, 'is exactly'),
        (OP_REGEX, 'matches the regular expression'),
    ]


class RuleSetFieldChoices(ChoiceSet):
    """What a discovery rule may fill in.

    The facts that decide what gets created, and the serial. The name is never
    blank (it falls back to the address) and has an override of its own. A
    serial belongs to one box — serials are what support contracts and quotes
    are matched on — so the model insists a serial rule match the device name
    or address exactly, and only fill in a serial the device does not report.
    """

    key = 'DiscoveryRule.set_field'

    FIELD_MODEL = 'model'
    FIELD_MANUFACTURER = 'manufacturer'
    FIELD_PLATFORM = 'platform'
    FIELD_SOFTWARE_VERSION = 'software_version'
    FIELD_SERIAL = 'serial'

    CHOICES = [
        (FIELD_MODEL, 'Model'),
        (FIELD_MANUFACTURER, 'Manufacturer'),
        (FIELD_PLATFORM, 'Platform'),
        (FIELD_SOFTWARE_VERSION, 'Software version'),
        (FIELD_SERIAL, 'Serial'),
    ]
