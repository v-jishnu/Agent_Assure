"""
Model Context Protocol (MCP) Adapter for AgentAssure.

Provides pre-execution governance, policy enforcement, and audit evidence
for MCP tool servers and tool call dispatchers.

Usage:
    from agentassure import AgentAssure
    from agentassure.adapters.mcp import MCPAdapter

    aa = AgentAssure()
    mcp_adapter = MCPAdapter(aa)

    # Option 1: Wrap an MCP tool execution function
    @mcp_adapter.govern_tool(tool_name="read_file")
    async def read_file(path: str):
        ...

    # Option 2: Wrap an MCP server's call_tool dispatcher
    governed_dispatcher = mcp_adapter.wrap_dispatcher(server_call_tool)
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, Dict, Optional

from agentassure.events import AgentEvent, EventType, EventStatus
from agentassure.trace import TraceContext, generate_span_id
from agentassure.policy import PolicyOutcome, PolicyDecision
from agentassure.enforcement import PolicyViolationError, PendingApprovalException
from agentassure.logging import log_runtime


class MCPAdapter:
    """
    Governance adapter for Model Context Protocol (MCP) servers and tools.
    """

    def __init__(self, agentassure_instance: Any):
        self.assure = agentassure_instance

    def govern_tool(self, tool_func: Optional[Callable] = None, *, tool_name: Optional[str] = None) -> Any:
        """Decorator for MCP tool implementations."""
        if tool_func is None:
            def decorator(fn: Callable) -> Callable:
                return self.wrap_tool(fn, tool_name=tool_name)
            return decorator
        return self.wrap_tool(tool_func, tool_name=tool_name)

    def wrap_tool(self, tool_func: Callable, tool_name: Optional[str] = None) -> Callable:
        """Wrap an MCP tool function (sync or async) with AgentAssure governance."""
        name = tool_name or getattr(tool_func, "__name__", "mcp_tool")

        if inspect.iscoroutinefunction(tool_func):
            @functools.wraps(tool_func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                sig = inspect.signature(tool_func)
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                tool_input = dict(bound.arguments)

                trace_id, parent_span_id, _ = TraceContext.get_current()
                span_id = generate_span_id()

                # Pre-execution policy check
                decision = self.assure.enforcement_engine.evaluate_action(
                    agent_id=self.assure.agent_id,
                    environment=self.assure.environment,
                    tool_name=name,
                    tool_input=tool_input,
                )

                start_event = AgentEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=self.assure.agent_id,
                    session_id=self.assure.session_id,
                    event_type=EventType.TOOL_CALL,
                    tool_name=name,
                    input=tool_input,
                    status=EventStatus.STARTED,
                )
                self.assure._record_and_publish(
                    event=start_event,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision=decision.outcome.value,
                    reason=decision.reason,
                    controls=decision.controls.as_citation() if decision.controls else None,
                )

                if decision.outcome == PolicyOutcome.BLOCK:
                    raise PolicyViolationError(decision)
                if decision.outcome == PolicyOutcome.ASK:
                    approval = self.assure.approval_store.create_approval(
                        trace_id=trace_id,
                        span_id=span_id,
                        event_id=start_event.event_id,
                        tool_name=name,
                        tool_arguments=tool_input,
                        agent_id=self.assure.agent_id,
                        session_id=self.assure.session_id,
                        policy_id=decision.policy_id,
                        policy_version=decision.policy_version,
                        reason=decision.reason,
                    )
                    raise PendingApprovalException(decision)

                result = await tool_func(*args, **kwargs)

                result_event = AgentEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=self.assure.agent_id,
                    session_id=self.assure.session_id,
                    event_type=EventType.TOOL_RESULT,
                    tool_name=name,
                    input=tool_input,
                    output=result,
                    status=EventStatus.COMPLETED,
                )
                self.assure._record_and_publish(
                    event=result_event,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision=decision.outcome.value,
                    reason=decision.reason,
                    controls=decision.controls.as_citation() if decision.controls else None,
                )
                return result

            return async_wrapper
        else:
            return self.assure.wrap_tool(tool_func, tool_name=name)

    def wrap_dispatcher(self, dispatcher_func: Callable) -> Callable:
        """
        Wrap an MCP dispatcher: dispatcher(name: str, arguments: dict).
        """
        is_async = inspect.iscoroutinefunction(dispatcher_func)

        if is_async:
            @functools.wraps(dispatcher_func)
            async def async_dispatch(name: str, arguments: Optional[Dict[str, Any]] = None, *args: Any, **kwargs: Any) -> Any:
                args_dict = arguments or {}
                trace_id, parent_span_id, _ = TraceContext.get_current()
                span_id = generate_span_id()

                decision = self.assure.enforcement_engine.evaluate_action(
                    agent_id=self.assure.agent_id,
                    environment=self.assure.environment,
                    tool_name=name,
                    tool_input=args_dict,
                )

                start_event = AgentEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=self.assure.agent_id,
                    session_id=self.assure.session_id,
                    event_type=EventType.TOOL_CALL,
                    tool_name=name,
                    input=args_dict,
                    status=EventStatus.STARTED,
                )
                self.assure._record_and_publish(
                    event=start_event,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision=decision.outcome.value,
                    reason=decision.reason,
                    controls=decision.controls.as_citation() if decision.controls else None,
                )

                if decision.outcome == PolicyOutcome.BLOCK:
                    raise PolicyViolationError(decision)
                if decision.outcome == PolicyOutcome.ASK:
                    approval = self.assure.approval_store.create_approval(
                        trace_id=trace_id,
                        span_id=span_id,
                        event_id=start_event.event_id,
                        tool_name=name,
                        tool_arguments=args_dict,
                        agent_id=self.assure.agent_id,
                        session_id=self.assure.session_id,
                        policy_id=decision.policy_id,
                        policy_version=decision.policy_version,
                        reason=decision.reason,
                    )
                    raise PendingApprovalException(decision)

                result = await dispatcher_func(name, arguments, *args, **kwargs)

                result_event = AgentEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=self.assure.agent_id,
                    session_id=self.assure.session_id,
                    event_type=EventType.TOOL_RESULT,
                    tool_name=name,
                    input=args_dict,
                    output=result,
                    status=EventStatus.COMPLETED,
                )
                self.assure._record_and_publish(
                    event=result_event,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision=decision.outcome.value,
                    reason=decision.reason,
                    controls=decision.controls.as_citation() if decision.controls else None,
                )
                return result

            return async_dispatch
        else:
            @functools.wraps(dispatcher_func)
            def sync_dispatch(name: str, arguments: Optional[Dict[str, Any]] = None, *args: Any, **kwargs: Any) -> Any:
                args_dict = arguments or {}
                trace_id, parent_span_id, _ = TraceContext.get_current()
                span_id = generate_span_id()

                decision = self.assure.enforcement_engine.evaluate_action(
                    agent_id=self.assure.agent_id,
                    environment=self.assure.environment,
                    tool_name=name,
                    tool_input=args_dict,
                )

                start_event = AgentEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=self.assure.agent_id,
                    session_id=self.assure.session_id,
                    event_type=EventType.TOOL_CALL,
                    tool_name=name,
                    input=args_dict,
                    status=EventStatus.STARTED,
                )
                self.assure._record_and_publish(
                    event=start_event,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision=decision.outcome.value,
                    reason=decision.reason,
                    controls=decision.controls.as_citation() if decision.controls else None,
                )

                if decision.outcome == PolicyOutcome.BLOCK:
                    raise PolicyViolationError(decision)
                if decision.outcome == PolicyOutcome.ASK:
                    approval = self.assure.approval_store.create_approval(
                        trace_id=trace_id,
                        span_id=span_id,
                        event_id=start_event.event_id,
                        tool_name=name,
                        tool_arguments=args_dict,
                        agent_id=self.assure.agent_id,
                        session_id=self.assure.session_id,
                        policy_id=decision.policy_id,
                        policy_version=decision.policy_version,
                        reason=decision.reason,
                    )
                    raise PendingApprovalException(decision)

                result = dispatcher_func(name, arguments, *args, **kwargs)

                result_event = AgentEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=self.assure.agent_id,
                    session_id=self.assure.session_id,
                    event_type=EventType.TOOL_RESULT,
                    tool_name=name,
                    input=args_dict,
                    output=result,
                    status=EventStatus.COMPLETED,
                )
                self.assure._record_and_publish(
                    event=result_event,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision=decision.outcome.value,
                    reason=decision.reason,
                    controls=decision.controls.as_citation() if decision.controls else None,
                )
                return result

            return sync_dispatch
