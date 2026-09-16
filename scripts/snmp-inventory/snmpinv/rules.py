"""Fill in what a device does not report, using rules kept in NetBox.

Some facts never arrive over SNMP. A Firepower publishes no model, and the
manufacturer or platform is missing from plenty of appliances' ENTITY-MIB.
The scanner leaves those blank rather than guess -- a wrong model silently
becomes a wrong device type -- and the onboarding review then asks a person to
type the value, one request at a time. When the same gap turns up on every box
of one kind, typing it every time is the wrong trade, and a rule says it once:

    when <field> <contains|starts with|...> <value>, set <field> to <value>

Rules live in the Discovery plugin (Discovery > Rules) so they are edited in
one place and every poller reads the same set. A poller fetches them at the
start of a run and applies them to each scan result before it is reported or
written, on onboarding scans and on the sweep alike.

By default a rule fills a field only when the device left it empty. What the
device reports wins, exactly as a reviewer's model override does, unless the
rule is explicitly set to replace. Either way every value a rule supplied is
recorded on the scan under `rules_applied`, so the review page can show which
facts came from the device and which from a rule.

Matching is case-insensitive throughout. The plugin validates a rule when it
is saved; the checks here are for a NetBox older than the poller, and simply
skip a rule this build cannot apply.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Sequence

from .model import ScanResult
from .netbox import NetBox, NetBoxError

log = logging.getLogger(__name__)

RULES_ENDPOINT = "/plugins/discovery/rules/"

# What a rule may look at: everything on a DeviceRecord that reads as text,
# plus two host-level facts -- the verbatim sysDescr, which names the product
# family on most platforms, and the scanned address.
MATCH_FIELDS = ("name", "model", "manufacturer", "platform", "serial",
                "software_version", "sys_descr", "address")
# What a rule may set: the facts that decide what gets created, and the
# serial. The name is never blank (it falls back to the address) and has an
# override of its own.
SET_FIELDS = ("model", "manufacturer", "platform", "software_version", "serial")
OPERATORS = ("contains", "starts_with", "ends_with", "equals", "regex")
# A serial belongs to one box, so a rule that supplies one must pin one box:
# the device name or its address, matched exactly. Anything looser stamps the
# same serial on every device it matches, and the sync then refuses the
# second as a duplicate. And it may only fill a serial the device does not
# report, never replace one: a changed serial is how a hardware swap is
# detected, and a rule must not fake one.
SERIAL_MATCH_FIELDS = ("name", "address")


@dataclass(frozen=True)
class Rule:
    name: str
    match_field: str
    match_operator: str
    match_value: str
    set_field: str
    set_value: str
    only_if_blank: bool = True
    weight: int = 100

    def matches(self, values: dict[str, str]) -> bool:
        subject = values.get(self.match_field) or ""
        haystack = subject.casefold()
        needle = self.match_value.casefold()
        if self.match_operator == "contains":
            return needle in haystack
        if self.match_operator == "starts_with":
            return haystack.startswith(needle)
        if self.match_operator == "ends_with":
            return haystack.endswith(needle)
        if self.match_operator == "equals":
            return haystack.strip() == needle.strip()
        if self.match_operator == "regex":
            try:
                return re.search(self.match_value, subject, re.IGNORECASE) is not None
            except re.error:
                return False
        return False


@dataclass
class RuleApplication:
    """One value a rule supplied, kept so the substitution is visible."""

    rule: str
    device: str
    field: str
    value: str
    previous: str = ""

    def as_dict(self) -> dict:
        return {"rule": self.rule, "device": self.device, "field": self.field,
                "value": self.value, "previous": self.previous}


def validate_rule(rule: Rule) -> str:
    """Why this build cannot apply the rule, or "" when it can."""
    if rule.match_field not in MATCH_FIELDS:
        return "cannot match on %r" % rule.match_field
    if rule.match_operator not in OPERATORS:
        return "unknown operator %r" % rule.match_operator
    if not rule.match_value.strip():
        return "the value to match is empty"
    if rule.set_field not in SET_FIELDS:
        return "cannot set %r" % rule.set_field
    if not rule.set_value.strip():
        return "the value to set is empty"
    if rule.match_operator == "regex":
        try:
            re.compile(rule.match_value)
        except re.error as exc:
            return "bad regular expression: %s" % exc
    if rule.set_field == "serial":
        if rule.match_field not in SERIAL_MATCH_FIELDS or rule.match_operator != "equals":
            return ("a serial belongs to one device, so a rule setting one must "
                    "match the device name or address exactly")
        if not rule.only_if_blank:
            return "a rule may fill in a serial the device does not report, never replace one"
    return ""


def _choice(value) -> str:
    """NetBox renders a choice field as {"value": ..., "label": ...}."""
    if isinstance(value, dict):
        value = value.get("value")
    return str(value or "")


def rule_from_api(item: dict) -> Rule | None:
    """Build a Rule from the plugin's representation; None if it is unusable."""
    try:
        rule = Rule(
            name=str(item.get("name") or "").strip() or "rule %s" % item.get("id", "?"),
            match_field=_choice(item.get("match_field")),
            match_operator=_choice(item.get("match_operator")),
            match_value=str(item.get("match_value") or ""),
            set_field=_choice(item.get("set_field")),
            set_value=str(item.get("set_value") or "").strip(),
            only_if_blank=bool(item.get("only_if_blank", True)),
            weight=int(item.get("weight") or 100),
        )
    except (TypeError, ValueError, AttributeError) as exc:
        log.warning("ignoring a malformed discovery rule (%s): %r", exc, item)
        return None
    problem = validate_rule(rule)
    if problem:
        log.warning("ignoring discovery rule %r: %s", rule.name, problem)
        return None
    return rule


