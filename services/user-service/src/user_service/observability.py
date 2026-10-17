import json
import logging
import sys
import threading
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response

SERVICE_NAME = "user-service"
REQUEST_ID_HEADER = "X-Request-ID"
_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_log_fields = (
    "event",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "operation",
    "query_id",
    "target",
    "exception_type",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "service": SERVICE_NAME,
            "level": record.levelname,
            "event": getattr(record, "event", "application_log"),
            "request_id": getattr(record, "request_id", get_request_id()),
            "message": record.getMessage(),
        }
        for field in _log_fields:
            if field != "event" and hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging() -> None:
    service_logger = logging.getLogger("user_service")
    if any(
        getattr(handler, "structured_json", False)
        for handler in service_logger.handlers
    ):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.structured_json = True  # type: ignore[attr-defined]
    handler.setFormatter(JsonFormatter())
    service_logger.handlers.clear()
    service_logger.addHandler(handler)
    service_logger.setLevel(logging.INFO)
    service_logger.propagate = False


def get_request_id() -> str:
    return _request_id.get()


def set_request_id(request_id: str) -> Token[str]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    _request_id.reset(token)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests_total = 0
        self._errors_total = 0

    def record_request(self) -> None:
        with self._lock:
            self._requests_total += 1

    def record_error(self) -> None:
        with self._lock:
            self._errors_total += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "requests_total": self._requests_total,
                "errors_total": self._errors_total,
                "cache_hits": 0,
                "cache_misses": 0,
            }


metrics = Metrics()
logger = logging.getLogger(__name__)


def install_http_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe_request(request: Request, call_next) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex
        token = set_request_id(request_id)
        started = perf_counter()
        metrics.record_request()
        logger.info(
            "HTTP request started",
            extra={
                "event": "http_request",
                "method": request.method,
                "path": request.url.path,
            },
        )
        try:
            response = await call_next(request)
        except Exception as error:
            metrics.record_error()
            logger.error(
                "HTTP request failed",
                extra={
                    "event": "http_response",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                    "exception_type": type(error).__name__,
                },
            )
            raise
        else:
            if response.status_code >= 500:
                metrics.record_error()
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "HTTP request completed",
                extra={
                    "event": "http_response",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                },
            )
            return response
        finally:
            reset_request_id(token)
