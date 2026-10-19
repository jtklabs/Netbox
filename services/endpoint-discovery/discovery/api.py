"""Versioned, scoped API for portals and remote collectors."""
import hmac
import ipaddress
import json
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .config import Settings, digest_token, read_access
from .models import Collection, Device, Observation, PollerHeartbeat, SchemaVersion
from .schemas import Report, mac_address
from .store import Conflict, database, heartbeat, ingest


class BodyLimit:
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > self.limit:
                await send({"type": "http.response.start", "status": 413,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"detail":"report too large"}'})
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay():
            return messages.pop(0) if messages else await receive()

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend([
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")])
            await send(message)

        await self.app(scope, replay, secure_send)


def create_app(settings=None):
    settings = settings or Settings.from_env()
    access = read_access(settings.access_file)
    engine, factory = database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="Endpoint Discovery", version="1.0.0", docs_url=None,
                  redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.engine, app.state.factory = engine, factory
    app.add_middleware(BodyLimit, limit=settings.max_body_bytes)
    bearer = HTTPBearer(auto_error=False)

    def authenticate(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials and credentials.scheme.lower() == "bearer":
            digest = digest_token(credentials.credentials)
            for principal in access.principals:
                if hmac.compare_digest(digest, principal.token_sha256):
                    return principal
        raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Bearer"})

    def reader(principal=Depends(authenticate)):
        if principal.role != "reader":
            raise HTTPException(403, "reader credentials required")
        return principal

    def poller(principal=Depends(authenticate)):
        if principal.role != "poller":
            raise HTTPException(403, "poller credentials required")
        return principal

    def scopes(principal, requested):
        if requested is not None and requested not in principal.scopes:
            raise HTTPException(403, "scope is not permitted")
        return [requested] if requested else principal.scopes

    def normalize(mac, ip):
        try:
            return mac_address(mac) if mac else None, str(ipaddress.ip_address(ip)) if ip else None
        except ValueError as exc:
            raise HTTPException(422, "invalid MAC or IP address") from exc

    def serialize(rows, session):
        # A bounded page, with one state lookup per device/context/kind.
        cache, items, now = {}, [], int(time.time())
        for row in rows:
            key = (row.device_key, row.context, row.kind)
            if key not in cache:
                cache[key] = session.get(Collection, key)
            state = cache[key]
            item = {c.name: getattr(row, c.name) for c in Observation.__table__.columns}
            item["stale"] = now - row.last_seen > settings.stale_seconds
            item["collection_status"] = state.status if state else "unknown"
            item["last_attempt"] = state.attempted_at if state else None
            items.append(item)
        return items

    @app.get("/healthz", tags=["health"])
    def health():
        try:
            with factory() as session:
                healthy = session.scalars(select(SchemaVersion.version)).all() == [1]
        except SQLAlchemyError:
            healthy = False
        if not healthy:
            raise HTTPException(503, "unhealthy")
        return {"healthy": True}

    @app.get("/v1/openapi.json", tags=["integration"])
    def openapi(principal=Depends(reader)):
        return app.openapi()

    @app.get("/v1/targets", tags=["poller"])
    def targets(principal=Depends(poller)):
        with factory.begin() as session:
            heartbeat(session, principal.name)
            rows = session.scalars(select(Device).where(
                Device.enabled.is_(True), Device.poller == principal.name,
                Device.scope.in_(principal.scopes)).order_by(Device.key))
            return {"items": [json.loads(d.config) for d in rows]}

    @app.get("/v1/pollers", tags=["portal"])
    def pollers(principal=Depends(reader)):
        now = int(time.time())
        with factory() as session:
            items = []
            for configured in access.principals:
                permitted = sorted(set(configured.scopes) & set(principal.scopes))
                if configured.role != "poller" or not permitted:
                    continue
                state = session.get(PollerHeartbeat, configured.name)
                items.append({"name": configured.name, "scopes": permitted,
                              "last_seen": state.last_seen if state else None,
                              "stale": state is None or now - state.last_seen > settings.stale_seconds})
            return {"items": items}

    @app.post("/v1/reports", tags=["poller"])
    def reports(report: Report, principal=Depends(poller)):
        try:
            return ingest(factory, principal, report, settings)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except IntegrityError as exc:
            # A UUID collision across devices racing in separate transactions.
            raise HTTPException(409, "report ID conflict; retry the original report") from exc

    @app.get("/v1/observations", tags=["portal"])
    def observations(
        scope: str | None = None, mac: str | None = None, ip: str | None = None,
        hostname: str | None = None, device_key: str | None = None,
        interface: str | None = None, vrf: str | None = None,
        vlan: int | None = Query(None, ge=0, le=4094), current: bool = True,
        since: int | None = Query(None, ge=0), until: int | None = Query(None, ge=0),
        after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000),
        principal=Depends(reader),
    ):
        mac, ip = normalize(mac, ip)
        query = select(Observation).where(Observation.scope.in_(scopes(principal, scope)),
                                          Observation.id > after_id)
        for field, value in (("mac", mac), ("ip", ip), ("hostname", hostname),
                             ("device_key", device_key), ("interface", interface),
                             ("vrf", vrf), ("vlan", vlan)):
            if value is not None:
                query = query.where(getattr(Observation, field) == value)
        if current:
            query = query.where(Observation.ended_at.is_(None))
        if since is not None:
            query = query.where(Observation.last_seen >= since)
        if until is not None:
            query = query.where(Observation.first_seen <= until)
        with factory() as session:
            rows = session.scalars(query.order_by(Observation.id).limit(limit + 1)).all()
            return {"items": serialize(rows[:limit], session),
                    "next_after_id": rows[limit - 1].id if len(rows) > limit else None}

    @app.get("/v1/locate", tags=["portal"])
    def locate(mac: str | None = None, ip: str | None = None, hostname: str | None = None,
               scope: str | None = None, vrf: str | None = None,
               limit: int = Query(200, ge=1, le=1000), principal=Depends(reader)):
        if sum(bool(v) for v in (mac, ip, hostname)) != 1:
            raise HTTPException(422, "supply exactly one of mac, ip, or hostname")
        mac, ip = normalize(mac, ip)
        base = select(Observation).where(Observation.scope.in_(scopes(principal, scope)),
                                         Observation.ended_at.is_(None))
        if vrf is not None:
            base = base.where(Observation.vrf == vrf)
        with factory() as session:
            if mac:
                matches = session.scalars(base.where(Observation.mac == mac)
                                          .order_by(Observation.id).limit(limit + 1)).all()
                truncated = len(matches) > limit
                matches = matches[:limit]
            else:
                neighbors = session.scalars(base.where(
                    Observation.kind == "neighbors",
                    Observation.ip == ip if ip else Observation.hostname == hostname,
                ).order_by(Observation.id).limit(limit + 1)).all()
                truncated = len(neighbors) > limit
                matches = neighbors[:limit]
                # Correlation never crosses tenant/site scopes or VRFs. Unknown
                # VLAN is retained as uncertainty, rather than invented.
                conditions = [
                    (Observation.scope == n.scope) & (Observation.vrf == n.vrf)
                    & (Observation.mac == n.mac)
                    & ((Observation.vlan.in_([0, n.vlan])) if n.vlan else text("1=1"))
                    for n in matches
                ]
                if conditions:
                    locations = session.scalars(base.where(Observation.kind == "fdb", or_(*conditions))
                                                .order_by(Observation.id).limit(limit + 1)).all()
                    truncated = truncated or len(locations) > limit
                    matches += locations[:limit]
            items = serialize(matches, session)
        locations = [i for i in items if i["kind"] == "fdb"]
        locations.sort(key=lambda i: (i["stale"], {"access": 0, "unknown": 1, "transit": 2}[i["port_role"]],
                                      -i["last_seen"], i["id"]))
        candidates = [i for i in locations if not i["stale"] and i["port_role"] != "transit"]
        # Multiple records on the same port are one location; results remain
        # evidence, never a guarantee of physical endpoint attachment.
        ports = {(i["scope"], i["vrf"], i["device_key"], i["ifindex"], i["vlan"]) for i in candidates}
        identities = {(i["scope"], i["vrf"], i["mac"]) for i in items}
        uncertain = (any(i["vlan"] == 0 for i in candidates)
                     or (not mac and any(i["stale"] for i in items if i["kind"] == "neighbors")))
        return {"locations": locations, "neighbors": [i for i in items if i["kind"] == "neighbors"],
                "ambiguous": truncated or len(ports) != 1 or len(identities) != 1 or uncertain,
                "truncated": truncated, "stale_seconds": settings.stale_seconds,
                "interpretation": "observed forwarding locations; transit ports are not endpoint attachment"}

    @app.get("/v1/collections", tags=["portal"])
    def collections(scope: str | None = None, device_key: str | None = None,
                    after_key: str = Query("", max_length=1000), limit: int = Query(100, ge=1, le=1000),
                    principal=Depends(reader)):
        # Stable string cursor avoids offset skipping during status updates.
        allowed = scopes(principal, scope)
        query = select(Collection).where(Collection.scope.in_(allowed))
        if device_key:
            query = query.where(Collection.device_key == device_key)
        if after_key:
            try:
                parts = json.loads(after_key)
                if not isinstance(parts, list) or len(parts) != 3 or not all(isinstance(p, str) for p in parts):
                    raise ValueError("expected three string cursor components")
                device, context, kind = parts
                query = query.where(or_(
                    Collection.device_key > device,
                    (Collection.device_key == device) & (Collection.context > context),
                    (Collection.device_key == device) & (Collection.context == context) & (Collection.kind > kind)))
            except (TypeError, ValueError) as exc:
                raise HTTPException(422, "invalid collection cursor") from exc
        with factory() as session:
            rows = session.scalars(query.order_by(Collection.device_key, Collection.context, Collection.kind)
                                   .limit(limit + 1)).all()
            items = [{c.name: getattr(row, c.name) for c in Collection.__table__.columns} for row in rows[:limit]]
        now = int(time.time())
        for item in items:
            item["stale"] = item["succeeded_at"] is None or now - item["succeeded_at"] > settings.stale_seconds
        cursor = json.dumps([rows[limit - 1].device_key, rows[limit - 1].context, rows[limit - 1].kind]) if len(rows) > limit else None
        return {"items": items, "next_after_key": cursor}

    return app
