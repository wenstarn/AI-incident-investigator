"""Agent Console: start investigations and watch the agents work in real time.

Investigations always run against the live stack; replay worlds are a testing
and evaluation tool and are deliberately not offered here.

The console runs on the host next to the Compose stack (like the CLI) and binds
to 127.0.0.1. It holds the Incident Controller token server-side to offer
"break" / "repair" buttons for demonstrations; the agents themselves still get
nothing but the read-only diagnostic tool registry.
"""

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from investigator_agent.config import AgentConfig
from investigator_agent.envfile import (
    AGENT_KEYS,
    DIAGNOSTIC_KEYS,
    HARNESS_KEYS,
    load_env_file,
    repository_env_file,
)
from investigator_agent.evaluation.cases import CASES, CASES_BY_ID, EvalCase
from investigator_agent.evaluation.controller import (
    ControllerError,
    IncidentControllerClient,
)
from investigator_agent.evaluation.runner import (
    RECOVERY_PROBES,
    SYMPTOM_PROBES,
    reproduce_symptom,
    wait_until,
)
from investigator_agent.evaluation.scoring import diagnosis_label, is_correct
from investigator_agent.factory import make_investigator
from investigator_agent.llm import LLMClient, LLMError, OpenAICompatibleClient
from investigator_agent.playback import events_for_playback
from investigator_agent.tools import Backend, ToolRegistry, live_backend
from pydantic import BaseModel, Field

from agent_console.runs import SSE_END, RunStore, sse
from agent_console.traces import discover

STATIC = Path(__file__).parent / "static"
REPOSITORY = repository_env_file().parent
INCIDENT_NAMES = {
    "INC-001": "Остановлен User Service",
    "INC-002": "Остановлен Redis",
    "INC-003": "Остановлен PostgreSQL",
    "INC-004": "Неверный порт User Service",
    "INC-005": "Медленный User Service",
    "INC-006": "Неверный порт Redis",
}
MAX_PLAYBACK_GAP_S = 1.5
# Browsers let any web page send "simple" cross-site POSTs to localhost. A
# custom header forces a CORS preflight, which this server never approves.
CSRF_HEADER = "X-Agent-Console"


class InvestigationRequest(BaseModel):
    symptom: str = Field(min_length=3, max_length=2000)
    system: Literal["single", "mas"] = "single"


class Settings(BaseModel):
    save_dir: Path = REPOSITORY / "investigations"
    trace_dirs: list[Path] = [
        REPOSITORY / "investigations",
        REPOSITORY / "eval-results",
    ]
    # Host header allowlist: blocks DNS-rebinding pages from reaching the API.
    allowed_hosts: frozenset[str] = frozenset({"127.0.0.1", "localhost", "[::1]"})


def _expected_label(case: EvalCase) -> str:
    expected = case.expected
    return diagnosis_label(expected.category, expected.component, expected.dependency)


