"""Machine-readable contract for the read-only integration API."""


def openapi():
    query = [
        {"name": "target", "in": "query", "schema": {"type": "string"}, "description": "Numeric IPv4/IPv6 destination; omit for all destinations."},
        {"name": "since", "in": "query", "schema": {"type": "number", "minimum": 0}, "description": "Inclusive UTC Unix timestamp in seconds."},
        {"name": "until", "in": "query", "schema": {"type": "number", "minimum": 0}, "description": "Inclusive UTC Unix timestamp in seconds; must be >= since."},
        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}},
        {"name": "before_id", "in": "query", "schema": {"type": "integer", "minimum": 1}, "description": "Use next_before_id from the previous page with the same filters."},
    ]
    error = {"description": "Authentication required; send Authorization: Bearer KEY."}
    paths = {}
    for name, description in (("events", "Confirmed path changes, hop movements, reply loss/recovery, and baselines."),
                              ("traces", "Parsed traceroute observations, including errors and timeouts.")):
        paths["/api/" + name] = {"get": {"operationId": "list_" + name, "summary": description,
            "parameters": query, "responses": {"200": {"description": "Newest first, ordered by insertion ID.",
                "content": {"application/json": {"schema": {"type": "object", "required": ["items", "next_before_id"],
                    "properties": {"items": {"type": "array", "items": {"$ref": "#/components/schemas/" + ("Event" if name == "events" else "Trace")}},
                                   "next_before_id": {"type": "integer", "nullable": True}}}}}},
                "400": {"description": "Invalid filters or pagination."}, "401": error}}}
    paths["/api/status"] = {"get": {"operationId": "get_status", "summary": "Current destinations, latest traces, accepted paths, settings and collector health.",
        "responses": {"200": {"description": "Current collector snapshot. Each target's latest observation includes raw_output: original traceroute stdout for clipboard/export, or null until a matching live trace is collected. Command text is held only for the latest trace in memory and is absent from retained history.", "content": {"application/json": {"schema": {"type": "object"}}}}, "401": error}}}
    paths["/api/events.csv"] = {"get": {"operationId": "export_events", "summary": "One page of events as CSV; structured fields are JSON cells.", "parameters": query,
        "responses": {"200": {"description": "CSV report.", "content": {"text/csv": {"schema": {"type": "string"}}}}, "400": {"description": "Invalid filters or pagination."}, "401": error}}}
    paths["/healthz"] = {"get": {"operationId": "health", "security": [], "summary": "Public health boolean only; no route data.",
        "responses": {str(status): {"description": description, "content": {"application/json": {"schema": {"type": "object", "properties": {"healthy": {"type": "boolean"}}, "additionalProperties": False}}}}
                      for status, description in ((200, "Collector is healthy."), (503, "Collector is not healthy."))}}}
    return {"openapi": "3.0.3", "info": {"title": "Path monitor API", "version": "1.0.0",
        "description": "Read-only route history. The configured Bearer token grants access to all configured targets and retained history. Use HTTPS. Never put tokens in URLs. IDs are pagination cursors within retained history, not permanent synchronization offsets."},
        "servers": [{"url": "../"}], "security": [{"bearerAuth": []}], "paths": paths,
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}}, "schemas": {
            "Trace": {"type": "object", "required": ["id", "target", "started", "hops", "reached", "error"], "properties": {
                "id": {"type": "integer"}, "target": {"type": "string"}, "started": {"type": "number", "description": "Trace start, Unix seconds UTC."},
                "duration_ms": {"type": "number"}, "reached": {"type": "boolean"}, "error": {"type": "string", "nullable": True},
                "protocol": {"type": "string", "enum": ["icmp", "tcp"], "description": "Probe method; absent in observations recorded before protocol support (ICMP)."},
                "port": {"type": "integer", "minimum": 1, "maximum": 65535, "nullable": True, "description": "TCP destination port; null for ICMP."},
                "hops": {"type": "array", "items": {"type": "object", "properties": {"ttl": {"type": "integer"}, "addresses": {"type": "array", "items": {"type": "string"}},
                    "rtt_ms": {"type": "array", "items": {"type": "number"}}, "timeouts": {"type": "integer"}, "annotations": {"type": "array", "items": {"type": "string"}}}}}}},
            "Event": {"type": "object", "required": ["id", "target", "started", "kind", "observed"], "properties": {
                "id": {"type": "integer"}, "target": {"type": "string"}, "started": {"type": "number", "description": "Confirming trace start, Unix seconds UTC."},
                "kind": {"type": "string", "enum": ["baseline", "path_change", "reachability"]},
                "observed": {"type": "object", "description": "Confirming observation; same observation fields as Trace without database id/target."},
                "hop_moves": {"type": "array", "items": {"type": "object"}},
                "last_reached_observation": {"type": "object", "nullable": True}, "first_observed_at": {"type": "number"},
                "evidence_window": {"type": "array", "items": {"type": "object"}}}}}}}
