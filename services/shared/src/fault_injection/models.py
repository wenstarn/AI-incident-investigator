from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DelayParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delay_ms: int = Field(strict=True, ge=0, le=10000)


def _validate_url(value: str, scheme: str, hosts: set[str], ports: set[int]) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Invalid URL") from error
    if (
        parsed.scheme != scheme
        or parsed.hostname not in hosts
        or port not in ports
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("URL is not allowed for this test environment")
    return value


class UserServiceURLParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str

    @field_validator("url")
    @classmethod
    def allowed_url(cls, value: str) -> str:
        result = _validate_url(
            value, "http", {"user-service", "localhost", "127.0.0.1"}, {8001, 9999}
        )
        if urlsplit(result).path not in ("", "/"):
            raise ValueError("URL path is not allowed")
        return result


class RedisURLParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str

    @field_validator("url")
    @classmethod
    def allowed_url(cls, value: str) -> str:
        result = _validate_url(
            value, "redis", {"redis", "localhost", "127.0.0.1"}, {6379, 9999}
        )
        if urlsplit(result).path not in ("", "/0"):
            raise ValueError("Redis database is not allowed")
        return result


PARAMETER_MODELS: dict[str, type[BaseModel]] = {
    "request_delay": DelayParameters,
    "user_service_url_override": UserServiceURLParameters,
    "redis_url_override": RedisURLParameters,
}


class FaultCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    parameters: dict[str, Any]
    ttl_seconds: int | None = Field(default=300, strict=True, gt=0, le=1800)


class Fault(BaseModel):
    fault_id: str
    type: str
    parameters: dict[str, Any]
    created_at: datetime
    expires_at: datetime | None
    status: Literal["active"] = "active"
