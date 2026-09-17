"""What a device is called in NetBox, given the hostname it reports.

sysName is frequently an FQDN, and NetBox device names are conventionally the
short name. The first rule this scanner had was the obvious one -- keep the
first label, "sw1.corp.example.com" becomes "sw1" -- and it is wrong wherever
the hostname itself contains dots: "sw1.floor2.corp.example.com" and
"sw1.floor3.corp.example.com" both became "sw1", and the second could never
be created beside the first.

So the domains to remove are said out loud instead. The Discovery plugin
holds a list (Discovery > Stripped Domains) that every poller reads at the
start of a run:

    list holds corp.example.com
        sw1.corp.example.com          -> sw1
        sw1.floor2.corp.example.com   -> sw1.floor2
        sw1.floor2                    -> sw1.floor2      (nothing to strip)
        sw1.other.net                 -> sw1.other.net   (not on the list)

A domain comes off only at a label boundary, together with the dot that
joined it, and only from the end. The longest match wins, so listing both
"example.com" and "corp.example.com" does what it looks like. A name that is
nothing but a listed domain is left alone rather than emptied.

With NO domains listed the old first-label rule still applies, so a NetBox
without the plugin, or one where nobody has set this up, behaves exactly as
it always did. Listing the first domain is therefore a switch, not just an
entry: from then on dots are kept, and a hostname under a domain that is NOT
on the list keeps that domain. List every domain the fleet uses, and look at
a --dry-run before the first real sweep.
"""

from __future__ import annotations

import logging
from typing import Iterable, Sequence

from .netbox import NetBox, NetBoxError

log = logging.getLogger(__name__)

STRIPPED_DOMAINS_ENDPOINT = "/plugins/discovery/stripped-domains/"


def normalise_domain(value) -> str:
    """A list entry as it is compared: lowercase, no surrounding dots or space.

    ".Corp.Example.com." and "corp.example.com" are the same entry; the dot
    that joins a domain to a hostname is inferred, never typed.
    """
    return str(value or "").strip().strip(".").strip().lower()


def first_label(name: str) -> str:
    return (name or "").strip().split(".")[0]


def strip_domain(name: str, domains: Sequence[str]) -> str:
    """`name` with the longest listed domain removed from its end."""
    cleaned = (name or "").strip().rstrip(".")
    lowered = cleaned.lower()
    for domain in sorted({normalise_domain(d) for d in domains} - {""}, key=len, reverse=True):
        suffix = "." + domain
        if lowered.endswith(suffix) and len(cleaned) > len(suffix):
            return cleaned[: -len(suffix)]
    return cleaned


def device_name(hostname: str, domains: Sequence[str] = ()) -> str:
    """The NetBox name for a reported hostname under the current rule."""
    if not domains:
        return first_label(hostname)
    return strip_domain(hostname, domains)


def derivations(hostname: str, domains: Sequence[str] = ()) -> tuple[str, ...]:
    """Every name any naming rule could have given this hostname.

    The first label, the hostname in full, and the hostname less a listed
    domain. A NetBox record whose name is one of these was named by the
    scanner, under whichever rule was in force when it was created; a name
    that is none of them was typed by a person. The sync uses that to
    recognise a record across a change of rule, and to take a newly listed
    domain off a name it gave -- never to lengthen one, and never to touch a
    name somebody chose.
    """
    cleaned = (hostname or "").strip().rstrip(".")
    if not cleaned:
        return ()
    seen, names = set(), []
    for candidate in (first_label(cleaned), cleaned, strip_domain(cleaned, domains)):
        key = candidate.lower()
        if candidate and key not in seen:
            seen.add(key)
            names.append(candidate)
    return tuple(names)


def load_stripped_domains(netbox: NetBox) -> tuple[str, ...]:
    """Read the enabled domains from the Discovery plugin.

    A 404 is a NetBox without the plugin, or with one older than this
    poller: no list, so the first-label rule applies, and that is not worth
    a log line on every run. Any other failure is said out loud, because a
    list that exists and is silently ignored would name devices differently
    from one run to the next.
    """
    try:
        items = netbox.all(STRIPPED_DOMAINS_ENDPOINT, {"enabled": "true"})
    except NetBoxError as exc:
        if " -> 404" in str(exc):
            log.debug("no stripped-domain list: %s", exc)
        else:
            log.warning("could not read the stripped-domain list, so hostnames are "
                        "trimmed at the first dot this run: %s", exc)
        return ()
    domains = _unique(normalise_domain(item.get("domain")) for item in items)
    if domains:
        log.info("stripping %d domain(s) from hostnames: %s", len(domains), ", ".join(domains))
    return domains


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen, out = set(), []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)
