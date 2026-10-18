"""Load selected keys from the repository .env without overriding the process."""

import os
from collections.abc import Iterable
from pathlib import Path

DIAGNOSTIC_KEYS = frozenset(
    {
        "USER_SERVICE_URL",
        "ORDER_SERVICE_URL",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
        "DIAGNOSTIC_POSTGRES_USER",
        "DIAGNOSTIC_POSTGRES_PASSWORD",
        "REDIS_HOST",
        "REDIS_PORT",
        "DOCKER_EXECUTOR_READ_URL",
        "DOCKER_EXECUTOR_READ_TOKEN",
        "DIAGNOSTICS_TOKEN",
        "DIAGNOSTIC_HTTP_TIMEOUT_SECONDS",
        "DIAGNOSTIC_POSTGRES_TIMEOUT_SECONDS",
        "DIAGNOSTIC_REDIS_TIMEOUT_SECONDS",
        "DIAGNOSTIC_DOCKER_TIMEOUT_SECONDS",
    }
)
AGENT_KEYS = frozenset(
    {
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "LLM_TIMEOUT_SECONDS",
        "LLM_TEMPERATURE",
        "LLM_MAX_RETRIES",
        "LLM_EXTRA_BODY",
        "AGENT_MAX_STEPS",
        "AGENT_MAX_TOOL_OUTPUT_CHARS",
        "MAS_MAX_REVIEW_ROUNDS",
        "MAS_SPECIALIST_MAX_STEPS",
    }
)
HARNESS_KEYS = frozenset({"INCIDENT_CONTROLLER_URL", "INCIDENT_CONTROLLER_TOKEN"})


def repository_env_file() -> Path:
    return Path(__file__).resolve().parents[4] / ".env"


def load_env_file(path: Path, keys: Iterable[str]) -> None:
    allowed = set(keys)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in allowed or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ[key] = value
