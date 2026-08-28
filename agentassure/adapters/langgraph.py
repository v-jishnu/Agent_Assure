"""
LangGraph Adapter for AgentAssure
Converts LangGraph tools/callbacks into canonical AgentEvents.
"""

from typing import Any, Callable, Dict, Optional
from agentassure.events import AgentEvent, EventType, EventStatus
from agentassure.trace import TraceContext


class LangGraphAdapter:
    def __init__(self, agentassure_instance: Any):
        self.assure = agentassure_instance

    def wrap_tool(self, tool_func: Callable, tool_name: Optional[str] = None) -> Callable:
        """
        Wraps a LangGraph tool function with AgentAssure pre-execution governance.
        """
        name = tool_name or getattr(tool_func, "__name__", "unnamed_tool")
        return self.assure.wrap_tool(tool_func, tool_name=name)
