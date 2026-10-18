import asyncio
import json
import re
from typing import get_args

import diagnostic_tools
import pytest
from diagnostic_tools import docker_tools, http_tools, service_tools
from diagnostic_tools.models import DiagnosticResult
from investigator_agent.evaluation.replay import ReplayWorld
from investigator_agent.tools import (
    TOOL_SPECS,
    AppService,
    ContainerService,
    Dependency,
    HttpPath,
    ToolRegistry,
    live_backend,
)

EXPECTED_TOOLS = {
    "get_service_status",
    "get_service_logs",
    "check_http_endpoint",
    "check_postgres",
    "check_redis",
    "get_service_metrics",
    "check_service_dependency",
    "get_dependency_config",
}


def healthy_registry(**kwargs) -> ToolRegistry:
    return ToolRegistry(ReplayWorld.named("healthy").backend(), **kwargs)


def execute(registry, name, arguments, agent="test"):
    return asyncio.run(registry.execute(name, arguments, agent))


def test_only_read_only_diagnostics_are_exposed():
    assert set(TOOL_SPECS) == EXPECTED_TOOLS
    assert set(TOOL_SPECS) == set(diagnostic_tools.__all__)
    text = json.dumps(healthy_registry().schemas()).lower()
    for forbidden in ("incident", "activate", "reset", "fault", "shell", "execute_sql"):
        assert not re.search(rf"\b{forbidden}", text), forbidden


def test_argument_enums_match_diagnostic_allowlists():
    assert set(get_args(ContainerService)) == docker_tools.SERVICES
    assert set(get_args(HttpPath)) == set().union(*http_tools.PATHS.values())
    assert set(get_args(AppService)) == set(http_tools.PATHS)
    assert set(get_args(Dependency)) == service_tools.DEPENDENCIES


def test_schemas_are_openai_function_definitions():
    schemas = healthy_registry().schemas()
    assert [item["function"]["name"] for item in schemas] == list(TOOL_SPECS)
    for item in schemas:
        assert item["type"] == "function"
        parameters = item["function"]["parameters"]
        assert parameters["type"] == "object" and "properties" in parameters
        assert "title" not in parameters


def test_live_backend_uses_diagnostic_tools_functions():
    backend = live_backend()
    assert backend["check_redis"] is diagnostic_tools.check_redis
    assert set(backend) == EXPECTED_TOOLS


def test_execute_valid_call_records_result():
    content, record = execute(
        healthy_registry(), "get_service_status", '{"service": "redis"}', "agent-x"
    )
    payload = json.loads(content)
    assert payload["status"] == "ok"
    assert payload["data"]["container_status"] == "running"
    assert record.agent == "agent-x" and record.tool == "get_service_status"
    assert record.arguments == {"service": "redis"} and record.status == "ok"


@pytest.mark.parametrize(
    ("name", "arguments", "kind"),
    [
        ("get_service_status", "{not json", "invalid_arguments"),
        (
            "get_service_status",
            '{"service": "incident-controller"}',
            "invalid_arguments",
        ),
        ("get_service_status", '{"service": "redis", "extra": 1}', "invalid_arguments"),
        ("get_service_logs", '{"service": "redis", "tail": 1000}', "invalid_arguments"),
        (
            "check_http_endpoint",
            '{"service": "user-service", "path": "/admin"}',
            "invalid_arguments",
        ),
        ("activate_incident", "{}", "tool_not_allowed"),
    ],
)
def test_invalid_calls_are_rejected_without_backend_call(name, arguments, kind):
    calls = []

    async def backend_call(**kwargs):
        calls.append(kwargs)
        raise AssertionError("backend must not be called")

    backend = {tool: backend_call for tool in TOOL_SPECS}
    content, record = execute(ToolRegistry(backend), name, arguments)
    assert json.loads(content)["error"]["type"] == kind
    assert record.status == "error" and not calls


def test_subset_restricts_tools():
    registry = healthy_registry().subset(["check_redis"])
    assert registry.names == ("check_redis",)
    content, _ = execute(registry, "check_postgres", "{}")
    assert json.loads(content)["error"]["type"] == "tool_not_allowed"
    with pytest.raises(ValueError):
        registry.subset(["check_postgres"])


def test_backend_exception_is_hidden():
    async def broken(**kwargs):
        raise RuntimeError("password=hunter2")

    registry = ToolRegistry({name: broken for name in TOOL_SPECS})
    content, record = execute(registry, "check_redis", "{}")
    assert json.loads(content)["error"]["type"] == "tool_failure"
    assert "hunter2" not in content and "hunter2" not in record.model_dump_json()


def test_long_logs_keep_newest_lines():
    lines = "".join(f"line-{index:04d} {'x' * 80}\n" for index in range(500))

    async def logs(**kwargs):
        return DiagnosticResult(
            tool="get_service_logs",
            status="ok",
            data={"service": "redis", "logs": lines},
        )

    registry = ToolRegistry(
        {**live_backend(), "get_service_logs": logs}, max_output_chars=2000
    )
    content, _ = execute(registry, "get_service_logs", '{"service": "redis"}')
    payload = json.loads(content)
    assert len(content) <= 2000
    assert payload["data"]["truncated_by_agent"] is True
    assert "line-0499" in payload["data"]["logs"]
    assert "line-0000" not in payload["data"]["logs"]


def test_registry_rejects_unknown_or_missing_tools():
    with pytest.raises(ValueError):
        ToolRegistry(live_backend(), ["drop_database"])
    with pytest.raises(ValueError):
        ToolRegistry({}, ["check_redis"])
