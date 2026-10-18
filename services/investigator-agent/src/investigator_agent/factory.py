"""Construct investigators by name."""

from investigator_agent.config import AgentConfig
from investigator_agent.llm import LLMClient
from investigator_agent.llm_only import LLMOnly
from investigator_agent.mas.system import MultiAgentSystem
from investigator_agent.single_agent import SingleAgent
from investigator_agent.tools import ToolRegistry

SYSTEMS = ("llm_only", "single", "mas")


def make_investigator(
    name: str, llm: LLMClient, registry: ToolRegistry, config: AgentConfig
) -> LLMOnly | SingleAgent | MultiAgentSystem:
    if name == "llm_only":
        return LLMOnly(llm)
    if name == "single":
        return SingleAgent(llm, registry, config)
    if name == "mas":
        return MultiAgentSystem(llm, registry, config)
    raise ValueError(f"Unknown system: {name}")
