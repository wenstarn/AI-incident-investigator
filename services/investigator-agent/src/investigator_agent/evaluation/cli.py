"""Command line for replay and live evaluations of LLM Only, single agent and MAS."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from investigator_agent.config import AgentConfig
from investigator_agent.envfile import (
    AGENT_KEYS,
    DIAGNOSTIC_KEYS,
    HARNESS_KEYS,
    load_env_file,
    repository_env_file,
)
from investigator_agent.evaluation.cases import CASES_BY_ID, select_cases
from investigator_agent.evaluation.controller import (
    ControllerError,
    IncidentControllerClient,
)
from investigator_agent.evaluation.fidelity import PROBES, run_fidelity
from investigator_agent.evaluation.report import write_report
from investigator_agent.evaluation.runner import ResultWriter, run_live, run_replay
from investigator_agent.evaluation.scoring import markdown_table, summarize
from investigator_agent.factory import SYSTEMS, make_investigator
from investigator_agent.llm import LLMError, OpenAICompatibleClient
from investigator_agent.tools import live_backend


def _list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


DESTRUCTIVE_WARNING = (
    "This mode stops containers and injects faults. Re-run with --confirm-destructive."
)


async def run_fidelity_check(args: argparse.Namespace) -> int:
    if not args.confirm_destructive:
        print(DESTRUCTIVE_WARNING, file=sys.stderr)
        return 2
    try:
        cases = select_cases(args.cases)
        mismatches = await run_fidelity(
            cases, IncidentControllerClient.from_env(), live_backend()
        )
    except (ControllerError, ValueError) as error:
        print(f"Fidelity check aborted: {error}", file=sys.stderr)
        return 1
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "fidelity.json"
    report.write_text(
        json.dumps([m.model_dump() for m in mismatches], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    for item in mismatches:
        print(f"{item.case_id}: {item.probe}")
        print(f"  live:   {item.live}\n  replay: {item.replay}")
    print(
        f"\n{len(mismatches)} mismatches in {len(cases)} cases x {len(PROBES)} probes. "
        f"Report: {report}"
    )
    return 0 if not mismatches else 3


async def run(args: argparse.Namespace) -> int:
    if args.mode == "report":
        out = write_report(
            Path(args.out_dir),
            Path(args.report_out or Path(args.out_dir) / "REPORT.md"),
        )
        print(f"Report: {out}")
        return 0
    if args.mode == "fidelity":
        return await run_fidelity_check(args)
    config = AgentConfig.from_env()
    try:
        llm = OpenAICompatibleClient(config)
        cases = select_cases(args.cases, args.generic_symptom)
    except (LLMError, ValueError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    unknown = [name for name in args.systems if name not in SYSTEMS]
    if unknown:
        print(f"Unknown systems: {', '.join(unknown)}", file=sys.stderr)
        return 2

    def make_system(name, registry):
        return make_investigator(name, llm, registry, config)

    writer = ResultWriter(Path(args.out_dir), args.mode)
    common = {
        "cases": cases,
        "systems": args.systems,
        "repeats": args.repeats,
        "make_system": make_system,
        "max_output_chars": config.max_tool_output_chars,
        "sink": writer,
    }
    try:
        if args.mode == "replay":
            scores = await run_replay(**common)
        else:
            if not args.confirm_destructive:
                print(DESTRUCTIVE_WARNING, file=sys.stderr)
                return 2
            scores = await run_live(
                **common,
                controller=IncidentControllerClient.from_env(),
                backend=live_backend(),
            )
    except ControllerError as error:
        print(f"Live evaluation aborted: {error}", file=sys.stderr)
        return 1
    table = markdown_table(summarize(scores))
    meta = {
        "mode": args.mode,
        "model": config.llm_model,
        "systems": ",".join(args.systems),
        "repeats": args.repeats,
        "symptoms": "generic" if args.generic_symptom else "specific",
        "llm_extra_body": json.dumps(config.llm_extra_body)
        if config.llm_extra_body
        else "",
    }
    summary = writer.write_summary(scores, table, meta)
    print("\n" + table)
    print(f"\nResults: {summary.parent}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate incident investigators.")
    parser.add_argument(
        "--mode",
        choices=("replay", "live", "fidelity", "report"),
        default="replay",
        help=(
            "fidelity compares replay worlds with the live stack (no LLM); "
            "report builds one Markdown summary of all runs in --out-dir"
        ),
    )
    parser.add_argument(
        "--systems",
        type=_list,
        default=list(SYSTEMS),
        help=f"Comma-separated subset of: {', '.join(SYSTEMS)}",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--cases",
        type=_list,
        default=None,
        help=f"Comma-separated subset of: {', '.join(CASES_BY_ID)}",
    )
    parser.add_argument(
        "--generic-symptom",
        action="store_true",
        help="Give every case the same vague symptom (harder)",
    )
    parser.add_argument("--out-dir", default="eval-results")
    parser.add_argument(
        "--report-out", help="Report path (default <out-dir>/REPORT.md)"
    )
    parser.add_argument("--env-file", type=Path, default=repository_env_file())
    parser.add_argument("--confirm-destructive", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    load_env_file(args.env_file, DIAGNOSTIC_KEYS | AGENT_KEYS | HARNESS_KEYS)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    sys.exit(asyncio.run(run(args), loop_factory=loop_factory))


if __name__ == "__main__":
    main()
