import asyncio
import os

from investigator_agent.cli import format_result
from investigator_agent.config import AgentConfig
from investigator_agent.envfile import load_env_file, repository_env_file
from investigator_agent.evaluation.replay import ReplayWorld
from investigator_agent.factory import make_investigator
from investigator_agent.mas.system import MultiAgentSystem
from investigator_agent.single_agent import SingleAgent
from investigator_agent.testing import ScriptedLLM, call, respond
from investigator_agent.tools import ToolRegistry


def test_env_file_loads_only_allowed_keys_without_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nLLM_MODEL='qwen'\nLLM_API_KEY=from-file\nOTHER=1\nbroken line\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "from-process")
    load_env_file(env, {"LLM_MODEL", "LLM_API_KEY"})
    assert os.environ["LLM_MODEL"] == "qwen"
    assert os.environ["LLM_API_KEY"] == "from-process"
    assert "OTHER" not in os.environ
    load_env_file(tmp_path / "missing", {"LLM_MODEL"})


def test_repository_env_file_points_to_repo_root():
    assert (repository_env_file().parent / "docker-compose.yml").is_file()


def test_factory_and_report_formatting():
    registry = ToolRegistry(ReplayWorld.named("healthy").backend())
    config = AgentConfig(llm_model="test")
    llm = ScriptedLLM(
        {
            "single-investigator": [
                respond(call("check_redis")),
                respond(
                    call(
                        "submit_report",
                        summary="Инцидент не обнаружен",
                        category="no_incident",
                        component="none",
                        evidence=[{"tool": "check_redis", "observation": "PING ok"}],
                        confidence=0.8,
                    )
                ),
            ]
        }
    )
    assert isinstance(make_investigator("mas", llm, registry, config), MultiAgentSystem)
    agent = make_investigator("single", llm, registry, config)
    assert isinstance(agent, SingleAgent)
    text = format_result(asyncio.run(agent.investigate("Сайт тормозит")))
    assert "инцидент не обнаружен" in text
    assert "[check_redis] PING ok" in text
    assert "single-investigator: check_redis({}) -> ok" in text
