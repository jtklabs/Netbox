"""Configuration standards, and what each device was found to be doing about them.

    ConfigStandard    one rule, written down: which config lines it governs,
                      what the correct state is, how (or whether) it can be
                      remediated, which devices it applies to, and from when.
    ConfigCompliance  one device measured against one standard: the verdict,
                      a redacted account of what was seen, when it was checked,
                      and any exemption.

Both inherit PrimaryModel, so NetBox writes an ObjectChange for every create,
update and delete — including writes arriving over the REST API from the
checker. That is the audit trail; there is deliberately no hand-rolled one
alongside it.

Nothing here stores a running configuration. That is not an oversight but the
central constraint: standards 4 and 5 match lines that contain password hashes,
so anything kept in NetBox or printed to a terminal is redacted first, by the
checker, before it ever arrives. `observed` and `pre_change_config` hold
redacted text, and the field help text says so, because the one way this design
fails is somebody deciding it would be convenient to post the raw config.

Three modes, and where each is allowed:

    audit    always. Reads, compares, records a result.
    update   adds what is missing. Needs `auto_remediable`.
    enforce  update, plus removing config the standard says must not be there.
             Needs `auto_remediable` AND `allow_enforce`, per standard.

`allow_enforce` is per standard rather than a global switch because the
difference between the two is whether a run can delete a local account off a
production switch. A global flag that silently starts doing that to every
standard at once is precisely the accident worth engineering against.
"""

import re
from datetime import date, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from netbox.models import PrimaryModel

from netbox_compliance.choices import (
    ConfigCheckResultChoices,
    ConfigCheckSourceChoices,
    ConfigCheckTypeChoices,
    ConfigComplianceStatusChoices,
)

__all__ = (
    'ConfigStandard',
    'ConfigCompliance',
)

# Substitution in a remediation template. Deliberately not str.format(): a
# template is operator-authored text that gets rendered into a command sent to a
# switch, and format() would let `{key.__class__.__mro__}` walk the object graph
# and `{0:>999999999}` build a gigabyte of spaces. This matches a bare
# identifier in braces and nothing else.
TEMPLATE_VARIABLE = re.compile(r'\{([a-z_][a-z0-9_]*)\}', re.IGNORECASE)

# Variables every template may use, whatever the standard.
#   key   the identity of the entry — the username, or the whole line for a
#         `present` standard
#   line  the config line as it was read from the device, used by `absent`
#         removals ("no {line}")
BASE_TEMPLATE_VARIABLES = ('key', 'line')

# The named group a regex must provide before an exact_set standard can tell one
# governed line from another. `^username (?P<key>\S+)` makes the username the
# identity, which is what "the standard defines which accounts should exist"
# means in practice: privilege and secret may change without the account
# becoming a different account.
KEY_GROUP = 'key'


def _plugin_settings():
    return settings.PLUGINS_CONFIG.get('netbox_compliance', {})


def render_template(template, values):
    """Substitute {name} placeholders from `values`, leaving unknown ones alone.

    Returns (text, missing) so a caller can refuse to send a command that still
    has a hole in it. Leaving unknown placeholders in place rather than raising
    is what makes that possible: the rendered text shows exactly which variable
    was not supplied, which is a far better error than KeyError('secret').

    Lives on the model side so the form can validate a template at save time.
    The checker implements the same substitution — it runs on a poller box with
    no NetBox on it — and tests/test_templates.py pins the two together.
    """
    missing = []

    def substitute(match):
        name = match.group(1)
        if name in values and values[name] not in (None, ''):
            return str(values[name])
        missing.append(name)
        return match.group(0)

    return TEMPLATE_VARIABLE.sub(substitute, template or ''), missing


