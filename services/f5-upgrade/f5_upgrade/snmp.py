"""Bounded SNMP v3 GETNEXT walks; no SSH or inventory dependency."""

import asyncio
import base64
import json
import os

from pysnmp.hlapi.v3arch import asyncio as snmp
from pysnmp.proto import rfc1902

from .snmp_models import SnmpWalkRequest
from .store import LostOwnership

MAX_RESULT_BYTES = 16 * 1024 * 1024


class WalkError(Exception):
    pass


def credentials(profile):
    def secret(name):
        value = os.environ.get(name or "", "")
        if len(value.encode()) < 8:
            raise WalkError("SNMP profile credential is missing or shorter than 8 bytes")
        return value

    protocols = {
        "SHA": snmp.usmHMACSHAAuthProtocol,
        "SHA224": snmp.usmHMAC128SHA224AuthProtocol,
        "SHA256": snmp.usmHMAC192SHA256AuthProtocol,
        "SHA384": snmp.usmHMAC256SHA384AuthProtocol,
        "SHA512": snmp.usmHMAC384SHA512AuthProtocol,
    }
    auth = profile.security_level != "noAuthNoPriv"
    private = profile.security_level == "authPriv"
    return snmp.UsmUserData(
        profile.username,
        authKey=secret(profile.auth_password_env) if auth else None,
        privKey=secret(profile.priv_password_env) if private else None,
        authProtocol=protocols[profile.auth_protocol] if auth else snmp.usmNoAuthProtocol,
        privProtocol=snmp.usmAesCfb128Protocol if private else snmp.usmNoPrivProtocol,
    )


def serialize(root, oid, value):
    encoding = "text"
    if isinstance(value, rfc1902.IpAddress):
        rendered = value.prettyPrint()
    elif isinstance(value, rfc1902.OctetString):
        # Preserve octets exactly, including non-UTF8 values and embedded NULs.
        rendered = base64.b64encode(value.asOctets()).decode("ascii")
        encoding = "base64"
    else:
        rendered = value.prettyPrint()
    return {
        "root_oid": root,
        "oid": str(oid),
        "type": type(value).__name__,
        "value": rendered,
        "encoding": encoding,
    }


class SnmpRunner:
    def __init__(self, settings, store, job, worker):
        self.settings, self.store, self.job, self.worker = settings, store, job, worker
        self.request = SnmpWalkRequest.model_validate(job["request"])
        self.result = {
            "rows": 0,
            "bytes": 0,
            "oids": [{"oid": oid, "status": "pending", "rows": 0} for oid in self.request.oids],
        }

    def record(self, step):
        self.store.record(self.job["id"], self.worker, step, result=self.result)

    def run(self):
        try:
            self.record("snmp_started")
            asyncio.run(self.walk())
            success = all(r["status"] == "succeeded" for r in self.result["oids"])
            self.record("snmp_finished")
            self.store.finish(self.job["id"], self.worker, "succeeded" if success else "failed")
        except LostOwnership:
            return
        except Exception:
            # Library exceptions can contain credential material; never persist their text.
            self.result["error"] = "SNMP worker failed; check server profile configuration"
            for item in self.result["oids"]:
                if item["status"] in ("pending", "running"):
                    item["status"] = "failed"
            try:
                self.record("snmp_failed")
                self.store.finish(self.job["id"], self.worker, "failed")
            except LostOwnership:
                pass

    async def walk(self):
        engine = snmp.SnmpEngine()
        try:
            async with asyncio.timeout(self.request.job_timeout):
                profile = self.settings.snmp_profiles[self.request.profile]
                auth = credentials(profile)
                transport = (
                    snmp.Udp6TransportTarget
                    if self.request.device_ip.version == 6
                    else snmp.UdpTransportTarget
                )
                target = await transport.create(
                    (str(self.request.device_ip), self.request.port),
                    timeout=self.request.timeout,
                    retries=self.request.retries,
                )
                for item in self.result["oids"]:
                    self.store.guard(self.job["id"], self.worker)
                    item["status"] = "running"
                    self.record("snmp_oid_started")
                    try:
                        await self.walk_oid(engine, auth, target, profile.context_name, item)
                        item["status"] = "succeeded"
                    except WalkError as exc:
                        item.update(status="failed", error=str(exc))
                    self.record("snmp_oid_finished")
                    if self.result.get("truncated"):
                        break
        except TimeoutError:
            self.result["error"] = "job timeout exceeded"
        finally:
            engine.close_dispatcher()
        for item in self.result["oids"]:
            if item["status"] in ("pending", "running"):
                item.update(
                    status="failed" if item["status"] == "running" else "skipped",
                    error=self.result.get("error", "job result limit reached"),
                )

    async def walk_oid(self, engine, auth, target, context, item):
        root = item["oid"]
        iterator = snmp.walk_cmd(
            engine,
            auth,
            target,
            snmp.ContextData(contextName=context),
            snmp.ObjectType(snmp.ObjectIdentity(root)),
            lookupMib=False,
            lexicographicMode=False,
            ignoreNonIncreasingOid=False,
        )
        try:
            async for indication, status, index, bindings in iterator:
                self.store.guard(self.job["id"], self.worker)
                if indication:
                    # Class names describe timeout/auth/transport failures without exposing secrets.
                    raise WalkError("SNMP engine error: " + type(indication).__name__)
                if status:
                    raise WalkError(f"SNMP PDU error {int(status)} at binding {int(index)}")
                rows = [serialize(root, oid, value) for oid, value in bindings]
                size = len(json.dumps(rows).encode())
                if (
                    self.result["rows"] + len(rows) > self.request.max_rows
                    or self.result["bytes"] + size > MAX_RESULT_BYTES
                ):
                    self.result["truncated"] = True
                    raise WalkError("job result limit reached")
                self.result["rows"] += len(rows)
                self.result["bytes"] += size
                item["rows"] += len(rows)
                self.store.append_snmp_rows(self.job["id"], self.worker, rows, self.result)
        finally:
            await iterator.aclose()
