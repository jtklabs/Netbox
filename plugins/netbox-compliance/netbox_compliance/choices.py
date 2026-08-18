"""Choice sets for configuration compliance.

Two separate sets describe the compliance state, which is worth explaining
because they overlap:

    ConfigCheckResultChoices    what the checker *found*, and what is stored on
                                the row. Four values, no exemption among them.
    ConfigComplianceStatusChoices  what a report *shows*, which is the stored
                                result unless the row is exempt, in which case
                                the exemption is the answer.

Keeping them apart means an exemption can be lifted without re-running the
check: the underlying finding is still on the row, so the device goes straight
back to whatever it actually was. Folding exemption into the stored value would
destroy that finding the moment somebody ticked the box.
"""

from utilities.choices import ChoiceSet

__all__ = (
    'ConfigCheckTypeChoices',
    'ConfigCheckResultChoices',
    'ConfigComplianceStatusChoices',
    'ConfigCheckSourceChoices',
)


class ConfigCheckTypeChoices(ChoiceSet):
    """The three genuinely different shapes a config standard comes in.

    A single "golden config diff" cannot express these. They differ in what
    counts as a violation and in what remediation even means:

      absent     a line matching the pattern must NOT appear. Any match is a
                 violation. Remediation, where it is possible at all, is a
                 removal — so it is enforce-only by definition.
                 e.g. `ip http server`, type-7 encoded passwords.
      present    an exact line must appear. Its absence is the violation, and
                 remediation is an addition.
                 e.g. `service password-encryption`.
      exact_set  a governed set of lines keyed by identity. Missing members are
                 one violation, members nobody asked for are another, and the
                 two are remediated by opposite operations.
                 e.g. local `username ...` accounts.
    """

    TYPE_ABSENT = 'absent'
    TYPE_PRESENT = 'present'
    TYPE_EXACT_SET = 'exact-set'

    CHOICES = [
        (TYPE_ABSENT, 'Must be absent', 'red'),
        (TYPE_PRESENT, 'Must be present', 'green'),
        (TYPE_EXACT_SET, 'Exact set', 'blue'),
    ]


class ConfigCheckResultChoices(ChoiceSet):
    """The stored verdict for one device against one standard.

    Unlike the software side, this is stored rather than derived. It has to be:
    the verdict depends on the device's running configuration, and that config
    is exactly what NetBox must not keep — several of these standards match
    lines containing secrets. So the checker decides, and NetBox records the
    decision plus a redacted account of what it saw.

    `unknown` and `error` are separate on purpose. "We have never looked at
    this device" and "we looked and could not get in" call for different
    actions, and merging them is how a fleet quietly develops a population of
    devices nobody has ever successfully checked.
    """

    key = 'ConfigCompliance.result'

    RESULT_COMPLIANT = 'compliant'
    RESULT_NON_COMPLIANT = 'non-compliant'
    RESULT_UNKNOWN = 'unknown'
    RESULT_ERROR = 'error'

    CHOICES = [
        (RESULT_COMPLIANT, 'Compliant', 'green'),
        (RESULT_NON_COMPLIANT, 'Non-compliant', 'red'),
        (RESULT_UNKNOWN, 'Not checked', 'gray'),
        (RESULT_ERROR, 'Check failed', 'orange'),
    ]


class ConfigComplianceStatusChoices(ChoiceSet):
    """What the fleet report shows — the stored result, or the exemption.

    Exempt is a state of its own rather than a filter that hides rows. An
    exemption that removes a device from the report is an exemption nobody ever
    reviews; one that shows up in purple every time somebody opens the page is
    an exemption with a shelf life. `exempt-expired` exists for the same reason:
    past its review date it stops looking like a decision and starts looking
    like a backlog item.
    """

    STATUS_COMPLIANT = 'compliant'
    STATUS_NON_COMPLIANT = 'non-compliant'
    STATUS_UNKNOWN = 'unknown'
    STATUS_ERROR = 'error'
    STATUS_EXEMPT = 'exempt'
    STATUS_EXEMPT_EXPIRED = 'exempt-expired'

    CHOICES = [
        (STATUS_COMPLIANT, 'Compliant', 'green'),
        (STATUS_NON_COMPLIANT, 'Non-compliant', 'red'),
        (STATUS_UNKNOWN, 'Not checked', 'gray'),
        (STATUS_ERROR, 'Check failed', 'orange'),
        (STATUS_EXEMPT, 'Exempt', 'purple'),
        (STATUS_EXEMPT_EXPIRED, 'Exemption expired', 'yellow'),
    ]


class ConfigCheckSourceChoices(ChoiceSet):
    """Where a result came from.

    Provenance matters the same way it does for software readings: a result
    typed in by hand is a claim, one posted by the SSH checker is an
    observation, and the report should let you tell them apart.
    """

    key = 'ConfigCompliance.source'

    SOURCE_MANUAL = 'manual'
    SOURCE_SSH = 'ssh'
    SOURCE_API = 'api'

    CHOICES = [
        (SOURCE_MANUAL, 'Manual entry', 'blue'),
        (SOURCE_SSH, 'SSH check', 'green'),
        (SOURCE_API, 'API', 'cyan'),
    ]
