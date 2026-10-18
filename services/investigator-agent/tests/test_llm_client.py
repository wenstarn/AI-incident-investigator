import asyncio
import json

import httpx
import pytest
from investigator_agent.config import AgentConfig
from investigator_agent.llm import LLMError, OpenAICompatibleClient

SECRET = "sk-test-secret"


def config(**overrides) -> AgentConfig:
    values = {
        "llm_base_url": "http://llm.test/v1/",
        "llm_api_key": SECRET,
        "llm_model": "test-model",
        "llm_max_retries": 0,
    }
    return AgentConfig(**{**values, **overrides})


def completion(message: dict, usage: dict | None = None) -> dict:
    payload = {"choices": [{"message": message}]}
    if usage is not None:
        payload["usage"] = usage
    return payload


def client_for(handler, **overrides) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        config(**overrides),
        transport=httpx.MockTransport(handler),
        retry_delay_seconds=0,
    )


def test_request_format_and_tool_call_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=completion(
                {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "check_redis",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {"prompt_tokens": 12, "completion_tokens": 3},
            ),
        )

    tools = [{"type": "function", "function": {"name": "check_redis"}}]
    response = asyncio.run(
        client_for(handler).complete([{"role": "user", "content": "hi"}], tools)
    )
    assert seen["url"] == "http://llm.test/v1/chat/completions"
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["body"]["model"] == "test-model"
    assert seen["body"]["temperature"] == 0
    assert seen["body"]["tools"] == tools
    assert seen["body"]["tool_choice"] == "auto"
    assert response.tool_calls[0].name == "check_redis"
    assert response.usage.total_tokens == 15


def test_object_arguments_and_missing_ids_are_normalized():
    def handler(request):
        return httpx.Response(
            200,
            json=completion(
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "get_service_status",
                                "arguments": {"service": "redis"},
                            }
                        }
                    ],
                }
            ),
        )

    response = asyncio.run(client_for(handler).complete([], []))
    call = response.tool_calls[0]
    assert json.loads(call.arguments) == {"service": "redis"}
    assert call.id.startswith("call_")
    assert response.usage.total_tokens == 0


def test_extra_body_is_merged_but_cannot_override_core_fields():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=completion({"content": "ok"}))

    extra = {"reasoning_effort": "none", "model": "other"}
    asyncio.run(client_for(handler, llm_extra_body=extra).complete([], []))
    assert seen["body"]["reasoning_effort"] == "none"
    assert seen["body"]["model"] == "test-model"


def test_extra_body_from_env_must_be_object(monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"reasoning_effort": "none"}')
    assert AgentConfig.from_env().llm_extra_body == {"reasoning_effort": "none"}
    monkeypatch.setenv("LLM_EXTRA_BODY", "[1]")
    with pytest.raises(ValueError):
        AgentConfig.from_env()


def test_no_tools_omits_tool_fields_and_no_key_omits_header():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=completion({"content": "ok"}))

    asyncio.run(client_for(handler, llm_api_key="").complete([], []))
    assert "tools" not in seen["body"] and "tool_choice" not in seen["body"]
    assert seen["auth"] is None


def test_retries_transient_errors():
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=completion({"content": "ok"}))

    response = asyncio.run(client_for(handler, llm_max_retries=1).complete([], []))
    assert response.content == "ok" and len(attempts) == 2


def test_http_error_reports_status_without_secret():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})

    with pytest.raises(LLMError) as error:
        asyncio.run(client_for(handler).complete([], []))
    assert "401" in str(error.value) and "invalid api key" in str(error.value)
    assert SECRET not in str(error.value)


def test_timeout_is_llm_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMError, match="timed out"):
        asyncio.run(client_for(handler).complete([], []))


def test_malformed_response_is_llm_error():
    def handler(request):
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(LLMError):
        asyncio.run(client_for(handler).complete([], []))


def test_missing_model_is_rejected():
    with pytest.raises(LLMError, match="LLM_MODEL"):
        OpenAICompatibleClient(config(llm_model=""))


def test_config_from_env_hides_key(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "qwen")
    monkeypatch.setenv("LLM_API_KEY", SECRET)
    monkeypatch.setenv("AGENT_MAX_STEPS", "7")
    monkeypatch.setenv("LLM_TEMPERATURE", "")
    loaded = AgentConfig.from_env()
    assert loaded.llm_model == "qwen" and loaded.max_steps == 7
    assert loaded.llm_temperature == 0
    assert SECRET not in repr(loaded)
