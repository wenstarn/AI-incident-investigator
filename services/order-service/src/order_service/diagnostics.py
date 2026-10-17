"""Allowlisted, authenticated read-only dependency observations."""

import os
import secrets
import time
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from order_service.config import get_redis_timeout, get_user_service_timeout
from order_service.effective_config import (
    effective_redis_url,
    effective_user_service_url,
)
from order_service.redis_client import create_redis_client

router = APIRouter(
    prefix="/internal/diagnostics",
    include_in_schema=False,
)
ALLOWED_DEPENDENCIES = frozenset({"user-service", "redis"})


def authorize_diagnostics(
    x_diagnostics_token: Annotated[str | None, Header()] = None,
) -> None:
    expected = os.getenv("DIAGNOSTICS_TOKEN", "")
    fault_token = os.getenv("FAULT_INJECTION_TOKEN", "")
    if not expected or (fault_token and secrets.compare_digest(expected, fault_token)):
        raise HTTPException(503, "Diagnostics credential unavailable")
    if not x_diagnostics_token or not secrets.compare_digest(
        x_diagnostics_token, expected
    ):
        raise HTTPException(403, "Forbidden")


def _effective_url(request: Request, dependency: str) -> str:
    if dependency == "user-service":
        return effective_user_service_url(request)
    if dependency == "redis":
        return effective_redis_url(request)
    raise HTTPException(404, "Unknown dependency")


@router.get("/config/{dependency}", dependencies=[Depends(authorize_diagnostics)])
def dependency_config(request: Request, dependency: str) -> dict[str, object]:
    parsed = urlsplit(_effective_url(request, dependency))
    return {
        "dependency": dependency,
        "host": parsed.hostname,
        "port": parsed.port,
        "timeout_seconds": (
            get_user_service_timeout()
            if dependency == "user-service"
            else get_redis_timeout()
        ),
    }


@router.get("/dependencies/{dependency}", dependencies=[Depends(authorize_diagnostics)])
def check_dependency(request: Request, dependency: str) -> dict[str, object]:
    url = _effective_url(request, dependency)
    started = time.monotonic()
    if dependency == "user-service":
        try:
            with httpx.Client(
                base_url=url,
                timeout=get_user_service_timeout(),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.get("/users/0")
            return {
                "dependency": dependency,
                "reachable": True,
                "status_code": response.status_code,
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "error_type": "http_error" if response.status_code >= 500 else None,
            }
        except httpx.TimeoutException:
            error_type = "timeout"
        except httpx.ConnectError:
            error_type = "connection_refused"
        except httpx.RequestError:
            error_type = "request_error"
    else:
        client = create_redis_client(url)
        try:
            pong = client.ping()
            return {
                "dependency": dependency,
                "reachable": bool(pong),
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "error_type": None if pong else "unexpected_response",
            }
        except RedisTimeoutError:
            error_type = "timeout"
        except RedisConnectionError:
            error_type = "connection_refused"
        finally:
            client.close()
    return {
        "dependency": dependency,
        "reachable": False,
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
        "error_type": error_type,
    }
