"""Environment-driven diagnostic connection settings."""

import os

from pydantic import BaseModel, Field


class DiagnosticConfig(BaseModel):
    user_service_url: str = "http://127.0.0.1:8001"
    order_service_url: str = "http://127.0.0.1:8002"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "shop"
    postgres_user: str = "diagnostic_reader"
    postgres_password: str = Field(default="", repr=False)
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    docker_executor_read_url: str = "http://127.0.0.1:8005"
    docker_executor_read_token: str = Field(default="", repr=False)
    diagnostics_token: str = Field(default="", repr=False)
    http_timeout_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    postgres_timeout_seconds: float = Field(default=3, gt=0, le=30, allow_inf_nan=False)
    redis_timeout_seconds: float = Field(default=3, gt=0, le=30, allow_inf_nan=False)
    docker_executor_timeout_seconds: float = Field(
        default=5, gt=0, le=30, allow_inf_nan=False
    )

    @classmethod
    def from_env(cls) -> "DiagnosticConfig":
        return cls(
            **{
                key: os.environ[name]
                for key, name in {
                    "user_service_url": "USER_SERVICE_URL",
                    "order_service_url": "ORDER_SERVICE_URL",
                    "postgres_host": "POSTGRES_HOST",
                    "postgres_port": "POSTGRES_PORT",
                    "postgres_db": "POSTGRES_DB",
                    "postgres_user": "DIAGNOSTIC_POSTGRES_USER",
                    "postgres_password": "DIAGNOSTIC_POSTGRES_PASSWORD",
                    "redis_host": "REDIS_HOST",
                    "redis_port": "REDIS_PORT",
                    "docker_executor_read_url": "DOCKER_EXECUTOR_READ_URL",
                    "docker_executor_read_token": "DOCKER_EXECUTOR_READ_TOKEN",
                    "http_timeout_seconds": "DIAGNOSTIC_HTTP_TIMEOUT_SECONDS",
                    "postgres_timeout_seconds": "DIAGNOSTIC_POSTGRES_TIMEOUT_SECONDS",
                    "redis_timeout_seconds": "DIAGNOSTIC_REDIS_TIMEOUT_SECONDS",
                    "docker_executor_timeout_seconds": (
                        "DIAGNOSTIC_DOCKER_TIMEOUT_SECONDS"
                    ),
                    "diagnostics_token": "DIAGNOSTICS_TOKEN",
                }.items()
                if name in os.environ
            }
        )