class ConfigStandard(PrimaryModel):
    """One configuration rule: what it governs, what is correct, and how to fix it.

    The three check types (see ConfigCheckTypeChoices) share one model because
    they share one workflow — match lines, compare against expectation, plan
    commands — and differ only in which half of the comparison is the violation.
    Splitting them into three models would triple the UI, the API and the report
    to express a difference that is one field wide.

    Two capability flags, not one, because "can this be fixed automatically" and
    "may a run remove things for this" are different questions:

      auto_remediable=False   detectable but not fixable by any mode. Standard 4
                              (no type-7 passwords) is the case this exists for.
                              Converting `password 7 <hash>` to a `secret` needs
                              the plaintext. Type 7 is reversible, so a tool
                              *could* decrypt and re-set it — and quietly
                              round-tripping production credentials through a
                              script is not a thing to do because it is
                              technically possible. It is reported and left for
                              a person.
      allow_enforce=False     fixable, but this standard's removals are not
                              authorised. Default, and the local-users standard
                              is why: `enforce` on it deletes accounts.
    """

    name = models.CharField(max_length=100, help_text='e.g. "No HTTP server"')

    check_type = models.CharField(
        max_length=20,
        choices=ConfigCheckTypeChoices,
        default=ConfigCheckTypeChoices.TYPE_PRESENT,
        help_text='What shape of rule this is — see the help below each type',
    )

    match_pattern = models.CharField(
        max_length=500,
        help_text=(
            'Regular expression matched against each configuration line. '
            'An exact-set standard must capture the entry identity in a group '
            r'named "key", e.g. ^username (?P<key>\S+)'
        ),
    )

    expected_entries = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'What should be there. A list of lines for a "present" standard, '
            'a list of entries for an "exact set", empty for "absent". '
            'Never put a secret here — see the remediation templates.'
        ),
    )

    # --- Remediation. Templates rather than literal commands, because the one
    # thing a governed set of local accounts needs — the secret — must not be in
    # NetBox. `username {key} privilege {privilege} secret {secret}` renders
    # `secret` from the checker's environment at the moment of the write, so the
    # standard can say which accounts should exist without the database holding
    # a single credential.
    add_template = models.CharField(
        max_length=500,
        blank=True,
        help_text=(
            'Command that adds a missing entry, e.g. "{key}" or '
            '"username {key} privilege {privilege} secret {secret}". '
            'Variables not captured by the pattern are supplied by the checker '
            'at run time — that is how secrets stay out of NetBox.'
        ),
    )
    remove_template = models.CharField(
        max_length=500,
        blank=True,
        help_text='Command that removes an offending entry, e.g. "no {line}" or "no username {key}"',
    )

    auto_remediable = models.BooleanField(
        default=True,
        verbose_name='Can be remediated automatically',
        help_text=(
            'Clear this for a standard that can be detected but not safely '
            'fixed by a script. Nothing will ever be sent for it.'
        ),
    )
    allow_enforce = models.BooleanField(
        default=False,
        verbose_name='Allow enforce (removals)',
        help_text=(
            'Enforce mode may remove configuration for this standard. Off by '
            'default: enforcing a local-user standard deletes accounts.'
        ),
    )
    remediation_notes = models.TextField(
        blank=True,
        help_text='What a person should do instead, when this cannot be fixed automatically',
    )

    # --- Scope. Every dimension is a narrowing filter and an empty one means
    # "no restriction on this dimension", uniformly. A standard with all four
    # empty applies to the whole fleet, which for "no HTTP server" is exactly
    # right and for anything platform-specific is a mistake the form warns about.
    platforms = models.ManyToManyField(
        to='dcim.Platform',
        blank=True,
        related_name='config_standards',
        help_text='Platforms this applies to. Empty means every platform.',
    )
    roles = models.ManyToManyField(
        to='dcim.DeviceRole',
        blank=True,
        related_name='config_standards',
        help_text='Device roles this applies to. Empty means every role.',
    )
    sites = models.ManyToManyField(
        to='dcim.Site',
        blank=True,
        related_name='config_standards',
        help_text='Sites this applies to. Empty means every site.',
    )
    # Not `tags` — NetBoxModel already owns that name for the standard's own
    # tags. These are tags a *device* must carry to be in scope.
    device_tags = models.ManyToManyField(
        to='extras.Tag',
        blank=True,
        related_name='scoped_config_standards',
        help_text='Devices must carry one of these tags. Empty means any device.',
    )

    # --- Effective dating, following SoftwareStandard: supersede by closing one
    # out and opening its successor, so "what was our standard in March?" is a
    # query against data rather than changelog archaeology.
    valid_from = models.DateField(
        default=date.today, help_text='Date this became our standard'
    )
    valid_to = models.DateField(
        blank=True, null=True, help_text='Leave empty while this standard is current'
    )

    clone_fields = (
        'check_type', 'auto_remediable', 'allow_enforce', 'valid_from',
    )

    class Meta:
        ordering = ('name', '-valid_from')
        verbose_name = 'config standard'
        verbose_name_plural = 'config standards'
        constraints = (
            models.UniqueConstraint(
                'name', 'valid_from',
                name='%(app_label)s_%(class)s_unique_name_from',
                violation_error_message=(
                    'A standard with that name already starts on that date.'
                ),
            ),
        )
        indexes = (
            models.Index(fields=('name',)),
            models.Index(fields=('valid_from', 'valid_to')),
            models.Index(fields=('check_type',)),
        )

    def __str__(self):
        if self.valid_to:
            return '%s (%s to %s)' % (self.name, self.valid_from, self.valid_to)
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_compliance:configstandard', args=[self.pk])

    # ------------------------------------------------------------------ #
    # Validation. A standard that does not make sense is worse than no
    # standard: it produces confident, wrong report rows. Everything that can
    # be checked before a checker ever loads it is checked here.
    # ------------------------------------------------------------------ #
    def clean(self):
        super().clean()

        compiled = None
        if self.match_pattern:
            try:
                compiled = re.compile(self.match_pattern)
            except re.error as exc:
                raise ValidationError(
                    {'match_pattern': 'That is not a valid regular expression: %s' % exc}
                )

        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValidationError({'valid_to': 'The end date cannot be before the start date.'})

        entries = self._clean_entries()

        if self.check_type == ConfigCheckTypeChoices.TYPE_ABSENT:
            if entries:
                raise ValidationError({
                    'expected_entries': (
                        'An "absent" standard has no expected entries — the pattern '
                        'alone says what must not appear.'
                    )
                })
        elif not entries:
            raise ValidationError({
                'expected_entries': 'List what should be there, one entry per line.'
            })

        if self.check_type == ConfigCheckTypeChoices.TYPE_EXACT_SET:
            if compiled is not None and KEY_GROUP not in compiled.groupindex:
                raise ValidationError({
                    'match_pattern': (
                        'An exact-set standard has to be able to tell one entry from '
                        r'another. Capture the identity in a group named "key", e.g. '
                        r'^username (?P<key>\S+)'
                    )
                })

        # A standard nothing can fix must not also claim removals are fine —
        # that combination reads as "enforce this" and would be silently
        # ignored by every mode.
        if self.allow_enforce and not self.auto_remediable:
            raise ValidationError({
                'allow_enforce': (
                    'This standard is marked as not automatically remediable, so '
                    'enforce has nothing it may do. Clear one of the two.'
                )
            })

        self._clean_templates()
        self._clean_overlap()

    def _clean_entries(self):
        """Normalise and validate expected_entries, returning the normalised list."""
        raw = self.expected_entries
        if raw in (None, ''):
            self.expected_entries = []
            return []
        if not isinstance(raw, list):
            raise ValidationError({
                'expected_entries': 'Expected a list of entries.'
            })

        normalised = []
        seen = set()
        for item in raw:
            if isinstance(item, str):
                item = {'key': item}
            if not isinstance(item, dict):
                raise ValidationError({
                    'expected_entries': 'Each entry is either a line or an object with a "key".'
                })
            key = str(item.get('key', '')).strip()
            if not key:
                raise ValidationError({'expected_entries': 'Every entry needs a key.'})
            variables = item.get('vars') or {}
            if not isinstance(variables, dict):
                raise ValidationError({
                    'expected_entries': 'An entry\'s "vars" must be an object.'
                })
            if key in seen:
                raise ValidationError({
                    'expected_entries': '"%s" is listed twice.' % key
                })
            seen.add(key)
            normalised.append({'key': key, 'vars': {str(k): v for k, v in variables.items()}})

        self.expected_entries = normalised
        return normalised

    def _clean_templates(self):
        """Refuse a remediable standard whose templates cannot produce a command."""
        if not self.auto_remediable:
            return

        needs_add = self.check_type in (
            ConfigCheckTypeChoices.TYPE_PRESENT, ConfigCheckTypeChoices.TYPE_EXACT_SET
        )
        if needs_add and not self.add_template.strip():
            raise ValidationError({
                'add_template': (
                    'This standard adds configuration, so it needs a template — '
                    '"{key}" sends the line itself.'
                )
            })

        needs_remove = self.check_type == ConfigCheckTypeChoices.TYPE_ABSENT or (
            self.check_type == ConfigCheckTypeChoices.TYPE_EXACT_SET and self.allow_enforce
        )
        if needs_remove and not self.remove_template.strip():
            raise ValidationError({
                'remove_template': (
                    'This standard removes configuration, so it needs a template — '
                    '"no {line}" negates the offending line.'
                )
            })

        # `{line}` is only meaningful where a matched line exists to negate.
        # Using it in an add template renders a command out of a line that by
        # definition is not on the device.
        if 'line' in self.add_template_variables:
            raise ValidationError({
                'add_template': (
                    '{line} is the line found on the device, so it has no meaning '
                    'when adding one that is missing. Use {key}.'
                )
            })

    def _clean_overlap(self):
        """Two versions of the same named standard must not both apply on a day."""
        if not (self.name and self.valid_from):
            return
        others = ConfigStandard.objects.filter(name=self.name).exclude(pk=self.pk)
        for other in others:
            if self.overlaps(other):
                raise ValidationError(
                    'This overlaps an existing version of "%s" (%s). Close that one '
                    'out with an end date first.' % (self.name, other)
                )

    # ------------------------------------------------------------------ #
    @property
    def add_template_variables(self):
        return tuple(TEMPLATE_VARIABLE.findall(self.add_template or ''))

    @property
    def remove_template_variables(self):
        return tuple(TEMPLATE_VARIABLE.findall(self.remove_template or ''))

    @property
    def runtime_variables(self):
        """Template variables the checker must supply — typically just `secret`.

        Anything the pattern captures, the entry declares, or the base set
        provides is already known. What is left has to come from the checker's
        environment at write time, and the detail page lists it so an operator
        can see what a run will ask them for.
        """
        known = set(BASE_TEMPLATE_VARIABLES)
        try:
            known |= set(re.compile(self.match_pattern).groupindex)
        except (re.error, TypeError):
            pass
        for entry in self.entries:
            known |= set(entry.get('vars') or {})
        used = set(self.add_template_variables) | set(self.remove_template_variables)
        return tuple(sorted(used - known))

    @property
    def entries(self):
        """expected_entries, always as a list of {'key', 'vars'} dicts."""
        normalised = []
        for item in self.expected_entries or []:
            if isinstance(item, str):
                normalised.append({'key': item, 'vars': {}})
            elif isinstance(item, dict) and item.get('key'):
                normalised.append({'key': item['key'], 'vars': item.get('vars') or {}})
        return normalised

    @property
    def entry_keys(self):
        return [entry['key'] for entry in self.entries]

    def overlaps(self, other):
        """Do these two both apply on some day? Open-ended means forever."""
        if self.valid_to is not None and self.valid_to < other.valid_from:
            return False
        if other.valid_to is not None and other.valid_to < self.valid_from:
            return False
        return True

    def applies_on(self, on_date):
        if self.valid_from > on_date:
            return False
        return self.valid_to is None or self.valid_to >= on_date

    @property
    def is_active(self):
        return self.applies_on(date.today())

    @property
    def audit_only(self):
        """True when no mode may write for this standard."""
        return not self.auto_remediable

    @property
    def scope_summary(self):
        """Human-readable scope, for tables and the API. "Everything" is a real answer."""
        parts = []
        for label, manager in (
            ('platform', self.platforms), ('role', self.roles),
            ('site', self.sites), ('tag', self.device_tags),
        ):
            names = [str(obj) for obj in manager.all()]
            if names:
                parts.append('%s: %s' % (label, ', '.join(names)))
        return '; '.join(parts) if parts else 'All devices'

    def get_check_type_color(self):
        return ConfigCheckTypeChoices.colors.get(self.check_type)

    def applies_to(self, device):
        """Is this device in scope? Every populated dimension must match.

        Prefer netbox_compliance.scoping.StandardResolver in bulk — this runs
        four queries per call and the fleet report renders a row per device.
        """
        if device is None:
            return False
        platform_ids = {p.pk for p in self.platforms.all()}
        if platform_ids and device.platform_id not in platform_ids:
            return False
        role_ids = {r.pk for r in self.roles.all()}
        if role_ids and device.role_id not in role_ids:
            return False
        site_ids = {s.pk for s in self.sites.all()}
        if site_ids and device.site_id not in site_ids:
            return False
        tag_ids = {t.pk for t in self.device_tags.all()}
        if tag_ids and not tag_ids & {t.pk for t in device.tags.all()}:
            return False
        return True


