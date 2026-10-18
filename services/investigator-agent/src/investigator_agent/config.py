"""Environment-driven agent settings. The API key is never included in repr."""

import json
import os
from typing import Any

from pydantic import BaseModel, Field, field_validator

ENV_NAMES = {
    "llm_base_url": "LLM_BASE_URL",
    "llm_api_key": "LLM_API_KEY",
    "llm_model": "LLM_MODEL",
    "llm_timeout_seconds": "LLM_TIMEOUT_SECONDS",
    "llm_temperature": "LLM_TEMPERATURE",
    "llm_max_retries": "LLM_MAX_RETRIES",
    "llm_extra_body": "LLM_EXTRA_BODY",
    "max_steps": "AGENT_MAX_STEPS",
    "max_tool_output_chars": "AGENT_MAX_TOOL_OUTPUT_CHARS",
    "mas_max_review_rounds": "MAS_MAX_REVIEW_ROUNDS",
    "mas_specialist_max_steps": "MAS_SPECIALIST_MAX_STEPS",
}


class AgentConfig(BaseModel):
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=120, gt=0, le=900)
    llm_temperature: float = Field(default=0, ge=0, le=2)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    # Provider-specific request fields, e.g. {"reasoning_effort": "none"}.
    llm_extra_body: dict[str, Any] = Field(default_factory=dict)
    max_steps: int = Field(default=12, ge=1, le=50)
    max_tool_output_chars: int = Field(default=6000, ge=500, le=60000)
    mas_max_review_rounds: int = Field(default=2, ge=0, le=5)
    mas_specialist_max_steps: int = Field(default=6, ge=1, le=30)

    @field_validator("llm_extra_body", mode="before")
    @classmethod
    def _parse_extra_body(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = json.loads(value)
            if not isinstance(value, dict):
                raise ValueError("LLM_EXTRA_BODY must be a JSON object")
        return value

    @classmethod
    def from_env(cls) -> "AgentConfig":
        return cls(
            **{
                key: os.environ[name]
                for key, name in ENV_NAMES.items()
                if os.environ.get(name, "") != ""
            }
        )
