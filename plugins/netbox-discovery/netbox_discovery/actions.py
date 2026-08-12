"""State transitions for an onboarding request.

Here rather than in the views because there are two front doors — the UI
buttons and the REST API — and they must behave identically. A rule enforced in
one and forgotten in the other is the kind of difference nobody notices until
somebody automates against the API and gets a result the UI would have refused.

Every function returns the request and raises TransitionError when the move is
not allowed, so callers differ only in how they render the message.
"""

from __future__ import annotations

from django.utils import timezone

from netbox_discovery.choices import OnboardingStatusChoices

__all__ = ('TransitionError', 'approve', 'enter_manually', 'recheck', 'reject',
           'retry')


class TransitionError(Exception):
    """The request is not in a state where this move makes sense."""


def approve(entry, user=None, override_name=None, override_site=None, role=None,
            override_model=None):
    """Accept what the scan found; the poller applies it on its next check-in.

    Approving writes nothing to DCIM. The poller owns every write, because all
    the idempotent create logic already lives in the scanner and a second copy
    here would be a second thing to keep correct.
    """
    if entry.status != OnboardingStatusChoices.STATUS_REVIEW:
        raise TransitionError(
            'Only a request awaiting review can be approved; this one is %s.'
            % entry.get_status_display()
        )
    if not entry.discovered_devices:
        raise TransitionError(
            'There is nothing to approve — no device was reported for this address.'
        )

    # Overrides are applied before the site is checked, because supplying a
    # site override in the same call is exactly how a request that has no site
    # gets approved — an address that fell back to the default region arrives
    # with nowhere to go, and the reviewer supplies it here.
    #
    # None means "leave as it is"; an empty string means "clear it". The
    # difference matters for a PATCH-shaped API call that omits a field.
    if override_name is not None:
        entry.override_name = override_name
    if override_model is not None:
        entry.override_model = override_model
    if override_site is not None:
        entry.override_site = override_site
    if role is not None:
        entry.role = role

    # Both checks below sit *after* the overrides for the same reason: each is
    # something the reviewer supplies in the very submission being validated,
    # so checking first would reject the fix along with the problem.
    if not entry.effective_model:
        # Refused here rather than left to the poller, which would create
        # nothing, report a failure, and by then the person who could have
        # typed the model has moved on.
        raise TransitionError(
            'The scan found no model, so there is no device type to create. '
            'Approve it again with a model, or enter the details by hand.'
        )

    if entry.target_site is None:
        raise TransitionError(
            'This request has no site, so there is nowhere to create the device. '
            'Approve it again with a site override.'
        )

    entry.status = OnboardingStatusChoices.STATUS_APPROVED
    entry.reviewed_at = timezone.now()
    entry.reviewed_by = user
    entry.save()
    return entry


def reject(entry, user=None, reason=''):
    """Decline a request. Nothing is written to DCIM, then or later."""
    if entry.status not in (
        OnboardingStatusChoices.STATUS_REVIEW,
        OnboardingStatusChoices.STATUS_APPROVED,
        OnboardingStatusChoices.STATUS_FAILED,
    ):
        raise TransitionError(
            'This request is not awaiting a decision; it is %s.'
            % entry.get_status_display()
        )
    entry.status = OnboardingStatusChoices.STATUS_REJECTED
    entry.reviewed_at = timezone.now()
    entry.reviewed_by = user
    if reason:
        # Kept in `error` rather than a field of its own: it is the answer to
        # the same question the page already asks, "why is this not done?"
        entry.error = reason
    entry.save()
    return entry


def retry(entry):
    """Put a failed or unresolvable request back in the queue.

    Resolution is re-run on the way, because the fix is almost always in IPAM —
    the missing prefix now exists, or the site has been tagged — and making
    somebody retype the address to pick that up would be silly.
    """
    if entry.status in OnboardingStatusChoices.TERMINAL:
        raise TransitionError(
            'This request is finished (%s); create a new one instead.'
            % entry.get_status_display()
        )
    entry.error = ''
    entry.claimed_at = None
    # Findings from the previous scan go, because they are the thing being
    # redone: they were derived against whatever IPAM said at the time, which
    # is exactly what the retry is correcting. Leaving them would show a stale
    # reading on the detail page until a poller happened to replace it.
    #
    # Hand-entered details are kept. Nobody typed those expecting a button
    # called "try again" to delete them, and no rescan can reproduce them —
    # a device that had to be described by hand is one that does not answer.
    if not entry.manually_entered:
        entry.discovered = {}
    entry.status = OnboardingStatusChoices.STATUS_PENDING
    entry.resolve_target()
    entry.save()
    if entry.status == OnboardingStatusChoices.STATUS_UNRESOLVED:
        raise TransitionError(entry.error)
    return entry


