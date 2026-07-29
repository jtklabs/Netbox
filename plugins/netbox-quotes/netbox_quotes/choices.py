from utilities.choices import ChoiceSet


class QuoteStatusChoices(ChoiceSet):
    key = 'Quote.status'

    STATUS_RECEIVED = 'received'
    STATUS_REVIEWING = 'reviewing'
    STATUS_ACCEPTED = 'accepted'
    STATUS_ORDERED = 'ordered'
    STATUS_EXPIRED = 'expired'
    STATUS_SUPERSEDED = 'superseded'

    CHOICES = [
        (STATUS_RECEIVED, 'Received', 'cyan'),
        (STATUS_REVIEWING, 'Reviewing', 'blue'),
        (STATUS_ACCEPTED, 'Accepted', 'green'),
        (STATUS_ORDERED, 'Ordered', 'purple'),
        (STATUS_EXPIRED, 'Expired', 'red'),
        (STATUS_SUPERSEDED, 'Superseded', 'gray'),
    ]


class MatchStateChoices(ChoiceSet):
    key = 'QuoteLine.match_state'

    STATE_UNMATCHED = 'unmatched'
    STATE_AUTO = 'auto'
    STATE_MANUAL = 'manual'
    STATE_AMBIGUOUS = 'ambiguous'

    CHOICES = [
        (STATE_UNMATCHED, 'Unmatched', 'orange'),
        (STATE_AUTO, 'Matched (auto)', 'green'),
        (STATE_MANUAL, 'Matched (manual)', 'blue'),
        (STATE_AMBIGUOUS, 'Ambiguous serial', 'red'),
    ]