def load_rules(netbox: NetBox) -> list[Rule]:
    """Read the enabled rules from the Discovery plugin.

    A NetBox without the plugin, or with one older than this poller, answers
    404 here. That is a NetBox with no rules, not a failure, and is not worth
    a line in the log on every run. Anything else -- the token lacking view
    permission on rules, say -- is, because the rules exist and are silently
    not being applied.
    """
    try:
        items = netbox.all(RULES_ENDPOINT, {"enabled": "true"})
    except NetBoxError as exc:
        if " -> 404" in str(exc):
            log.debug("no discovery rules: %s", exc)
        else:
            log.warning("could not read discovery rules, so none apply this run: %s", exc)
        return []
    rules = [rule for rule in (rule_from_api(item) for item in items) if rule is not None]
    rules.sort(key=lambda rule: (rule.weight, rule.name.casefold()))
    if rules:
        log.info("%d discovery rule(s) in force", len(rules))
    return rules


def apply_rules(result: ScanResult, rules: Sequence[Rule]) -> list[RuleApplication]:
    """Apply the rules to every device in a scan result, in weight order.

    Each device is judged on its own -- stack members have their own names
    and models -- against the host-level facts they share. Rules see each
    other's work: one that sets the manufacturer from the name lets a later
    one set the platform from the manufacturer. Access points learned from a
    controller are left alone; the controller reports their model itself.
    """
    if not rules or not result.devices:
        return []
    facts = result.facts
    shared = {
        "sys_descr": facts.sys_descr if facts is not None else "",
        "address": result.host,
    }
    applied: list[RuleApplication] = []
    for device in result.devices:
        for rule in rules:
            current = (getattr(device, rule.set_field, "") or "").strip()
            if current and rule.only_if_blank:
                continue
            values = dict(shared)
            for name in MATCH_FIELDS:
                if name not in values:
                    values[name] = getattr(device, name, "") or ""
            if not rule.matches(values):
                continue
            if current == rule.set_value:
                # The device already says this; nothing was supplied.
                continue
            setattr(device, rule.set_field, rule.set_value)
            applied.append(RuleApplication(
                rule=rule.name, device=device.name, field=rule.set_field,
                value=rule.set_value, previous=current,
            ))
    result.rules_applied.extend(applied)
    return applied


def summarise(applied: Sequence[RuleApplication]) -> str:
    """One line for the log: which fields rules filled in, and from which rule."""
    return ", ".join("%s=%r (%s)" % (entry.field, entry.value, entry.rule)
                     for entry in applied)
