"""Small JSON logger for controller lifecycle and HTTP requests."""

import json
import logging
import sys
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, Request


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": "incident-controller",
            "level": record.levelname,
            "request_id": getattr(record, "request_id", None),
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }
        for field in ("method", "path", "status_code", "duration_ms", "incident_id"):
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload)


def install_observability(app: FastAPI) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("incident_controller")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "HTTP response",
            extra={
                "event": "http_response",
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
            },
        )
        return response
