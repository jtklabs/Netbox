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
