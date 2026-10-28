import hmac
import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from .config import Settings, under
from .models import JobRequest, Model, RecoveryResolution, RollbackRequest
from .snmp_models import SnmpResults, SnmpWalkRequest
from .store import Conflict, Store


class JobResponse(Model):
    id: str
    device: str
    lock_key: str
    lock_held: bool
    kind: Literal["upgrade", "rollback", "snmp_walk"]
    parent: str | None
    request: dict
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "recovery_required", "resolved"]
    created: float
    updated: float
    worker: str | None
    heartbeat: float | None
    result: dict
    idempotency_key: str | None


class EventResponse(Model):
    seq: int
    job: str
    time: float
    step: str
    data: dict


class PlanResponse(Model):
    request: JobRequest
    steps: list[str]
    lock_key: str


def create_app(settings=None):
    os.umask(0o077)
    settings = settings or Settings.load()
    store = Store(settings.data_dir / "jobs.sqlite3")
    app = FastAPI(title="F5 SSH Upgrade API", version="1.1.0", docs_url="/docs", redoc_url=None)
    app.state.store = store
    bearer = HTTPBearer(auto_error=False)

    def auth(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if not credentials or not hmac.compare_digest(
            credentials.credentials.encode(), settings.token.encode()
        ):
            raise HTTPException(401, "valid bearer token required", headers={"WWW-Authenticate": "Bearer"})

    authenticated = [Depends(auth)]

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "not found"}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    def lock_key(device_id):
        if device_id not in settings.devices:
            raise HTTPException(422, "unknown inventory device")
        d = settings.devices[device_id]
        return "group:" + d.lock_group if d.lock_group else "device:" + d.id

    def planned(request):
        key = lock_key(request.device)
        if request.backup_directory:
            try:
                under(settings.backup_root, request.backup_directory)
            except ValueError:
                raise HTTPException(422, "backup directory escapes BACKUP_ROOT") from None
        return PlanResponse(request=request, steps=request.plan(), lock_key=key)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok", "api_version": "v1"}

    @app.get("/api/v1/devices", dependencies=authenticated)
    def devices() -> list[dict]:
        return [
            {"id": d.id, "host": d.host, "port": d.port, "lock_group": d.lock_group}
            for d in settings.devices.values()
        ]

    @app.post("/api/v1/plan", dependencies=authenticated, response_model=PlanResponse)
    def plan(request: JobRequest):
        return planned(request)

    @app.post("/api/v1/jobs", dependencies=authenticated, status_code=202, response_model=JobResponse)
    def submit(
        request: JobRequest,
        idempotency_key: Annotated[str | None, Header(min_length=1, max_length=160)] = None,
    ):
        p = planned(request)
        return store.submit(request.model_dump(), p.lock_key, idempotency_key)

    @app.post("/api/v1/snmp/walks", dependencies=authenticated, status_code=202, response_model=JobResponse)
    def submit_walk(
        request: SnmpWalkRequest,
        idempotency_key: Annotated[str | None, Header(min_length=1, max_length=160)] = None,
    ):
        if request.profile not in settings.snmp_profiles:
            raise HTTPException(422, "unknown SNMP v3 profile")
        return store.submit(
            request.model_dump(mode="json"),
            f"snmp:{request.device_ip}:{request.port}",
            idempotency_key,
            kind="snmp_walk",
        )

    @app.get("/api/v1/snmp/walks/{job_id}", dependencies=authenticated, response_model=JobResponse)
    def walk_job(job_id: str):
        store.reap(settings.stale_seconds)
        result = store.get(job_id)
        if result["kind"] != "snmp_walk":
            raise HTTPException(404, "SNMP job not found")
        return result

    @app.get("/api/v1/snmp/walks/{job_id}/results", dependencies=authenticated, response_model=SnmpResults)
    def walk_results(
        job_id: str,
        after: int = Query(default=0, ge=0),
        limit: int = Query(default=500, ge=1, le=1000),
    ):
        store.reap(settings.stale_seconds)
        return store.snmp_results(job_id, after, limit)

    @app.get("/api/v1/jobs", dependencies=authenticated, response_model=list[JobResponse])
    def jobs(limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
        store.reap(settings.stale_seconds)
        return store.list(limit, offset)

    @app.get("/api/v1/jobs/{job_id}", dependencies=authenticated, response_model=JobResponse)
    def job(job_id: str):
        store.reap(settings.stale_seconds)
        return store.get(job_id)

    @app.get("/api/v1/jobs/{job_id}/events", dependencies=authenticated, response_model=list[EventResponse])
    def events(
        job_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=1000)
    ):
        return store.events(job_id, after, limit)

    @app.post("/api/v1/jobs/{job_id}/cancel", dependencies=authenticated, response_model=JobResponse)
    def cancel(job_id: str):
        store.get(job_id)
        return store.cancel(job_id)

    @app.post("/api/v1/jobs/{job_id}/resolve", dependencies=authenticated, response_model=JobResponse)
    def resolve(job_id: str, resolution: RecoveryResolution):
        return store.resolve(job_id, resolution.model_dump())

    @app.post(
        "/api/v1/jobs/{job_id}/rollback",
        dependencies=authenticated,
        status_code=202,
        response_model=JobResponse,
    )
    def rollback(
        job_id: str,
        request: RollbackRequest,
        idempotency_key: Annotated[str | None, Header(min_length=1, max_length=160)] = None,
    ):
        original = store.get(job_id)
        if original["kind"] == "snmp_walk":
            raise Conflict("SNMP walks have no rollback target")
        payload = {**request.model_dump(), "device": original["device"]}
        return store.submit(
            payload, lock_key(original["device"]), idempotency_key, kind="rollback", parent=job_id
        )

    @app.get("/api/v1/jobs/{job_id}/backup", dependencies=authenticated)
    def backup(job_id: str):
        result = store.get(job_id)["result"].get("backup")
        if not result:
            raise HTTPException(404, "no completed backup")
        path = under(settings.backup_root, result["path"])
        if not path.is_file():
            raise HTTPException(404, "backup file is unavailable")
        return FileResponse(path, filename=job_id + ".ucs", media_type="application/octet-stream")

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    return app
