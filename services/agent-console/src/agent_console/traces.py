"""Discovery of saved investigations for playback.

Sources: single results saved by the CLI and the console (``*.json``) and
evaluation runs (``*/investigations.jsonl``). Only investigations of the live
stack are offered: the console presents real runs, not replay-world ones.
Traces are addressed by an opaque ID; client input is never used as a file path.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

MAX_TRACES = 200


class TraceInfo(BaseModel):
    id: str
    system: str
    symptom: str
    created: str
    predicted: str | None = None
    correct: bool | None = None
    case_id: str | None = None
    model: str | None = None
    file: str


class Trace(BaseModel):
    info: TraceInfo
    result: dict[str, Any]
    score: dict[str, Any] | None = None


def _trace_id(path: Path, line: int) -> str:
    key = f"{path.resolve()}:{line}".encode()
    return hashlib.sha1(key, usedforsecurity=False).hexdigest()[:16]


def _predicted(result: dict[str, Any]) -> str | None:
    report = result.get("report") or {}
    if not report:
        return None
    label = f"{report.get('category')}/{report.get('component')}"
    dependency = report.get("dependency", "none")
    return f"{label}->{dependency}" if dependency != "none" else label


def investigation_source(path: Path, result: dict[str, Any]) -> str | None:
    """live/replay from the result, or from an evaluation folder name."""
    if result.get("source") in {"live", "replay"}:
        return result["source"]
    for mode in ("live", "replay"):
        if path.suffix == ".jsonl" and path.parent.name.endswith(f"-{mode}"):
            return mode
    return None


def _run_model(path: Path) -> str | None:
    """Model of an evaluation run, from the scores.json next to its traces."""
    try:
        meta = json.loads((path.parent / "scores.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return (meta.get("meta") or {}).get("model")


def _make(
    path: Path,
    line: int,
    result: dict[str, Any],
    record: dict[str, Any] | None,
    run_model: str | None = None,
) -> Trace | None:
    if not isinstance(result, dict) or "system" not in result:
        return None
    if investigation_source(path, result) != "live":
        return None
    details = result.get("details") or {}
    # Evaluation runs keep the score next to the result; console runs inside it.
    score = (record or {}).get("score") or details.get("evaluation")
    created = (
        details.get("started_at")
        or datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    )
    return Trace(
        info=TraceInfo(
            id=_trace_id(path, line),
            system=result.get("system", "?"),
            symptom=result.get("symptom", ""),
            created=created,
            predicted=_predicted(result),
            correct=score.get("correct") if score else None,
            case_id=(record or {}).get("case_id") or (score or {}).get("case_id"),
            # The evaluation run label is authoritative for its traces.
            model=run_model or details.get("model"),
            file=f"{path.parent.name}/{path.name}",
        ),
        result=result,
        score=score,
    )


def _read(path: Path) -> list[Trace]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    traces = []
    if path.suffix == ".jsonl":
        run_model = _run_model(path)
        for number, line in enumerate(text.splitlines()):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            trace = _make(path, number, record.get("result"), record, run_model)
            if trace:
                traces.append(trace)
    else:
        try:
            trace = _make(path, 0, json.loads(text), None)
        except ValueError:
            trace = None
        if trace:
            traces.append(trace)
    return traces


def discover(directories: list[Path]) -> dict[str, Trace]:
    paths: list[Path] = []
    for directory in directories:
        if directory.is_dir():
            paths += directory.glob("*.json")
            paths += directory.glob("*/investigations.jsonl")
    found = [trace for path in paths for trace in _read(path)]
    # Newest run first; ISO timestamps in UTC sort lexicographically.
    found.sort(key=lambda trace: trace.info.created, reverse=True)
    return {trace.info.id: trace for trace in found[:MAX_TRACES]}
