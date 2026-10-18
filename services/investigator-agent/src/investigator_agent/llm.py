"""Minimal client for OpenAI-compatible chat completions with tool calling.

Works with OpenAI, OpenRouter, Ollama, vLLM, LM Studio and other servers that
implement ``POST {base_url}/chat/completions``.
"""

import asyncio
import json
import uuid
from typing import Any, Protocol

import httpx
from pydantic import BaseModel

from investigator_agent.config import AgentConfig

RETRY_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_ERROR_DETAIL_CHARS = 200


class LLMError(Exception):
    """The language model could not produce a response."""


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = []
    usage: Usage = Usage()


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> LLMResponse: ...


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    return str(message)[:MAX_ERROR_DETAIL_CHARS] if message else ""


def parse_response(payload: Any) -> LLMResponse:
    try:
        message = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMError("LLM response has no message") from error
    tool_calls = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        arguments = function.get("arguments", "{}")
        # Some servers (e.g. Ollama) return arguments as an object.
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        tool_calls.append(
            ToolCall(
                id=raw.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                name=function.get("name", ""),
                arguments=arguments or "{}",
            )
        )
    usage = payload.get("usage") or {}
    return LLMResponse(
        content=message.get("content"),
        tool_calls=tool_calls,
        usage=Usage(
            prompt_tokens=usage.get("prompt_tokens") or 0,
            completion_tokens=usage.get("completion_tokens") or 0,
        ),
    )


class OpenAICompatibleClient:
    def __init__(
        self,
        config: AgentConfig,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay_seconds: float = 2.0,
    ) -> None:
        if not config.llm_model:
            raise LLMError("LLM_MODEL is not configured")
        self._config = config
        self._transport = transport
        self._retry_delay = retry_delay_seconds

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: str | dict[str, Any] = "auto",
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            **self._config.llm_extra_body,
            "model": self._config.llm_model,
            "messages": messages,
            "temperature": self._config.llm_temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        headers = {"Content-Type": "application/json"}
        if self._config.llm_api_key:
            headers["Authorization"] = f"Bearer {self._config.llm_api_key}"
        url = self._config.llm_base_url.rstrip("/") + "/chat/completions"
        attempts = self._config.llm_max_retries + 1
        async with httpx.AsyncClient(
            timeout=self._config.llm_timeout_seconds, transport=self._transport
        ) as client:
            for attempt in range(attempts):
                try:
                    response = await client.post(url, json=payload, headers=headers)
                except httpx.TimeoutException as error:
                    if attempt + 1 < attempts:
                        await asyncio.sleep(self._retry_delay * (attempt + 1))
                        continue
                    raise LLMError("LLM request timed out") from error
                except httpx.HTTPError as error:
                    raise LLMError("LLM request failed") from error
                if (
                    response.status_code in RETRY_STATUS_CODES
                    and attempt + 1 < attempts
                ):
                    await asyncio.sleep(self._retry_delay * (attempt + 1))
                    continue
                if response.status_code >= 400:
                    detail = _error_detail(response)
                    raise LLMError(
                        f"LLM API returned HTTP {response.status_code}"
                        + (f": {detail}" if detail else "")
                    )
                try:
                    return parse_response(response.json())
                except ValueError as error:
                    raise LLMError("LLM response is not valid JSON") from error
        raise LLMError("LLM request failed")
