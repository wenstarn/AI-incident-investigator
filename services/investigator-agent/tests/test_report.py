import json
from datetime import UTC, datetime

from investigator_agent.evaluation.report import build_report, load_runs, write_report


def score(case_id, system, correct=True, repeat=1, tokens=100, duration=10.0):
    return {
        "case_id": case_id,
        "system": system,
        "repeat": repeat,
        "correct": correct,
        "partial": False,
        "grounded": True,
        "predicted": "component_down/redis" if correct else "no_incident/none",
        "expected": "component_down/redis",
        "status": "completed",
        "tool_calls": 5,
        "llm_calls": 3,
        "tokens": tokens,
        "duration_s": duration,
    }


def write_run(root, name, model, mode, scores, symptoms=None):
    folder = root / name
    folder.mkdir(parents=True)
    meta = {"mode": mode, "model": model, "systems": "single,mas", "repeats": 1}
    if symptoms:
        meta["symptoms"] = symptoms
    (folder / "scores.json").write_text(json.dumps({"meta": meta, "scores": scores}))


def test_report_is_computed_from_score_files(tmp_path):
    write_run(
        tmp_path,
        "20261018T100000Z-replay",
        "model-a",
        "replay",
        [
            score("INC-002", "single", tokens=100, duration=10),
            score("INC-002", "mas", tokens=400, duration=50),
        ],
    )
    write_run(
        tmp_path,
        "20261018T110000Z-live",
        "model-b",
        "live",
        [score("INC-002", "single"), score("INC-002", "mas", correct=False)],
    )
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "scores.json").write_text("{not json")

    runs = load_runs(tmp_path)
    assert [run.name for run in runs] == [
        "20261018T100000Z-replay",
        "20261018T110000Z-live",
    ]
    report = build_report(runs, datetime(2026, 10, 18, tzinfo=UTC))
    assert (
        "| 20261018T100000Z-replay | model-a · replay | 1/1 | 1/1 | ×4.0 | ×5.0 |"
        in report
    )
    assert (
        "| 20261018T110000Z-live | model-b · live | MAS | INC-002 | 1 "
        "| `component_down/redis` | `no_incident/none` |" in report
    )
    assert "| 20261018T110000Z-live | model-b | live | 1 | MAS | 0/1 |" in report
    assert "### 20261018T100000Z-replay" in report


def test_generic_symptom_runs_are_marked(tmp_path):
    write_run(
        tmp_path,
        "20261018T120000Z-replay",
        "model-a",
        "replay",
        [score("INC-002", "single"), score("INC-002", "mas")],
        symptoms="generic",
    )
    report = build_report(load_runs(tmp_path))
    assert "| model-a | replay · generic | 1 | Одиночный агент |" in report
    assert "| model-a · replay · generic | 1/1 | 1/1 |" in report


def test_empty_results_and_file_output(tmp_path):
    assert "Оценок пока нет" in build_report([])
    out = write_report(tmp_path, tmp_path / "REPORT.md")
    assert out.read_text(encoding="utf-8").startswith("# Результаты оценки агентов")


def test_llm_only_run_without_mas_comparison(tmp_path):
    write_run(
        tmp_path,
        "20261018T130000Z-replay",
        "model-a",
        "replay",
        [score("INC-002", "llm_only", correct=False)],
    )
    report = build_report(load_runs(tmp_path))
    assert "| model-a | replay | 1 | Только LLM | 0/1 |" in report
    assert "Нет прогонов, где запускались и single, и MAS." in report
    assert "| Только LLM | INC-002 | 1 |" in report
