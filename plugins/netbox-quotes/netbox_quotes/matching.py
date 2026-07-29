"""Serial-number matching of quote lines to devices and device components.

NetBox does not enforce serial uniqueness, so a serial can legitimately match
zero, one, or several objects; only an unambiguous single match auto-assigns.
Manually-matched lines are never touched by re-matching.
"""

from netbox_quotes.choices import MatchStateChoices


def find_matches(serial):
    from dcim.models import Device, InventoryItem, Module

    serial = (serial or '').strip()
    if not serial:
        return []
    matches = []
    for model in (Device, Module, InventoryItem):
        matches.extend(model.objects.filter(serial__iexact=serial)[:10])
    return matches


def match_line(line):
    """Set line.assigned_object/match_state from its serial. Mutates, does not save."""
    matches = find_matches(line.serial)
    if len(matches) == 1:
        line.assigned_object = matches[0]
        line.match_state = MatchStateChoices.STATE_AUTO
    elif matches:
        line.assigned_object = None
        line.match_state = MatchStateChoices.STATE_AMBIGUOUS
    else:
        line.assigned_object = None
        line.match_state = MatchStateChoices.STATE_UNMATCHED
    return line.match_state


def rematch_quote(quote):
    """Re-run matching for all non-manual lines of a quote. Returns state counts."""
    results = {}
    lines = quote.lines.exclude(match_state=MatchStateChoices.STATE_MANUAL)
    for line in lines:
        line.assigned_object = None
        state = match_line(line)
        line.save()
        results[state] = results.get(state, 0) + 1
    return results
