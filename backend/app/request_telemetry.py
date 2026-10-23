"""Request IDs and real timings without URLs, queries, identities or document text."""
import json
import logging
import time
import uuid
from .llm import request_client


class RequestTelemetry:
    def __init__(self, app, runtime):
        self.app, self.runtime = app, runtime

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not (path == "/api" or path.startswith("/api/")):
            return await self.app(scope, receive, send)
        started, request_id, status = time.perf_counter(), uuid.uuid4().hex, 500
        context = request_client.set(getattr(self.runtime.state, "llm_client", None))

        async def measured_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.extend([(b"x-request-id", request_id.encode()),
                                (b"server-timing", f"app;dur={(time.perf_counter() - started) * 1000:.1f}".encode())])
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, measured_send)
        finally:
            request_client.reset(context)
            # The route template contains no document IDs, names, tokens or search terms.
            route = getattr(scope.get("route"), "path", "unmatched")
            logging.getLogger("uvicorn.error").info("hwp_request %s", json.dumps({
                "request_id": request_id, "method": scope.get("method"), "route": route,
                "status": status, "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }, separators=(",", ":")))
