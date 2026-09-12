"""
Tests for Framework Adapters (LangGraph, MCP).
"""

import pytest
import asyncio
from agentassure.sdk import AgentAssure
from agentassure.adapters.langgraph import LangGraphAdapter
from agentassure.adapters.mcp import MCPAdapter
from agentassure.enforcement import PolicyViolationError
from agentassure.policy import PolicyRule, PolicyCondition, PolicyOutcome, PolicyMode


def test_langgraph_adapter(tmp_path):
    db_path = str(tmp_path / "langgraph.db")
    aa = AgentAssure(db_path=db_path)
    adapter = LangGraphAdapter(aa)

    def sample_tool(query: str):
        return f"result: {query}"

    governed = adapter.wrap_tool(sample_tool, tool_name="search")
    res = governed(query="testing")
    assert res == "result: testing"


def test_mcp_adapter_sync_and_async(tmp_path):
    db_path = str(tmp_path / "mcp.db")
    aa = AgentAssure(db_path=db_path)
    mcp = MCPAdapter(aa)

    # Sync tool
    @mcp.govern_tool(tool_name="sync_tool")
    def sync_tool(x: int):
        return x * 2

    assert sync_tool(5) == 10

    # Async tool
    @mcp.govern_tool(tool_name="async_tool")
    async def async_tool(msg: str):
        await asyncio.sleep(0.01)
        return f"echo: {msg}"

    res = asyncio.run(async_tool("hello"))
    assert res == "echo: hello"


def test_mcp_adapter_dispatcher(tmp_path):
    db_path = str(tmp_path / "mcp_dispatch.db")
    aa = AgentAssure(db_path=db_path)

    # Add a policy blocking "danger_tool"
    rule = PolicyRule(
        id="MCP-BLOCK-DANGER",
        name="Block danger tool",
        action=PolicyOutcome.BLOCK,
        mode=PolicyMode.ENFORCE,
        scope={"tools": ["danger_tool"]}
    )
    aa.policy_engine.add_or_update_rule(rule)

    mcp = MCPAdapter(aa)

    def dispatch(name: str, arguments: dict):
        return f"executed {name} with {arguments}"

    governed_dispatch = mcp.wrap_dispatcher(dispatch)

    # Allowed tool
    ok_res = governed_dispatch("safe_tool", {"param": 123})
    assert "safe_tool" in ok_res

    # Blocked tool
    with pytest.raises(PolicyViolationError):
        governed_dispatch("danger_tool", {"param": 456})