class ConfigCompliance(PrimaryModel):
    """One device measured against one standard.

    The row exists as soon as anything is known about the pairing, including
    "we have never checked this" — that state has to be reportable, and an
    exemption has to be recordable for a device nobody has scanned yet.

    What is stored about the finding is deliberately thin: a redacted `observed`
    excerpt and a `findings` object holding keys and redacted lines. Enough to
    answer "what exactly is wrong here?" without NetBox becoming a place where
    password hashes accumulate.
    """

    device = models.ForeignKey(
        to='dcim.Device', on_delete=models.CASCADE, related_name='config_compliance'
    )
    standard = models.ForeignKey(
        to=ConfigStandard, on_delete=models.CASCADE, related_name='results'
    )

    result = models.CharField(
        max_length=20,
        choices=ConfigCheckResultChoices,
        default=ConfigCheckResultChoices.RESULT_UNKNOWN,
        help_text='What the last check found',
    )

    observed = models.TextField(
        blank=True,
        help_text=(
            'Redacted excerpt of the governed configuration lines, as seen on the '
            'device. Secrets are removed by the checker before it is sent here.'
        ),
    )
    findings = models.JSONField(
        default=dict,
        blank=True,
        help_text='Structured detail: missing entries, unexpected entries, violating lines',
    )
    error_message = models.CharField(
        max_length=500, blank=True, help_text='Why the last check could not complete'
    )

    source = models.CharField(
        max_length=20,
        choices=ConfigCheckSourceChoices,
        default=ConfigCheckSourceChoices.SOURCE_MANUAL,
    )
    last_checked = models.DateTimeField(
        blank=True, null=True, help_text='When this device was last measured'
    )

    # --- Rollback reference. Captured immediately before a remediation writes,
    # so there is something to compare against when a change goes wrong. It is
    # the governed sections only, redacted — a full running-config would put
    # every secret on the box into the database, which is the one thing this
    # design will not do. The previous capture is not lost when a new one
    # overwrites it: PrimaryModel snapshots pre-change values into the
    # ObjectChange, so the changelog holds the history.
    pre_change_config = models.TextField(
        blank=True,
        help_text='Redacted governed configuration captured immediately before the last write',
    )
    pre_change_at = models.DateTimeField(blank=True, null=True)
    last_remediated = models.DateTimeField(
        blank=True, null=True, help_text='When configuration was last changed for this standard'
    )
    remediation_log = models.TextField(
        blank=True, help_text='Commands sent by the last remediation, redacted'
    )

    # --- Exemption, following netbox_refresh: a real answer, but never a silent
    # or permanent one. Who said so, when, and when it gets looked at again.
    exempt = models.BooleanField(
        default=False,
        help_text='Exclude from pass/fail, but keep showing it as exempt',
    )
    exempt_reason = models.TextField(blank=True)
    exempt_approved_by = models.CharField(
        max_length=100, blank=True,
        help_text='Free text — approvers are often not NetBox users',
    )
    exempt_approved_on = models.DateField(blank=True, null=True)
    exempt_review_by = models.DateField(
        blank=True, null=True, help_text='When this exemption should be revisited'
    )

    clone_fields = ('standard', 'source')

    class Meta:
        ordering = ('device', 'standard')
        verbose_name = 'config compliance'
        verbose_name_plural = 'config compliance'
        constraints = (
            models.UniqueConstraint(
                'device', 'standard',
                name='%(app_label)s_%(class)s_unique_device_standard',
                violation_error_message='That device already has a result for this standard.',
            ),
        )
        indexes = (
            models.Index(fields=('result',)),
            models.Index(fields=('exempt',)),
            models.Index(fields=('last_checked',)),
        )

    def __str__(self):
        return '%s — %s' % (self.device, self.standard.name if self.standard_id else '?')

    def get_absolute_url(self):
        return reverse('plugins:netbox_compliance:configcompliance', args=[self.pk])

    def clean(self):
        super().clean()
        if self.exempt and not (self.exempt_reason or '').strip():
            raise ValidationError(
                {'exempt_reason': 'Give a reason — an unexplained exemption cannot be reviewed.'}
            )
        if self.result == ConfigCheckResultChoices.RESULT_ERROR and not self.error_message.strip():
            raise ValidationError(
                {'error_message': 'Say what went wrong, or the row is unactionable.'}
            )

    # ------------------------------------------------------------------ #
    @property
    def status(self):
        """What a report shows: the exemption if there is one, else the result."""
        if self.exempt:
            if self.exemption_expired:
                return ConfigComplianceStatusChoices.STATUS_EXEMPT_EXPIRED
            return ConfigComplianceStatusChoices.STATUS_EXEMPT
        return self.result

    def get_status_display(self):
        labels = {entry[0]: entry[1] for entry in ConfigComplianceStatusChoices.CHOICES}
        return labels.get(self.status, self.status)

    def get_status_color(self):
        return ConfigComplianceStatusChoices.colors.get(self.status)

    def get_result_color(self):
        return ConfigCheckResultChoices.colors.get(self.result)

    def get_source_color(self):
        return ConfigCheckSourceChoices.colors.get(self.source)

    @property
    def exemption_expired(self):
        return bool(
            self.exempt and self.exempt_review_by and self.exempt_review_by < date.today()
        )

    # --- The three kinds of finding. Kept as properties so templates, the API
    # and the report all read them the same way, and a checker that posts a
    # partial findings object cannot make any of them explode.
    @property
    def missing_entries(self):
        """Entries the standard requires that the device does not have."""
        return list((self.findings or {}).get('missing') or [])

    @property
    def extra_entries(self):
        """Entries on the device that the standard does not name (exact_set only)."""
        return list((self.findings or {}).get('extra') or [])

    @property
    def violations(self):
        """Lines that match an `absent` standard's pattern — already redacted."""
        return list((self.findings or {}).get('violations') or [])

    @property
    def finding_count(self):
        return len(self.missing_entries) + len(self.extra_entries) + len(self.violations)

    @property
    def is_compliant(self):
        return self.result == ConfigCheckResultChoices.RESULT_COMPLIANT

    @property
    def needs_manual_fix(self):
        """Non-compliant, and no mode is allowed to fix it.

        The report's most actionable column: these are the rows a person has to
        go and do something about, as opposed to the ones the next `--update`
        run will clear.
        """
        return (
            self.result == ConfigCheckResultChoices.RESULT_NON_COMPLIANT
            and not self.exempt
            and self.standard_id is not None
            and not self.standard.auto_remediable
        )

    @property
    def age_days(self):
        stamp = self.last_checked or self.last_updated
        if stamp is None:
            return None
        return (timezone.now() - stamp).days

    @property
    def is_stale(self):
        """Is the result old enough that it should not be read at face value?

        Freshness is separate from the verdict on purpose. A device that passed
        three months ago is not a device that passes.
        """
        if self.result == ConfigCheckResultChoices.RESULT_UNKNOWN:
            return False  # "Not checked" already says everything
        days = self.age_days
        if days is None:
            return True
        return days > _plugin_settings().get('stale_after_days', 30)

    @property
    def stale_threshold(self):
        return timezone.now() - timedelta(
            days=_plugin_settings().get('stale_after_days', 30)
        )
