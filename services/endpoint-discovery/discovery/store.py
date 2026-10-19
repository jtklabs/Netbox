import hashlib
import json
import time

from sqlalchemy import case, create_engine, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import sessionmaker

from .models import Base, Collection, Device, Observation, PollerHeartbeat, Receipt, SchemaVersion
from .schemas import Report, Target


class Conflict(ValueError):
    pass


def database(url):
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    return engine, sessionmaker(engine, expire_on_commit=False)


def heartbeat(session, name):
    now = int(time.time())
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    session.execute(insert(PollerHeartbeat).values(name=name, last_seen=now).on_conflict_do_update(
        index_elements=[PollerHeartbeat.name],
        set_={"last_seen": case((PollerHeartbeat.last_seen < now, now), else_=PollerHeartbeat.last_seen)}))


def migrate(engine):
    # Run as a deployment job, never from web worker startup. PostgreSQL advisory
    # lock serializes deploys. Refuse unknown versions instead of guessing.
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(729410021)")
        Base.metadata.create_all(connection)
        versions = connection.execute(select(SchemaVersion.version)).scalars().all()
        if versions and versions != [1]:
            raise RuntimeError("unsupported database schema version")
        if not versions:
            connection.execute(SchemaVersion.__table__.insert().values(version=1))


def provision(factory, targets: list[Target], replace=False):
    if len({t.key for t in targets}) != len(targets):
        raise ValueError("device keys must be unique")
    with factory.begin() as session:
        if replace:
            for device in session.scalars(select(Device).with_for_update()):
                device.enabled = False
        for target in targets:
            target = target.model_copy()
            target.revision = hashlib.sha256(json.dumps(
                target.model_dump(exclude={"revision"}), sort_keys=True).encode()).hexdigest()
            device = session.get(Device, target.key, with_for_update=True)
            if device is None:
                device = Device(key=target.key)
                session.add(device)
            if device.scope is not None and device.scope != target.scope:
                raise ValueError("device scope is immutable; use a new device key after a scope change")
            device.scope, device.poller = target.scope, target.poller
            device.config, device.enabled = target.model_dump_json(), True
            for context in target.contexts:
                for kind in context.tables:
                    if session.get(Collection, (target.key, context.key, kind)) is None:
                        session.add(Collection(device_key=target.key, context=context.key, kind=kind,
                                               scope=target.scope, poller=target.poller, attempted_at=0,
                                               status="pending", count=0))


def _key(entry):
    return entry.mac, entry.ip, entry.vlan, entry.ifindex


def ingest(factory, principal, report: Report, settings, now=None):
    now = int(time.time()) if now is None else now
    if not now - settings.max_report_age_days * 86400 <= report.observed_at <= now + 300:
        raise ValueError("report timestamp outside the permitted replay window")
    digest = hashlib.sha256(report.model_dump_json().encode()).hexdigest()
    with factory.begin() as session:
        device = session.scalar(select(Device).where(Device.key == report.device_key).with_for_update())
        if (device is None or not device.enabled or device.poller != principal.name
                or device.scope not in principal.scopes):
            raise PermissionError("target is not assigned to this poller")
        receipt = session.get(Receipt, report.report_id)
        if receipt:
            if receipt.principal != principal.name or receipt.digest != digest:
                raise Conflict("report ID already used with a different payload")
            return {"accepted": True, "duplicate": True}
        target = Target.model_validate_json(device.config)
        if report.target_revision != target.revision:
            raise Conflict("target assignment changed; refresh targets before collecting")
        context = next((c for c in target.contexts if c.key == report.context), None)
        if context is None or any(t.kind not in context.tables for t in report.tables):
            raise PermissionError("context or table is not assigned to this poller")
        ignored = []
        for table in report.tables:
            identity = (device.key, context.key, table.kind)
            state = session.get(Collection, identity)
            if state is not None and report.observed_at <= state.attempted_at:
                ignored.append(table.kind)
                continue
            if state is None:
                state = Collection(device_key=device.key, context=context.key, kind=table.kind,
                                   scope=device.scope, poller=principal.name)
                session.add(state)
            state.attempted_at = report.observed_at
            state.poller, state.status, state.error = principal.name, table.status, table.error
            if table.status != "complete":
                continue
            normalized = {}
            for entry in table.entries:
                entry = entry.model_copy()
                if context.vlan:
                    if entry.vlan not in (0, context.vlan):
                        raise ValueError("VLAN is outside the assigned context")
                    entry.vlan = context.vlan
                key = _key(entry)
                if key in normalized and normalized[key] != entry:
                    raise ValueError("conflicting duplicate observation")
                normalized[key] = entry
            count = len(normalized)
            active = session.scalars(select(Observation).where(
                Observation.device_key == device.key, Observation.context == context.key,
                Observation.kind == table.kind, Observation.ended_at.is_(None))).all()
            for old in active:
                key = _key(old)
                entry = normalized.get(key)
                role = target.interface_roles.get(entry.interface, entry.port_role) if entry else None
                if (entry is None or old.interface != entry.interface or old.port_role != role
                        or old.hostname != entry.hostname or old.vrf != context.vrf
                        or old.device_name != target.name or old.netbox_device_id != target.netbox_device_id
                        or old.netbox_interface_id != target.interface_ids.get(entry.interface)):
                    old.ended_at = report.observed_at
                else:
                    old.last_seen = report.observed_at
                    del normalized[key]
            session.flush()  # close old intervals before inserting replacement metadata
            for entry in normalized.values():
                session.add(Observation(
                    scope=device.scope, device_key=device.key, device_name=target.name,
                    netbox_device_id=target.netbox_device_id,
                    netbox_interface_id=target.interface_ids.get(entry.interface),
                    context=context.key, vrf=context.vrf, kind=table.kind,
                    **entry.model_dump(exclude={"port_role"}),
                    port_role=target.interface_roles.get(entry.interface, entry.port_role),
                    first_seen=report.observed_at, last_seen=report.observed_at, ended_at=None,
                ))
            state.succeeded_at, state.count = report.observed_at, count
        session.add(Receipt(id=report.report_id, principal=principal.name, digest=digest, received_at=now))
    return {"accepted": True, "duplicate": False, "ignored_tables": ignored}


def prune(factory, settings, now=None):
    now = int(time.time()) if now is None else now
    cutoff = now - settings.retention_days * 86400
    with factory.begin() as session:
        # Expired current sightings are retired by age; failed polls do not
        # create an inferred disappearance time. last_seen remains the evidence.
        history = session.execute(delete(Observation).where(Observation.last_seen < cutoff)).rowcount
        receipts = session.execute(delete(Receipt).where(
            Receipt.received_at < now - (settings.max_report_age_days + 1) * 86400)).rowcount
    return {"observations_deleted": history, "receipts_deleted": receipts}
