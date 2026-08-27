"""Login and MOTD banners.

The text itself lives in `templates/<platform>/banner.j2`, so it is edited
where every other piece of platform syntax is edited and reviewed in the same
diff as the rest of a change.

Comparing a banner is not a set difference, so this feature brings its own
planner: it renders the template, pulls the body back out of the rendered
block, and compares it with the body read off the device. Whitespace either
side is ignored -- a device that re-wraps or re-indents nothing should not look
like a change every run -- but the text itself must match exactly.
"""

from __future__ import annotations

import argparse
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core import MODE_REPLACE, Desired, Entry, Feature, PlatformSupport, render
from ..standards import of as standards_of

SHOW_COMMAND = "show running-config | section ^banner"

#: The banners this feature knows how to write. `exec` and others can be added
#: by naming them here and handling them in the templates.
KINDS = ("motd", "login")

#: Lines that end a banner body. IOS renders its delimiter as `^C` (sometimes
#: doubled, as `^CC`, in the running config); EOS terminates with `EOF`.
TERMINATORS = ("EOF",)

IOS_SAMPLE = """\
banner motd ^CC

  Authorised access only. Activity is logged and monitored.

^C
"""

EOS_SAMPLE = """\
banner motd
Authorised access only. Activity is logged and monitored.
EOF
"""


def normalize_body(lines: Sequence[str]) -> str:
    """The comparable form of a banner: trailing whitespace and blank lines
    either end are not part of the message."""
    trimmed = [line.rstrip() for line in lines]
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return "\n".join(trimmed)


def _is_terminator(line: str, marker: str) -> bool:
    text = line.strip()
    if not text:
        return False
    if text in TERMINATORS:
        return True
    if not marker:
        return False
    # IOS writes `banner motd ^CC` and ends the body with `^C`, so accept the
    # marker with or without its final character.
    return text == marker or text == marker[:-1] or marker.startswith(text)


def parse_banners(output: str) -> List[Entry]:
    """One entry per configured banner, carrying its normalized body."""
    entries: List[Entry] = []
    lines = output.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line.startswith("banner "):
            index += 1
            continue
        tokens = line.split()
        kind = tokens[1] if len(tokens) > 1 else ""
        marker = tokens[2] if len(tokens) > 2 else ""

        body: List[str] = []
        index += 1
        while index < len(lines) and not _is_terminator(lines[index], marker):
            body.append(lines[index])
            index += 1
        index += 1  # step over the terminator

        text = normalize_body(body)
        entries.append(
            Entry(
                key=kind,
                line=f"banner {kind}",
                display=f"banner {kind} ({len(text.splitlines())} line(s))",
                data={"body": text},
            )
        )
    return entries


def desired_body(platform: str, kind: str, variables: Mapping[str, Any]) -> str:
    """Render the template and pull the body back out of it.

    Rendering and then re-parsing means the comparison uses exactly what would
    be sent, delimiters and all, rather than a second copy of the text kept
    somewhere for comparison purposes.
    """
    commands = render("banner", platform, [kind], [], variables, keep_blank=True)
    parsed = parse_banners("\n".join(commands))
    return parsed[0].data["body"] if parsed else ""


def plan_banner(
    current: Sequence[Entry],
    desired: Sequence[str],
    mode: str,
    context: Optional[Mapping[str, Any]] = None,
) -> Tuple[List[str], List[Entry]]:
    context = context or {}
    platform = context.get("platform", "")
    variables = context.get("variables") or {}
    configured = {entry.key: entry for entry in current}

    to_add: List[str] = []
    for kind in desired:
        entry = configured.get(kind)
        if entry is not None and entry.data.get("body") == desired_body(
            platform, kind, variables
        ):
            continue  # already exactly right
        to_add.append(kind)

    to_remove: List[Entry] = []
    if mode == MODE_REPLACE:
        to_remove = [entry for key, entry in configured.items() if key not in desired]
    return to_add, to_remove


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-b",
        "--banner",
        action="append",
        choices=KINDS,
        help="which banner(s) to manage; defaults to the banner section of the "
        "standards file",
    )


def build_desired(args: argparse.Namespace) -> Desired:
    standards = standards_of(args)
    if args.banner:
        kinds = list(dict.fromkeys(args.banner))
    else:
        section = standards.section("banner")
        kinds = [kind for kind in KINDS if section.get(kind)]
    if not kinds:
        raise ValueError(
            "no banners selected: pass --banner motd or set banner.motd: true "
            "in the standards file"
        )
    delimiter: Dict[str, Any] = {"delimiter": standards.value("banner.delimiter")}
    return Desired(keys=kinds, variables=delimiter)


FEATURE = Feature(
    name="banner",
    help="converge the login and MOTD banners",
    platforms={
        "cisco_ios": PlatformSupport(SHOW_COMMAND, parse_banners, IOS_SAMPLE),
        "arista_eos": PlatformSupport(SHOW_COMMAND, parse_banners, EOS_SAMPLE),
    },
    add_arguments=add_arguments,
    build_desired=build_desired,
    plan=plan_banner,
    keep_blank_lines=True,
    # The device stops prompting between the delimiters, so netmiko must not
    # wait for a prompt it will not get until the banner is finished.
    config_options={"cmd_verify": False},
    selftest_args=[],
)
