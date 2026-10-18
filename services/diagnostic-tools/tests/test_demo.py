import asyncio
import runpy
import sys
from pathlib import Path

import diagnostic_tools
from diagnostic_tools.models import DiagnosticResult


def test_demo_uses_compatible_event_loop(monkeypatch, capsys):
    loops = []

    async def check(*args, **kwargs):
        loops.append(asyncio.get_running_loop())
        return DiagnosticResult(tool="demo_test", status="ok", data={})

    for name in diagnostic_tools.__all__:
        monkeypatch.setattr(diagnostic_tools, name, check)
    for key in (
        "DOCKER_EXECUTOR_READ_TOKEN",
        "DIAGNOSTIC_POSTGRES_PASSWORD",
        "DIAGNOSTICS_TOKEN",
    ):
        monkeypatch.setenv(key, "test")

    demo = Path(__file__).resolve().parents[3] / "examples/diagnostic_tools_demo.py"
    runpy.run_path(str(demo), run_name="__main__")

    assert len(loops) == 8
    assert all(loop is loops[0] for loop in loops)
    if sys.platform == "win32":
        assert isinstance(loops[0], asyncio.SelectorEventLoop)
    assert loops[0].is_closed()
    assert len(capsys.readouterr().out.splitlines()) == 8
