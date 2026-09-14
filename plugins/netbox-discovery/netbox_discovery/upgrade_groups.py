"""Redundancy groups and upgrade ordering.

A group is a set of devices of which at most `max_concurrent` may be upgrading
at once; a pair is a group with a limit of one. A dependency says an upstream
device waits for a downstream one, so a core does not go until the closets it
serves are done. Both can be entered by hand, and both are derived from NetBox
where NetBox already knows: FHRP (HSRP/VRRP) group assignments make groups,
and cables between devices of different role tiers make dependencies.
"""
from collections import defaultdict

from dcim.models import Cable, Device, Interface
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from ipam.models import FHRPGroupAssignment

from .models import UpgradeDependency, UpgradeGroup
from .utils import plugin_setting


class GroupError(ValueError):
    pass


def memberships(devices):
    """Group names per device id, for a set of devices."""
    result = {device.pk: [] for device in devices}
    for group in UpgradeGroup.objects.filter(members__in=devices).distinct().prefetch_related('members'):
        for member in group.members.all():
            if member.pk in result:
                result[member.pk].append(group.name)
    return {pk: sorted(names) for pk, names in result.items()}


def downstream_of(devices):
    """Downstream device ids per device id: what each device must wait for.

    Device-level dependencies (cabled or manual) plus the members of every
    group that one of the device's groups waits for.
    """
    result = {device.pk: set() for device in devices}
    for dependency in UpgradeDependency.objects.filter(upstream__in=devices):
        result[dependency.upstream_id].add(dependency.downstream_id)
    groups = UpgradeGroup.objects.filter(members__in=devices).distinct().prefetch_related('members', 'depends_on__members')
    for group in groups:
        waits = {member.pk for other in group.depends_on.all() for member in other.members.all()}
        for member in group.members.all():
            if member.pk in result:
                result[member.pk] |= waits - {member.pk}
    return {pk: sorted(ids) for pk, ids in result.items()}


def plan_waves(rows):
    """Assign a planned wave to each row ({'device', 'groups', 'waits_for'}).

    Only dependencies between the selected devices order the waves; a
    dependency on a device outside the batch is recorded but never waited for.
    A group limit of 0 places every member in the same wave. Raises GroupError
    when the dependencies form a cycle.
    """
    names = {name for row in rows for name in row['groups']}
    limits = {group.name: group.max_concurrent for group in UpgradeGroup.objects.filter(name__in=names)}
    in_batch = {row['device'].pk for row in rows}
    remaining, done, wave = list(rows), set(), 0
    while remaining:
        wave += 1
        counts, chosen = defaultdict(int), []
        for row in remaining:
            if any(dep in in_batch and dep not in done for dep in row['waits_for']):
                continue
            if any(limits.get(name, 1) and counts[name] + 1 > limits.get(name, 1) for name in row['groups']):
                continue
            for name in row['groups']:
                counts[name] += 1
            chosen.append(row)
        if not chosen:
            stuck = ', '.join(str(row['device']) for row in remaining[:5])
            raise GroupError(f'Upgrade dependencies form a cycle among the selected devices ({stuck}); fix the group or device dependencies first.')
        for row in chosen:
            row['wave'] = wave
            done.add(row['device'].pk)
        remaining = [row for row in remaining if row not in chosen]
    return rows


def fhrp_group_name(group, devices):
    label = group.get_protocol_display() if hasattr(group, 'get_protocol_display') else str(group.protocol)
    address = group.ip_addresses.first() if hasattr(group, 'ip_addresses') else None
    detail = str(address.address.ip) if address is not None else group.name or ', '.join(sorted(d.name for d in devices))
    return f'{label} {group.group_id} {detail}'[:100]


@transaction.atomic
def refresh_discovered():
    """Derive groups from FHRP assignments and dependencies from cables.

    Discovered objects are updated in place by key; ones no longer seen are
    marked stale rather than deleted, so a scheduled batch keeps its ordering.
    Manual groups and dependencies are never touched.
    """
    counts = {'groups': 0, 'dependencies': 0, 'stale_groups': 0, 'stale_dependencies': 0}
    interface_type = ContentType.objects.get_for_model(Interface)
    by_group = defaultdict(set)
    for assignment in FHRPGroupAssignment.objects.filter(interface_type=interface_type).select_related('group'):
        interface = Interface.objects.filter(pk=assignment.interface_id).select_related('device').first()
        if interface is not None and interface.device_id:
            by_group[assignment.group].add(interface.device)
    seen = set()
    for fhrp, devices in by_group.items():
        if len(devices) < 2:
            continue
        key = f'fhrp:{fhrp.pk}'
        seen.add(key)
        group = UpgradeGroup.objects.filter(key=key).first()
        if group is None:
            name = fhrp_group_name(fhrp, devices)
            if UpgradeGroup.objects.filter(name=name).exists():
                name = f'{name[:90]} #{fhrp.pk}'
            group = UpgradeGroup(name=name, source='fhrp', key=key)
            group.save()
        elif group.stale:
            group.stale = False
            group.save()
        group.members.set(devices)
        counts['groups'] += 1
    counts['stale_groups'] = UpgradeGroup.objects.filter(source='fhrp').exclude(key__in=seen).update(stale=True)

    tiers = list(plugin_setting('upgrade_tier_roles') or [])
    rank = {slug: index for index, slug in enumerate(tiers)}
    seen = set()
    for cable in Cable.objects.prefetch_related('terminations'):
        ends = {'A': [], 'B': []}
        for termination in cable.terminations.all():
            target = termination.termination
            if isinstance(target, Interface) and target.device_id:
                ends[termination.cable_end].append(target.device)
        for a in ends['A']:
            for b in ends['B']:
                ra, rb = rank.get(getattr(a.role, 'slug', None)), rank.get(getattr(b.role, 'slug', None))
                if ra is None or rb is None or ra == rb or a.pk == b.pk:
                    continue
                upstream, downstream = (a, b) if ra < rb else (b, a)
                key = f'cable:{upstream.pk}:{downstream.pk}'
                if key in seen:
                    continue
                seen.add(key)
                dependency = UpgradeDependency.objects.filter(key=key).first()
                if dependency is None:
                    dependency = UpgradeDependency.objects.filter(upstream=upstream, downstream=downstream).first()
                if dependency is None:
                    UpgradeDependency.objects.create(upstream=upstream, downstream=downstream, source='cable', key=key)
                elif dependency.source != 'manual' and (dependency.stale or dependency.key != key):
                    dependency.stale, dependency.key = False, key
                    dependency.save()
                counts['dependencies'] += 1
    counts['stale_dependencies'] = UpgradeDependency.objects.filter(source='cable').exclude(key__in=seen).update(stale=True)
    return counts
