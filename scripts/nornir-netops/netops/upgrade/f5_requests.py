"""Final WAF request-log gate; retain only request ID and UTC timestamp."""

from datetime import datetime, timezone
import time

from .. import cancellation
from . import f5
from ..errors import DeviceHTTPError

REQUESTS = ("/mgmt/tm/asm/events/requests?$select=id,requestDatetime"
            "&$top=1&$orderby=requestDatetime%20desc")
CLOCK = "date -u +%Y-%m-%dT%H:%M:%SZ"


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Request timestamp is missing")
    result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Request timestamp has no timezone")
    return result.astimezone(timezone.utc)


def device_time(client, timeout):
    return timestamp(f5.bash(client, CLOCK, timeout=timeout)).isoformat()


def mark_reconnected(client, before, plan):
    """Use the device clock once the target boot volume is confirmed reachable.

    A clock-read failure must not interrupt health checks or traffic restoration.
    The final gate retries it, using a later (conservative) boundary if necessary.
    """
    waf = before.get("waf", {})
    if waf.get("provisioned") and waf.get("policy_count", 0):
        record = plan.setdefault("waf_requests", {})
        try:
            record["after"] = device_time(client, client.timeout)
        except Exception:
            pass


def skip_reason(after):
    waf = after.get("waf", {})
    if not waf.get("provisioned"):
        return "WAF is not provisioned"
    count = waf.get("policy_count")
    if type(count) is not int or count < 0:
        raise ValueError("WAF policy count is unreadable; cannot skip request-log validation")
    if count == 0:
        return "WAF has no policies"
    if after.get("facts", {}).get("failover_state") == "standby":
        return "Unit is on standby"
    return None


def wait_for_request(client, after, plan, options, emit):
    record = plan.setdefault("waf_requests", {})
    reason = skip_reason(after)
    if reason:
        record.update(status="skipped", reason=reason)
        emit("waf_requests", f"Skipping WAF request-log check: {reason}")
        return
    timeout = plan.get("approved_profile", {}).get("validation_timeout")
    if timeout is None:
        timeout = options.validation_timeout
    deadline = time.monotonic() + timeout
    record.update(status="waiting", timeout_seconds=timeout, attempts=0)
    emit("waf_requests", "Waiting for one WAF request logged after the upgrade")
    while time.monotonic() < deadline:
        record["attempts"] += 1
        try:
            if not record.get("after"):
                record["after"] = device_time(client, min(client.timeout, deadline - time.monotonic()))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            document = client.request("GET", REQUESTS, timeout=min(client.timeout, remaining))
            items = document.get("items")
            if not isinstance(items, list):
                raise ValueError("Request-log response has no items list")
            # Validate timestamps locally even if the appliance ignores sorting.
            # No total-count threshold and no raw request/response content retained.
            match = None
            for row in items:
                if not isinstance(row, dict):
                    raise ValueError("Request-log response contains an invalid item")
                seen = timestamp(row.get("requestDatetime"))
                if seen > timestamp(record["after"]):
                    match = {"request_id": str(row.get("id", ""))[:100], "request_datetime": seen.isoformat()}
                    break
            record.pop("last_error", None)
            if match and time.monotonic() < deadline:
                record.update(status="passed", seconds_remaining=max(0, int(deadline - time.monotonic())), **match)
                emit("waf_requests", "Observed a new post-upgrade WAF request log; final check passed")
                return
        except Exception as exc:
            # Raw HTTP errors may contain logged request content. Only report a
            # fixed error category; Client handles token refresh on each attempt.
            record["last_error"] = (f"HTTP {exc.status_code}" if isinstance(exc, DeviceHTTPError)
                                    else type(exc).__name__)
        remaining = max(0, deadline - time.monotonic())
        record["seconds_remaining"] = int(remaining)
        detail = f"; last read failed: {record['last_error']}" if record.get("last_error") else ""
        emit("waf_requests", f"No confirmed new WAF request log yet; {int(remaining)}s left{detail}")
        if remaining:
            _sleep(min(max(options.poll_interval, 0.1), remaining))
    record.update(status="timed_out", seconds_remaining=0)
    raise TimeoutError(f"No post-upgrade WAF request log confirmed within {timeout}s; "
                       "traffic remains in its validated state. Check local WAF logging and incoming traffic.")


def _sleep(seconds):
    return cancellation.sleep(seconds, fallback=time.sleep)
