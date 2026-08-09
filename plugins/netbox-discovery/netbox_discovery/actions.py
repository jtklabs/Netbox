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

__all__ = ('TransitionError', 'approve', 'reject', 'retry')


class TransitionError(Exception):
    """The request is not in a state where this move makes sense."""


def approve(entry, user=None, override_name=None, override_site=None, role=None):
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
    if override_site is not None:
        entry.override_site = override_site
    if role is not None:
        entry.role = role

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
    entry.status = OnboardingStatusChoices.STATUS_PENDING
    entry.resolve_target()
    entry.save()
    if entry.status == OnboardingStatusChoices.STATUS_UNRESOLVED:
        raise TransitionError(entry.error)
    return entry
