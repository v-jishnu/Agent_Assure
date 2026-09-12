"""
AgentAssure Framework Adapters
"""

from agentassure.adapters.langgraph import LangGraphAdapter
from agentassure.adapters.mcp import MCPAdapter

# OpenAI adapter is opt-in and provides instrument()
try:
    from agentassure.adapters.openai import instrument as instrument_openai
except ImportError:
    instrument_openai = None  # type: ignore[assignment]

__all__ = [
    "LangGraphAdapter",
    "MCPAdapter",
    "instrument_openai",
]