def recheck(entry, user=None):
    """Re-resolve against IPAM, keeping the scan that has already happened.

    The common shape of this: an address falls outside every prefix, the
    default region supplies a poller so it still gets scanned, and it stops
    for review because there is nowhere to create the device. Somebody then
    creates the prefix — and there was no way to say "look again". Retry would
    have done the resolution, but by throwing away a perfectly good scan and
    waiting for a poller to walk the device a second time, which for a device
    that answered fine is pure delay.

    So this re-runs resolution only. If that supplies what was missing, the
    request is re-judged by the same rules a fresh scan is judged by: if
    nothing else about it needs a person, it goes straight to approved and the
    next check-in applies it. If something does, it stays in review with the
    current reason rather than the stale one.
    """
    from netbox_discovery import review

    if entry.status in OnboardingStatusChoices.TERMINAL:
        raise TransitionError(
            'This request is finished (%s); create a new one instead.'
            % entry.get_status_display()
        )
    if not entry.discovered:
        raise TransitionError(
            'Nothing has been scanned for this request yet, so there is no '
            'reading to re-judge. Use Try again to queue it.'
        )

    entry.error = ''
    entry.resolve_target()
    if entry.status == OnboardingStatusChoices.STATUS_UNRESOLVED:
        # resolve_target marks an address it cannot place as unresolved, which
        # is right for one that has never been scanned and wrong here: this
        # one has a reading, and demoting it would lose that it is a request
        # awaiting a decision. Keep it in review, wearing the reason
        # resolution just gave for why it still cannot be placed.
        reason = entry.error
        entry.status = OnboardingStatusChoices.STATUS_REVIEW
        entry.save()
        raise TransitionError(reason)

    needs_review, reason = review.evaluate(entry, entry.discovered)
    if needs_review:
        entry.status = OnboardingStatusChoices.STATUS_REVIEW
        entry.error = reason
        entry.save()
        raise TransitionError(reason)

    entry.status = OnboardingStatusChoices.STATUS_APPROVED
    entry.reviewed_at = timezone.now()
    entry.reviewed_by = user
    entry.save()
    return entry


def enter_manually(entry, user=None, *, name, manufacturer, model, serial='',
                   platform='', role=None, override_site=None,
                   software_version=''):
    """Record hardware details a scan could not obtain, and approve them.

    Built into the same `discovered` shape a poller reports, so everything
    downstream is unchanged: the same review page renders it, the same apply
    creates it, and the poller's existing "device unreachable, use the reading
    that was approved" path does the work. One route into DCIM, not two.

    Goes straight to approved. Somebody has just typed these details in while
    looking at the request; asking them to then approve their own typing is
    ceremony, not a control.
    """
    if entry.status in OnboardingStatusChoices.TERMINAL:
        raise TransitionError(
            'This request is finished (%s); create a new one instead.'
            % entry.get_status_display()
        )
    if not (model or '').strip():
        raise TransitionError('A model is required — it is what the device type is.')

    if override_site is not None:
        entry.override_site = override_site
    if role is not None:
        entry.role = role
    if entry.target_site is None:
        raise TransitionError(
            'This request has no site, so there is nowhere to create the device. '
            'Choose one.'
        )

    entry.discovered = {
        'sys_name': name,
        'sys_descr': '',
        'credential': '',
        'devices': [{
            'name': name,
            'model': model.strip(),
            'serial': (serial or '').strip(),
            'manufacturer': str(manufacturer),
            'platform': str(platform) if platform else '',
            'software_version': (software_version or '').strip(),
            'is_master': True,
            'vc_position': None,
            # No interfaces: nothing observed them, and inventing them would be
            # worse than leaving the device with none.
            'interfaces': [],
            'modules': [],
        }],
        'access_points': [],
    }
    entry.manually_entered = True
    entry.override_name = name
    entry.error = ''
    entry.status = OnboardingStatusChoices.STATUS_APPROVED
    entry.reviewed_at = timezone.now()
    entry.reviewed_by = user
    entry.save()
    return entry