def create_app(
    settings: Settings | None = None,
    llm_factory: Callable[[AgentConfig], LLMClient] = OpenAICompatibleClient,
    live_backend_factory: Callable[[], Backend] = live_backend,
    controller_factory: Callable[[], Any] = IncidentControllerClient.from_env,
    reproduce: Callable[[EvalCase], Awaitable[None]] = reproduce_symptom,
    symptom_timeout_seconds: float = 60,
    recovery_timeout_seconds: float = 180,
) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Agent Console")
    runs = RunStore()

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "").rsplit(":", 1)[0]
        if host not in settings.allowed_hosts:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        if request.method != "GET" and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": "Missing console header"}, status_code=403)
        return await call_next(request)

    def controller():
        try:
            return controller_factory()
        except ControllerError as error:
            raise HTTPException(503, str(error)) from error

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/options")
    def options() -> dict[str, Any]:
        config = AgentConfig.from_env()
        return {
            "model": config.llm_model or None,
            "controller": bool(os.getenv("INCIDENT_CONTROLLER_TOKEN")),
            "systems": [
                {"id": "single", "label": "Одиночный агент"},
                {"id": "mas", "label": "Мультиагентная система"},
            ],
            "incidents": [
                {"id": key, "name": name} for key, name in INCIDENT_NAMES.items()
            ],
            "cases": [
                {
                    "id": case.id,
                    "incident_id": case.incident_id,
                    "symptom": case.symptom,
                    "expected": _expected_label(case),
                }
                for case in CASES
            ],
        }

    async def expected_case() -> EvalCase | None:
        """The answer is known only for incidents enabled on the test stand."""
        try:
            active = await controller_factory().active()
        except ControllerError:
            return None
        return CASES_BY_ID[active] if active in CASES_BY_ID else CASES_BY_ID["healthy"]

    def save(result_json: dict[str, Any]) -> Path:
        settings.save_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = settings.save_dir / f"{stamp}-{result_json['system']}-console.json"
        path.write_text(json.dumps(result_json, ensure_ascii=False, indent=1), "utf-8")
        return path

    @app.post("/api/investigations")
    async def start_investigation(request: InvestigationRequest) -> dict[str, str]:
        config = AgentConfig.from_env()
        try:
            llm = llm_factory(config)
        except LLMError as error:
            raise HTTPException(400, str(error)) from error
        registry = ToolRegistry(
            live_backend_factory(), max_output_chars=config.max_tool_output_chars
        )
        investigator = make_investigator(request.system, llm, registry, config)
        case = await expected_case()
        run = runs.create()

        async def execute() -> None:
            started_at = datetime.now(UTC).isoformat()
            try:
                run.push(
                    {
                        "type": "console_started",
                        "source": "live",
                        "model": config.llm_model,
                    }
                )
                result = await investigator.investigate(
                    request.symptom, on_event=run.push
                )
                result.source = "live"
                result.details["started_at"] = started_at
                result.details["model"] = config.llm_model
                if case is not None:
                    report = result.report
                    evaluation = {
                        "case_id": case.id,
                        "expected": _expected_label(case),
                        "predicted": diagnosis_label(
                            report.category, report.component, report.dependency
                        ),
                        "correct": is_correct(result, case.expected),
                    }
                    # Stored with the result so the trace list and playback
                    # can show the verdict later.
                    result.details["evaluation"] = evaluation
                    run.push({"type": "evaluation", **evaluation})
                path = save(result.model_dump(mode="json"))
                run.push({"type": "saved", "file": path.name})
            except Exception as error:  # surfaced to the operator, not raised
                run.push({"type": "error", "agent": "console", "message": str(error)})
            finally:
                run.finish()

        run.task = asyncio.create_task(execute())
        return {"id": run.id}

    @app.get("/api/investigations/{run_id}/stream")
    async def stream_investigation(run_id: str) -> StreamingResponse:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, "Investigation not found")

        async def body():
            async for event in run.follow():
                yield sse(event)
            yield SSE_END

        return StreamingResponse(body(), media_type="text/event-stream")

    @app.get("/api/traces")
    def traces() -> list[dict[str, Any]]:
        return [
            trace.info.model_dump() for trace in discover(settings.trace_dirs).values()
        ]

    @app.get("/api/traces/{trace_id}/stream")
    async def stream_trace(
        trace_id: str, speed: float = Query(default=4, ge=0.5, le=1000)
    ) -> StreamingResponse:
        trace = discover(settings.trace_dirs).get(trace_id)
        if trace is None:
            raise HTTPException(404, "Trace not found")
        events = events_for_playback(trace.result)

        async def body():
            yield sse(
                {
                    "type": "console_started",
                    "source": "playback",
                    "model": trace.info.model,
                    "t": 0,
                }
            )
            previous = 0.0
            for event in events:
                gap = max(0.0, float(event.get("t", previous)) - previous)
                previous = float(event.get("t", previous))
                await asyncio.sleep(min(gap / speed, MAX_PLAYBACK_GAP_S))
                yield sse(event)
            if trace.score:
                yield sse(
                    {
                        "type": "evaluation",
                        "case_id": trace.info.case_id,
                        "expected": trace.score.get("expected"),
                        "predicted": trace.score.get("predicted"),
                        "correct": trace.score.get("correct"),
                    }
                )
            yield SSE_END

        return StreamingResponse(body(), media_type="text/event-stream")

    @app.get("/api/incidents/active")
    async def active_incident() -> dict[str, Any]:
        try:
            return {"incident": await controller().active()}
        except ControllerError as error:
            raise HTTPException(503, str(error)) from error

    @app.post("/api/incidents/{incident_id}/activate")
    async def activate_incident(incident_id: str) -> dict[str, Any]:
        if incident_id not in INCIDENT_NAMES:
            raise HTTPException(404, "Unknown incident")
        client = controller()
        try:
            await client.activate(incident_id)
        except ControllerError as error:
            raise HTTPException(409, str(error)) from error
        backend = live_backend_factory()
        observable = await wait_until(
            backend,
            [SYMPTOM_PROBES[incident_id]],
            "degraded",
            symptom_timeout_seconds,
        )
        case = CASES_BY_ID[incident_id]
        # Real user traffic, so logs and counters show the symptom.
        await reproduce(case)
        return {
            "incident": incident_id,
            "observable": observable,
            "symptom": case.symptom,
        }

    @app.post("/api/incidents/reset")
    async def reset_incident() -> dict[str, Any]:
        client = controller()
        try:
            await client.reset()
        except ControllerError as error:
            raise HTTPException(409, str(error)) from error
        recovered = await wait_until(
            live_backend_factory(), RECOVERY_PROBES, "ok", recovery_timeout_seconds
        )
        return {"incident": None, "recovered": recovered}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def run() -> None:
    import uvicorn

    load_env_file(repository_env_file(), DIAGNOSTIC_KEYS | AGENT_KEYS | HARNESS_KEYS)
    host = os.getenv("CONSOLE_HOST", "127.0.0.1")
    port = int(os.getenv("CONSOLE_PORT", "3010"))
    print(f"Agent Console: http://{host}:{port}", file=sys.stderr)
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
