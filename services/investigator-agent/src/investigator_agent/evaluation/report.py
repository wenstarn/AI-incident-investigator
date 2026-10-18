"""One Markdown report built from every evaluation run in eval-results/.

All numbers are computed from the ``scores.json`` files written by the
evaluation harness; nothing is typed in by hand. Each section names the run
folders it comes from.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import BaseModel

SYSTEM_NAMES = {"single": "Одиночный агент", "mas": "MAS"}


class EvalRun(BaseModel):
    name: str
    meta: dict[str, Any]
    scores: list[dict[str, Any]]

    @property
    def label(self) -> str:
        return f"{self.meta.get('model') or '?'} · {self.meta.get('mode', '?')}"


def load_runs(root: Path) -> list[EvalRun]:
    runs = []
    for path in sorted(root.glob("*/scores.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("scores"):
            runs.append(
                EvalRun(name=path.parent.name, meta=data["meta"], scores=data["scores"])
            )
    return runs


def _totals(scores: list[dict[str, Any]]) -> dict[str, float]:
    return {
        "runs": len(scores),
        "correct": sum(bool(s["correct"]) for s in scores),
        "grounded": sum(bool(s.get("grounded")) for s in scores),
        "tool_calls": mean(s["tool_calls"] for s in scores),
        "llm_calls": mean(s["llm_calls"] for s in scores),
        "tokens": mean(s["tokens"] for s in scores),
        "duration_s": mean(s["duration_s"] for s in scores),
    }


def _by_system(run: EvalRun) -> dict[str, dict[str, float]]:
    systems: dict[str, list[dict[str, Any]]] = {}
    for score in run.scores:
        systems.setdefault(score["system"], []).append(score)
    return {name: _totals(items) for name, items in systems.items()}


def _ratio(numerator: float, denominator: float) -> str:
    return f"×{numerator / denominator:.1f}" if denominator else "—"


def build_report(runs: list[EvalRun], generated_at: datetime | None = None) -> str:
    generated_at = generated_at or datetime.now(UTC)
    lines = [
        "# Результаты оценки агентов",
        "",
        f"Сгенерировано {generated_at:%Y-%m-%d %H:%M} UTC командой "
        "`make agent-eval-report` из файлов `eval-results/*/scores.json`. "
        "Каждая цифра посчитана по сохранённым оценкам отдельных расследований.",
        "",
        "**Как читать:** *верно* — категория, компонент и (для неверной "
        "конфигурации) зависимость совпали с правильным ответом кейса; "
        "*подтверждено* — каждое доказательство в отчёте ссылается на реально "
        "вызванный инструмент; токены, вызовы и время — средние на одно "
        "расследование. *replay* — имитатор стенда, *live* — живой стенд.",
        "",
    ]
    if not runs:
        return "\n".join(
            [*lines, "Оценок пока нет: запустите `make agent-eval-replay`."]
        )

    lines += [
        "## 1. Сводка по всем прогонам",
        "",
        "| Прогон (папка) | Модель | Режим | Повторов | Система | Верно | "
        "Подтверждено | Инструментов | LLM-вызовов | Токенов | Время, с |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        for system, total in _by_system(run).items():
            lines.append(
                f"| {run.name} | {run.meta.get('model')} | {run.meta.get('mode')} "
                f"| {run.meta.get('repeats')} | {SYSTEM_NAMES.get(system, system)} "
                f"| {total['correct']}/{total['runs']} "
                f"| {total['grounded']}/{total['runs']} "
                f"| {total['tool_calls']:.1f} | {total['llm_calls']:.1f} "
                f"| {total['tokens']:,.0f} | {total['duration_s']:.1f} |".replace(
                    ",", " "
                )
            )

    lines += [
        "",
        "## 2. MAS против одиночного агента",
        "",
        "| Прогон | Модель · режим | Точность single | Точность MAS "
        "| Токены MAS / single | Время MAS / single |",
        "|---|---|---|---|---|---|",
    ]
    for run in runs:
        systems = _by_system(run)
        if not {"single", "mas"} <= set(systems):
            continue
        single, mas = systems["single"], systems["mas"]
        lines.append(
            f"| {run.name} | {run.label} "
            f"| {single['correct']}/{single['runs']} | {mas['correct']}/{mas['runs']} "
            f"| {_ratio(mas['tokens'], single['tokens'])} "
            f"| {_ratio(mas['duration_s'], single['duration_s'])} |"
        )

    errors = [
        (run, score) for run in runs for score in run.scores if not score["correct"]
    ]
    lines += ["", "## 3. Ошибки", ""]
    if errors:
        lines += [
            "| Прогон | Модель · режим | Система | Кейс | Повтор "
            "| Ожидалось | Получено |",
            "|---|---|---|---|---|---|---|",
        ]
        for run, score in errors:
            lines.append(
                f"| {run.name} | {run.label} "
                f"| {SYSTEM_NAMES.get(score['system'], score['system'])} "
                f"| {score['case_id']} | {score['repeat']} | `{score['expected']}` "
                f"| `{score['predicted']}` |"
            )
        lines += [
            "",
            "Полный ход каждого ошибочного расследования — в "
            "`eval-results/<прогон>/investigations.jsonl` и в Agent Console "
            "(«Повтор сохранённых», отмечены ✗).",
        ]
    else:
        lines.append("Ошибок нет.")

    lines += ["", "## 4. Подробно по кейсам", ""]
    for run in runs:
        lines += [
            f"### {run.name} — {run.label}, повторов: {run.meta.get('repeats')}, "
            f"симптомы: {run.meta.get('symptoms', 'specific')}",
            "",
            "| Кейс | Система | Верно | Подтверждено | Инструментов | LLM-вызовов "
            "| Токенов | Время, с |",
            "|---|---|---|---|---|---|---|---|",
        ]
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for score in run.scores:
            groups.setdefault((score["case_id"], score["system"]), []).append(score)
        for (case_id, system), items in groups.items():
            total = _totals(items)
            lines.append(
                f"| {case_id} | {SYSTEM_NAMES.get(system, system)} "
                f"| {total['correct']}/{total['runs']} "
                f"| {total['grounded']}/{total['runs']} | {total['tool_calls']:.1f} "
                f"| {total['llm_calls']:.1f} | {total['tokens']:,.0f} "
                f"| {total['duration_s']:.1f} |".replace(",", " ")
            )
        lines.append("")
    return "\n".join(lines)


def write_report(root: Path, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report(load_runs(root)), encoding="utf-8")
    return out
