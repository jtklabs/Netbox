"""Reading a Cisco IOS configuration, and deciding what to do about it.

Everything in this module is pure: text in, verdicts and command plans out. No
sockets, no NetBox, no netmiko. That is deliberate — it is the half that decides
whether a production switch gets a `no username` sent to it, and it needs to be
testable without a switch anywhere near it. `ios_standards.py` does the I/O and
calls in here for every decision.

Three things live here:

  parsing      indentation-aware, because `password 7 ...` inside a `line vty`
               block is a different thing from one at top level, and a removal
               has to enter the block before it can negate the line.
  redaction    applied to everything that leaves this process — printed,
               logged, or posted to NetBox. Several of these standards match
               lines whose whole point is that they contain a secret.
  planning     what to add, what to remove, and — as important — what was
               refused and why.

The redaction rule is deliberately blunt: anything that looks like a credential
is replaced whether or not this tool understands the command it appeared in. A
redactor that only handles the syntax it was taught fails silently on the line
nobody anticipated, and that failure ends with a password hash in a database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = (
    'ConfigLine',
    'parse_config',
    'redact',
    'redact_config',
    'Evaluation',
    'evaluate',
    'Plan',
    'plan_remediation',
    'render_template',
    'REDACTED',
)

REDACTED = '<redacted>'

# --------------------------------------------------------------------------- #
# Redaction
# --------------------------------------------------------------------------- #
# Order matters: the typed form (`password 7 <hash>`) is replaced first, and the
# untyped rules carry a negative lookahead so they do not then chew the type
# digit off what is left. Every rule keeps the identifying half of the line —
# which command, which account — because a finding nobody can act on is not
# worth reporting.
_REDACTIONS = (
    # `password 7 070C285F4D06`, `secret 9 $14$...`, `key-string 7 ...`
    (re.compile(r'(?i)\b(password|secret|key-string|key)(\s+\d+\s+)(\S+)'),
     lambda m: '%s%s%s' % (m.group(1), m.group(2), REDACTED)),
    # `password cleartext`, `secret cleartext` — no type digit. The lookahead
    # keeps this off anything the rule above already handled.
    (re.compile(r'(?i)\b(password|secret)(\s+)(?!\d+\s)(\S+)'),
     lambda m: '%s%s%s' % (m.group(1), m.group(2), REDACTED)),
    # SNMP community strings are passwords that everyone forgets are passwords.
    (re.compile(r'(?i)\b(snmp-server\s+community)(\s+)(\S+)'),
     lambda m: '%s%s%s' % (m.group(1), m.group(2), REDACTED)),
    (re.compile(r'(?i)\b(pre-shared-key|wpa-psk\s+(?:ascii|hex))(\s+\d*\s*)(\S+)'),
     lambda m: '%s%s%s' % (m.group(1), m.group(2), REDACTED)),
    # `neighbor 10.0.0.1 password ...` is covered above; this catches the
    # authentication-key spellings that do not use the word "password".
    (re.compile(r'(?i)\b(authentication-key|message-digest-key\s+\d+\s+md5)(\s+\d*\s*)(\S+)'),
     lambda m: '%s%s%s' % (m.group(1), m.group(2), REDACTED)),
)


def redact(line):
    """Strip anything credential-shaped from one configuration line.

    Idempotent: redacting an already-redacted line changes nothing, which
    matters because the same text passes through here more than once on its way
    from a device to a report.
    """
    if line is None:
        return ''
    text = line
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def redact_config(text):
    """Redact every line of a configuration block."""
    return '\n'.join(redact(line) for line in (text or '').splitlines())


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
@dataclass
class ConfigLine:
    """One line of running-config, with enough context to act on it.

    `parent` is the enclosing block's line — `line vty 0 4` for the password
    indented under it — and it is what makes a removal correct: sending
    `no password 7 ...` at global config level does nothing useful, while
    entering the block first does.
    """

    text: str            # the line, trailing whitespace stripped, indent kept
    stripped: str        # the same line with leading whitespace removed
    indent: int
    number: int          # 1-based, for pointing somebody at the right place
    parent: str = ''     # the enclosing block's stripped text, '' at top level

    @property
    def context(self):
        return self.parent


def parse_config(text):
    """Turn running-config text into ConfigLines, tracking block nesting.

    IOS marks structure with indentation and nothing else, so the parse is an
    indentation stack. Comment lines (`!`) and blank lines are dropped: they
    carry no configuration and would otherwise show up in `observed` output as
    noise. Banner bodies are not special-cased — a banner containing something
    that looks like config is a known limitation, noted in the README rather
    than half-handled here.
    """
    lines = []
    stack = []  # (indent, stripped text) of the blocks we are inside
    for number, raw in enumerate((text or '').splitlines(), start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith('!'):
            continue
        indent = len(line) - len(line.lstrip())

        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else ''

        lines.append(ConfigLine(
            text=line, stripped=stripped, indent=indent, number=number, parent=parent,
        ))
        stack.append((indent, stripped))
    return lines


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
# Must behave identically to netbox_compliance.models.render_template. The two
# cannot import each other — one runs inside NetBox, the other on a poller with
# no NetBox on it — so tests/test_templates.py pins them together.
_TEMPLATE_VARIABLE = re.compile(r'\{([a-z_][a-z0-9_]*)\}', re.IGNORECASE)


def render_template(template, values):
    """Substitute {name} placeholders, returning (text, missing_names).

    Deliberately not str.format(): the template is operator-authored text being
    rendered into a command sent to a switch, and format() would happily walk
    the object graph for `{key.__class__}` or build a gigabyte of padding for
    `{0:>999999999}`. Unknown placeholders are left in the text rather than
    raising, so a caller can show exactly which variable was not supplied — and
    so a half-rendered command is visibly half-rendered rather than being sent.
    """
    missing = []

    def substitute(match):
        name = match.group(1)
        value = values.get(name)
        if value in (None, ''):
            missing.append(name)
            return match.group(0)
        return str(value)

    return _TEMPLATE_VARIABLE.sub(substitute, template or ''), missing


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
CHECK_ABSENT = 'absent'
CHECK_PRESENT = 'present'
CHECK_EXACT_SET = 'exact-set'

RESULT_COMPLIANT = 'compliant'
RESULT_NON_COMPLIANT = 'non-compliant'


@dataclass
class ObservedEntry:
    """One governed line found on the device, and what the pattern captured."""

    key: str
    line: ConfigLine
    groups: dict = field(default_factory=dict)

    @property
    def redacted(self):
        return redact(self.line.stripped)


@dataclass
class Evaluation:
    """What one standard found on one device."""

    standard: dict
    result: str = RESULT_COMPLIANT
    observed: list = field(default_factory=list)     # ObservedEntry
    missing: list = field(default_factory=list)      # expected keys not present
    extra: list = field(default_factory=list)        # ObservedEntry not expected
    violations: list = field(default_factory=list)   # ObservedEntry that must not exist

    @property
    def compliant(self):
        return self.result == RESULT_COMPLIANT

    @property
    def observed_text(self):
        """The governed lines, redacted, as they would be stored in NetBox.

        Only the governed lines — never the whole configuration. The report has
        to show what is wrong; it does not need, and must not hold, a copy of
        every secret on the device.
        """
        return '\n'.join(entry.redacted for entry in self.observed)

    def governed_capture(self):
        """The governed lines with their enclosing blocks, redacted.

        This is the rollback reference: enough to see what the device had
        before a write, without being a copy of the running configuration. A
        line indented under `line vty 0 4` is shown under it, because the block
        is part of what the line means and part of how you would put it back.
        """
        out = []
        last_context = None
        for entry in self.observed:
            context = entry.line.context
            if context and context != last_context:
                out.append(context)
            last_context = context
            prefix = ' ' if context else ''
            out.append('%s%s' % (prefix, entry.redacted))
        return '\n'.join(out)

    def findings(self):
        """The structured findings, in the shape the NetBox API expects."""
        return {
            'missing': list(self.missing),
            'extra': [
                {'key': e.key, 'line': e.redacted, 'context': e.line.context}
                for e in self.extra
            ],
            'violations': [
                {'line': e.redacted, 'context': e.line.context, 'line_number': e.line.number}
                for e in self.violations
            ],
        }

    @property
    def finding_count(self):
        return len(self.missing) + len(self.extra) + len(self.violations)


def _entries(standard):
    """expected_entries, always as [{'key', 'vars'}] — the API sends both shapes."""
    normalised = []
    for item in standard.get('entries') or standard.get('expected_entries') or []:
        if isinstance(item, str):
            normalised.append({'key': item, 'vars': {}})
        elif isinstance(item, dict) and item.get('key'):
            normalised.append({'key': item['key'], 'vars': item.get('vars') or {}})
    return normalised


def evaluate(standard, config_lines):
    """Measure one device's configuration against one standard.

    `search`, not `match`: a pattern like `(?:^|\\s)password\\s+7\\s+\\S+` has to
    be able to anchor mid-line, which is what catches
    `username x privilege 15 password 7 ...` and an indented
    `password 7 ...` with one expression instead of three.
    """
    pattern = re.compile(standard['match_pattern'])
    check_type = standard['check_type']
    evaluation = Evaluation(standard=standard)

    for line in config_lines:
        found = pattern.search(line.stripped)
        if not found:
            continue
        groups = {k: v for k, v in (found.groupdict() or {}).items() if v is not None}
        key = groups.get('key') or line.stripped
        evaluation.observed.append(ObservedEntry(key=key, line=line, groups=groups))

    expected = _entries(standard)

    if check_type == CHECK_ABSENT:
        # Every match is a violation; the pattern IS the rule.
        evaluation.violations = list(evaluation.observed)
        evaluation.result = (
            RESULT_COMPLIANT if not evaluation.violations else RESULT_NON_COMPLIANT
        )
        return evaluation

    seen_keys = {entry.key for entry in evaluation.observed}

    if check_type == CHECK_PRESENT:
        # The pattern finds candidate lines so `observed` can show what is
        # there; the entry key is compared against the line itself, which is
        # what "an exact line must appear" means.
        for entry in expected:
            if entry['key'] not in seen_keys:
                evaluation.missing.append(entry['key'])
        evaluation.result = (
            RESULT_COMPLIANT if not evaluation.missing else RESULT_NON_COMPLIANT
        )
        return evaluation

    if check_type == CHECK_EXACT_SET:
        expected_keys = [entry['key'] for entry in expected]
        evaluation.missing = [key for key in expected_keys if key not in seen_keys]
        wanted = set(expected_keys)
        evaluation.extra = [e for e in evaluation.observed if e.key not in wanted]
        evaluation.result = (
            RESULT_COMPLIANT
            if not evaluation.missing and not evaluation.extra
            else RESULT_NON_COMPLIANT
        )
        return evaluation

    raise ValueError('Unknown check type %r on standard %r'
                     % (check_type, standard.get('name')))


# --------------------------------------------------------------------------- #
# Remediation planning
# --------------------------------------------------------------------------- #
MODE_AUDIT = 'audit'
MODE_UPDATE = 'update'
MODE_ENFORCE = 'enforce'

# Privilege level at or above which a local account can recover a box. An
# account below this cannot re-enable one that was removed, so losing the last
# one is losing the switch.
RECOVERY_PRIVILEGE = 15


@dataclass
class Command:
    """One configuration command, in both the form that is sent and the form
    that is shown.

    `text` may contain a secret — that is the entire reason `display` exists.
    Nothing outside the SSH write path may use `text`: not the plan printout,
    not the log posted to NetBox, not an exception message.
    """

    text: str
    display: str
    kind: str            # 'add' or 'remove'
    entry: str           # the key this relates to
    context: str = ''    # enclosing block to enter first, '' at top level

    def as_sent(self):
        """The command sequence, including entering any enclosing block.

        A `password 7 ...` under `line vty 0 4` cannot be negated from global
        config mode. Sending the parent line first is not decoration.
        """
        if not self.context:
            return [self.text]
        return [self.context, self.text, 'exit']

    def as_shown(self):
        if not self.context:
            return [self.display]
        return [self.context, self.display, 'exit']


@dataclass
class Blocked:
    """Something the plan refused to do, and why. Always reported, never silent."""

    what: str
    why: str


@dataclass
class Plan:
    """What a run would do to one device for one standard."""

    standard: dict
    evaluation: Evaluation
    mode: str
    add: list = field(default_factory=list)
    remove: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    # Filled in by the runner when it actually writes: the redacted governed
    # config as it was immediately before, and the redacted commands it sent.
    # Both end up on the NetBox compliance record as the rollback reference.
    pre_change_config: str = ''
    sent_display: str = ''

    @property
    def commands(self):
        """Adds first, always.

        The order is a safety property, not a style choice. If a run is both
        creating the account the standard wants and removing the one it does
        not, doing it the other way round means a failure between the two steps
        leaves a switch with no local login at all.
        """
        return self.add + self.remove

    @property
    def will_change(self):
        return bool(self.add or self.remove)

    def display_lines(self):
        lines = []
        for command in self.commands:
            lines.extend(command.as_shown())
        return lines


def _privilege(entry):
    """The privilege level a governed line declares, or None if it did not.

    IOS defaults an account with no explicit `privilege` to level 1, but that
    is only knowable if the standard's pattern captured the group at all. None
    means "this standard does not tell us", which is treated as not-privileged
    for counting and is why the blocked-adds guard exists as a backstop.
    """
    raw = entry.groups.get('privilege')
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def plan_remediation(standard, evaluation, mode, secrets=None, session_user=None):
    """Work out the commands for one standard on one device, and what is refused.

    Everything that is not done is recorded in `blocked` with a reason. A plan
    that silently drops a remediation is indistinguishable from a plan that had
    nothing to do, and the difference is exactly what an operator needs to see.
    """
    secrets = secrets or {}
    plan = Plan(standard=standard, evaluation=evaluation, mode=mode)

    if evaluation.compliant or mode == MODE_AUDIT:
        return plan

    if not standard.get('auto_remediable', True):
        plan.blocked.append(Blocked(
            what='everything for "%s"' % standard['name'],
            why=_summarise_notes(standard.get('remediation_notes')),
        ))
        return plan

    _plan_adds(plan, standard, evaluation, secrets)

    removals_allowed = mode == MODE_ENFORCE and standard.get('allow_enforce')
    if (evaluation.violations or evaluation.extra) and not removals_allowed:
        reason = (
            'removing configuration needs enforce mode'
            if standard.get('allow_enforce')
            else 'enforce is not enabled for this standard in NetBox'
        )
        plan.blocked.append(Blocked(
            what='%d removal(s) for "%s"'
                 % (len(evaluation.violations) + len(evaluation.extra), standard['name']),
            why=reason,
        ))
        return plan

    if removals_allowed:
        _plan_removals(plan, standard, evaluation, session_user)
    return plan


def _summarise_notes(notes, limit=140):
    """The first sentence of a standard's guidance, for a one-line log entry.

    The full text belongs on the standard's page in NetBox, where there is room
    for it. Printing all of it against every device turns a run's output into a
    wall nobody reads, which is how the one line that mattered gets missed.
    """
    text = ' '.join((notes or '').split())
    if not text:
        return 'this standard is audit-only'
    sentence = text.split('. ')[0].rstrip('.')
    if len(sentence) > limit:
        sentence = sentence[:limit].rsplit(' ', 1)[0] + '...'
    return '%s (see the standard in NetBox for the rest)' % sentence


def _plan_adds(plan, standard, evaluation, secrets):
    """One command per missing entry — or one blocked entry, never a half-command."""
    if not evaluation.missing:
        return
    template = (standard.get('add_template') or '').strip()
    if not template:
        plan.blocked.append(Blocked(
            what='%d addition(s) for "%s"' % (len(evaluation.missing), standard['name']),
            why='the standard has no add template',
        ))
        return

    by_key = {entry['key']: entry for entry in _entries(standard)}
    for key in evaluation.missing:
        values = {'key': key}
        values.update(by_key.get(key, {}).get('vars') or {})
        # Runtime values last, and the per-account set last of all, so a secret
        # supplied for this specific account beats a fleet-wide default.
        values.update(secrets.get('*') or {})
        values.update(secrets.get(key) or {})
        text, missing = render_template(template, values)
        if missing:
            plan.blocked.append(Blocked(
                what='adding "%s"' % key,
                why='no value for %s — supply it in the environment (see the README)'
                    % ', '.join(sorted(set(missing))),
            ))
            continue
        plan.add.append(Command(
            text=text, display=redact(text), kind='add', entry=key,
        ))


def _plan_removals(plan, standard, evaluation, session_user):
    """One command per offending entry, minus everything a guard refuses.

    Three guards, in the order they matter:

      blocked adds  if this run could not create an account the standard asks
                    for, it must not remove the ones that are there. That is
                    the case where a missing secret turns a tidy-up into a
                    lockout.
      session user  never remove the account this session authenticated as.
      last recovery removing the last privilege-15 local account leaves nobody
                    who can put one back. Evaluated against the state AFTER
                    this plan's additions, so replacing an old admin with a new
                    one in the same run is allowed — and refused if the new one
                    could not be built.
    """
    template = (standard.get('remove_template') or '').strip()
    candidates = list(evaluation.violations) + list(evaluation.extra)
    if not candidates:
        return
    if not template:
        plan.blocked.append(Blocked(
            what='%d removal(s) for "%s"' % (len(candidates), standard['name']),
            why='the standard has no remove template',
        ))
        return

    if any(b.what.startswith('adding ') for b in plan.blocked):
        plan.blocked.append(Blocked(
            what='%d removal(s) for "%s"' % (len(candidates), standard['name']),
            why=('an addition this standard needs could not be built, so removing '
                 'what is there now could leave the device short'),
        ))
        return

    # Post-change privilege-15 population: what survives, plus what we are about
    # to add that the standard declares as privileged.
    # Entries that survive this plan: everything not being removed, plus the
    # session account — which the guard below refuses to remove, so counting it
    # as going would make this needlessly refuse safe removals elsewhere.
    going = {
        id(entry) for entry in candidates
        if not (session_user and entry.key == session_user)
    }
    keep_privileged = {
        entry.key for entry in evaluation.observed
        if (_privilege(entry) or 0) >= RECOVERY_PRIVILEGE and id(entry) not in going
    }
    by_key = {entry['key']: entry for entry in _entries(standard)}
    for command in plan.add:
        declared = (by_key.get(command.entry, {}).get('vars') or {}).get('privilege')
        try:
            if declared is not None and int(declared) >= RECOVERY_PRIVILEGE:
                keep_privileged.add(command.entry)
        except (TypeError, ValueError):
            pass

    for entry in candidates:
        if session_user and entry.key == session_user:
            plan.blocked.append(Blocked(
                what='removing "%s"' % entry.key,
                why='this is the account this session is logged in as',
            ))
            continue

        privileged = (_privilege(entry) or 0) >= RECOVERY_PRIVILEGE
        if privileged and not keep_privileged:
            plan.blocked.append(Blocked(
                what='removing "%s"' % entry.key,
                why=('it is the last privilege-%d local account — removing it '
                     'leaves nobody who can put one back' % RECOVERY_PRIVILEGE),
            ))
            continue

        values = {'key': entry.key, 'line': entry.line.stripped}
        values.update(entry.groups)
        text, missing = render_template(template, values)
        if missing:
            plan.blocked.append(Blocked(
                what='removing "%s"' % entry.key,
                why='no value for %s' % ', '.join(sorted(set(missing))),
            ))
            continue
        plan.remove.append(Command(
            text=text, display=redact(text), kind='remove', entry=entry.key,
            context=entry.line.context,
        ))
