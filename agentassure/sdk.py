"""
AgentAssure Developer SDK & Agent Integration Interface
"""

import functools
import inspect
from typing import Any, Callable, Dict, List, Optional, Union
from agentassure.events import AgentEvent, EventType, EventStatus
from agentassure.trace import TraceContext, generate_trace_id, generate_span_id
from agentassure.policy import PolicyEngine, PolicyDecision, PolicyOutcome
from agentassure.detectors import BaseDetector, PIIDetector, SecretDetector, RestrictedToolDetector
from agentassure.enforcement import EnforcementEngine, PolicyViolationError, PendingApprovalException
from server.evidence import EvidenceStore


class AgentAssure:
    def __init__(
        self,
        policy_path: Optional[str] = None,
        db_path: str = "agentassure.db",
        agent_id: str = "loan-agent",
        environment: str = "demo",
        detectors: Optional[List[BaseDetector]] = None,
    ):
        self.agent_id = agent_id
        self.environment = environment
        self.session_id = "default_session"

        if policy_path:
            self.policy_engine = PolicyEngine.load_from_yaml(policy_path)
        else:
            self.policy_engine = PolicyEngine()

        if detectors is None:
            detectors = [PIIDetector(), SecretDetector(), RestrictedToolDetector()]

        self.detectors = detectors
        self.enforcement_engine = EnforcementEngine(self.policy_engine, self.detectors)
        self.evidence_store = EvidenceStore(db_path)

    def set_session(self, session_id: str):
        self.session_id = session_id

    def wrap_tool(self, tool_func: Callable, tool_name: Optional[str] = None) -> Callable:
        name = tool_name or getattr(tool_func, "__name__", "unnamed_tool")

        @functools.wraps(tool_func)
        def wrapper(*args, **kwargs):
            # Parse arguments into dictionary
            sig = inspect.signature(tool_func)
            bound_args = sig.bind_partial(*args, **kwargs)
            bound_args.apply_defaults()
            tool_input = dict(bound_args.arguments)

            # Get current trace and span context
            trace_id, parent_span_id, _ = TraceContext.get_current()
            span_id = generate_span_id()

            # 1. Emit TOOL_CALL event (started)
            start_event = AgentEvent(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                agent_id=self.agent_id,
                session_id=self.session_id,
                event_type=EventType.TOOL_CALL,
                tool_name=name,
                input=tool_input,
                status=EventStatus.STARTED,
            )

            # 2. Pre-execution Policy & Detector evaluation
            decision: PolicyDecision = self.enforcement_engine.evaluate_action(
                agent_id=self.agent_id,
                environment=self.environment,
                tool_name=name,
                tool_input=tool_input,
            )

            # 3. Record policy evaluation evidence
            self.evidence_store.record_event(
                event=start_event,
                policy_id=decision.policy_id,
                policy_version=decision.policy_version,
                decision=decision.outcome.value,
                reason=decision.reason,
                controls=decision.controls.as_citation() if decision.controls else None,
            )

            # 4. Pre-execution Interception: BLOCK
            if decision.outcome == PolicyOutcome.BLOCK:
                block_event = AgentEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    event_type=EventType.POLICY_DECISION,
                    tool_name=name,
                    input=tool_input,
                    status=EventStatus.BLOCKED,
                    metadata={"decision": decision.outcome.value, "reason": decision.reason},
                )
                self.evidence_store.record_event(
                    event=block_event,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision=decision.outcome.value,
                    reason=decision.reason,
                    controls=decision.controls.as_citation() if decision.controls else None,
                )
                # UNDERLYING TOOL IS NOT EXECUTED!
                raise PolicyViolationError(decision)

            # 5. Pre-execution Interception: ASK (Pending Human Approval)
            elif decision.outcome == PolicyOutcome.ASK:
                ask_event = AgentEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    event_type=EventType.APPROVAL_REQUESTED,
                    tool_name=name,
                    input=tool_input,
                    status=EventStatus.PENDING_APPROVAL,
                    metadata={"decision": decision.outcome.value, "reason": decision.reason},
                )
                self.evidence_store.record_event(
                    event=ask_event,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision=decision.outcome.value,
                    reason=decision.reason,
                    controls=decision.controls.as_citation() if decision.controls else None,
                )
                # UNDERLYING TOOL IS NOT EXECUTED!
                raise PendingApprovalException(decision)

            # 6. ALLOW / SHADOW: Execute underlying tool function
            try:
                result = tool_func(*args, **kwargs)
                result_event = AgentEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    event_type=EventType.TOOL_RESULT,
                    tool_name=name,
                    input=tool_input,
                    output=result,
                    status=EventStatus.COMPLETED,
                )
                self.evidence_store.record_event(
                    event=result_event,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision=decision.outcome.value,
                    reason=decision.reason,
                    controls=decision.controls.as_citation() if decision.controls else None,
                )
                return result
            except Exception as e:
                error_event = AgentEvent(
                    trace_id=trace_id,
                    span_id=span_id,
                    parent_span_id=parent_span_id,
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    event_type=EventType.ERROR,
                    tool_name=name,
                    input=tool_input,
                    output=str(e),
                    status=EventStatus.FAILED,
                )
                self.evidence_store.record_event(event=error_event)
                raise e

        return wrapper

    def wrap(self, agent_obj: Any) -> Any:
        """
        Generic agent wrapper. Scans for methods starting with tool_ or decorated attributes.
        """
        for attr_name in dir(agent_obj):
            if attr_name.startswith("tool_") or attr_name in getattr(agent_obj, "registered_tools", []):
                attr = getattr(agent_obj, attr_name)
                if callable(attr):
                    setattr(agent_obj, attr_name, self.wrap_tool(attr, tool_name=attr_name))
        return agent_obj
