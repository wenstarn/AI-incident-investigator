"""Command line: investigate a symptom on the live stack or in a replay world."""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from investigator_agent.config import AgentConfig
from investigator_agent.envfile import (
    AGENT_KEYS,
    DIAGNOSTIC_KEYS,
    load_env_file,
    repository_env_file,
)
from investigator_agent.evaluation.replay import WORLD_STATES, ReplayWorld
from investigator_agent.factory import SYSTEMS, make_investigator
from investigator_agent.llm import LLMError, OpenAICompatibleClient
from investigator_agent.report import InvestigationResult
from investigator_agent.tools import ToolRegistry, live_backend

CATEGORY_NAMES = {
    "component_down": "компонент остановлен или не отвечает",
    "dependency_misconfiguration": "неверная конфигурация зависимости",
    "dependency_slow": "зависимость отвечает медленнее таймаута",
    "no_incident": "инцидент не обнаружен",
    "unknown": "причина не установлена",
}


def format_result(result: InvestigationResult) -> str:
    report = result.report
    lines = [
        f"Система: {result.system}   статус: {result.status}",
        f"Диагноз: {CATEGORY_NAMES[report.category]} "
        f"(category={report.category}, component={report.component}, "
        f"dependency={report.dependency}, confidence={report.confidence:.2f})",
        f"Кратко: {report.summary}",
        "Доказательства:",
        *(
            [f"  - [{item.tool}] {item.observation}" for item in report.evidence]
            or ["  нет"]
        ),
    ]
    if report.ruled_out:
        lines += ["Исключено:", *[f"  - {item}" for item in report.ruled_out]]
    if report.recommendation:
        lines.append(f"Рекомендация: {report.recommendation}")
    lines.append("Вызовы инструментов:")
    for record in result.tool_calls:
        lines.append(
            f"  - {record.agent}: {record.tool}({record.arguments}) -> {record.status}"
        )
    if not result.tool_calls:
        lines.append("  нет")
    lines.append(
        f"LLM-вызовов: {result.llm_calls}, токенов: {result.usage.total_tokens}, "
        f"время: {result.duration_s} с"
    )
    if result.error:
        lines.append(f"Ошибка: {result.error}")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    config = AgentConfig.from_env()
    try:
        llm = OpenAICompatibleClient(config)
    except LLMError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    backend = (
        ReplayWorld.named(args.replay).backend() if args.replay else live_backend()
    )
    registry = ToolRegistry(backend, max_output_chars=config.max_tool_output_chars)
    investigator = make_investigator(args.system, llm, registry, config)
    result = await investigator.investigate(args.symptom)
    result.source = "replay" if args.replay else "live"
    result.details["model"] = config.llm_model
    print(format_result(result))
    if not args.no_save:
        path = args.trace_out or (
            Path(args.save_dir)
            / f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{args.system}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
        print(f"Полный трейс сохранён: {path}")
    return 0 if result.status != "llm_error" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Investigate an incident symptom.")
    parser.add_argument(
        "symptom", help="Symptom description, e.g. 'POST /orders → 502'"
    )
    parser.add_argument("--system", choices=SYSTEMS, default="single")
    parser.add_argument(
        "--replay",
        choices=sorted(WORLD_STATES),
        help="Use a recorded world instead of the live stack",
    )
    parser.add_argument("--env-file", type=Path, default=repository_env_file())
    parser.add_argument("--trace-out", type=Path, help="Where to save the full result")
    parser.add_argument("--save-dir", default="investigations")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()
    load_env_file(args.env_file, DIAGNOSTIC_KEYS | AGENT_KEYS)
    # Async psycopg needs a selector loop on Windows.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    sys.exit(asyncio.run(run(args), loop_factory=loop_factory))


if __name__ == "__main__":
    main()
