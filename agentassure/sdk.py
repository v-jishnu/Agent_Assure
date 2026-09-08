"""
AgentAssure Developer SDK & Agent Integration Interface
"""

import functools
import inspect
from typing import Any, Callable, Dict, List, Optional, Union
from agentassure.events import AgentEvent, EventType, EventStatus
from agentassure.trace import TraceContext, generate_span_id
from agentassure.policy import PolicyEngine, PolicyDecision, PolicyOutcome
from agentassure.detectors import BaseDetector, PIIDetector, SecretDetector, RestrictedToolDetector
from agentassure.enforcement import EnforcementEngine, PolicyViolationError, PendingApprovalException
from agentassure.evidence import EvidenceStore, EvidenceRecord
from agentassure.approval import ApprovalStore, ApprovalRequest, ApprovalStatus
from agentassure.logging import log_runtime
from agentassure.publisher import EventPublisher, default_event_publisher


class AgentAssure:
    def __init__(
        self,
        policy_path: Optional[str] = None,
        db_path: str = "agentassure.db",
        agent_id: str = "loan-agent",
        environment: str = "demo",
        detectors: Optional[List[BaseDetector]] = None,
        publisher: Optional[EventPublisher] = None,
    ):
        self.agent_id = agent_id
        self.environment = environment
        self.session_id = "default_session"
        self.db_path = db_path

        if policy_path:
            self.policy_engine = PolicyEngine.load_from_yaml(policy_path)
        else:
            self.policy_engine = PolicyEngine()

        if detectors is None:
            detectors = [PIIDetector(), SecretDetector(), RestrictedToolDetector()]

        self.detectors = detectors
        self.enforcement_engine = EnforcementEngine(self.policy_engine, self.detectors)
        self.evidence_store = EvidenceStore(db_path)
        self.approval_store = ApprovalStore(db_path)
        self.publisher = publisher or default_event_publisher

    def set_session(self, session_id: str):
        self.session_id = session_id

    def _record_and_publish(
        self,
        event: AgentEvent,
        policy_id: Optional[str] = None,
        policy_version: Optional[int] = None,
        decision: Optional[str] = None,
        reason: Optional[str] = None,
        controls: Optional[str] = None,
        risk_score: float = 0.0,
    ) -> EvidenceRecord:
        """Persist evidence record first, then publish live event notification."""
        rec = self.evidence_store.record_event(
            event=event,
            policy_id=policy_id,
            policy_version=policy_version,
            decision=decision,
            reason=reason,
            controls=controls,
            risk_score=risk_score,
        )

        payload = event.to_dict()
        payload["record_id"] = rec.record_id
        payload["record_hash"] = rec.record_hash
        payload["decision"] = decision
        payload["policy_id"] = policy_id
        payload["policy_version"] = policy_version
        payload["reason"] = reason
        payload["controls"] = controls

        try:
            self.publisher.publish(payload)
        except Exception as e:
            # Publisher error must NEVER break runtime or cause evidence loss
            log_runtime(
                level="WARN",
                component="publisher",
                message=f"Live delivery skipped due to publish error: {e}",
                trace_id=event.trace_id,
                span_id=event.span_id,
                event_id=event.event_id,
            )

        return rec

    def resolve_approval(
        self,
        approval_id: str,
        status: Union[str, ApprovalStatus],
        resolved_by: Optional[str] = "operator",
    ) -> Optional[ApprovalRequest]:
        """Resolves a pending approval, emits APPROVAL_RESOLVED event, and updates state."""
        if isinstance(status, str):
            status = ApprovalStatus(status.upper())

        resolved = self.approval_store.resolve_approval(approval_id, status, resolved_by)
        if resolved:
            resolve_event = AgentEvent(
                trace_id=resolved.trace_id,
                span_id=resolved.span_id,
                agent_id=resolved.agent_id,
                session_id=resolved.session_id,
                event_type=EventType.APPROVAL_RESOLVED,
                tool_name=resolved.tool_name,
                input=resolved.tool_arguments,
                status=EventStatus.COMPLETED if status == ApprovalStatus.APPROVED else EventStatus.BLOCKED,
                metadata={
                    "approval_id": resolved.approval_id,
                    "resolution": status.value,
                    "resolved_by": resolved_by,
                },
            )
            self._record_and_publish(
                event=resolve_event,
                policy_id=resolved.policy_id,
                policy_version=resolved.policy_version,
                decision=status.value,
                reason=f"Governance action {status.value} by {resolved_by}",
            )
            log_runtime(
                level="INFO",
                component="approval",
                message=f"Approval {approval_id} resolved as {status.value} by {resolved_by}",
                trace_id=resolved.trace_id,
                span_id=resolved.span_id,
                event_id=resolve_event.event_id,
                decision=status.value,
                tool_name=resolved.tool_name,
            )
        return resolved

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

            log_runtime(
                level="INFO",
                component="runtime",
                message=f"Tool call requested: {name}",
                trace_id=trace_id,
                span_id=span_id,
                tool_name=name,
            )

            # Check if this exact action already has an approval decision for this trace
            if self.approval_store.is_action_approved(trace_id, name):
                log_runtime(
                    level="INFO",
                    component="approval",
                    message=f"Tool '{name}' execution permitted by prior approved sign-off",
                    trace_id=trace_id,
                    span_id=span_id,
                    decision="ALLOW",
                    tool_name=name,
                )
                # Action approved -> execute underlying tool directly
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
                    metadata={"approval_override": True},
                )
                self._record_and_publish(
                    event=start_event,
                    decision="ALLOW",
                    reason="Pre-approved by operator",
                )
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
                    metadata={"approval_override": True},
                )
                self._record_and_publish(
                    event=result_event,
                    decision="ALLOW",
                    reason="Completed following approved sign-off",
                )
                return result

            if self.approval_store.is_action_rejected(trace_id, name):
                log_runtime(
                    level="WARN",
                    component="approval",
                    message=f"Tool '{name}' blocked: approval was rejected by operator",
                    trace_id=trace_id,
                    span_id=span_id,
                    decision="BLOCK",
                    tool_name=name,
                )
                rejected_decision = PolicyDecision(
                    outcome=PolicyOutcome.BLOCK,
                    policy_id="APPROVAL-REJECTED",
                    policy_version=1,
                    reason=f"Action '{name}' was rejected by operator",
                )
                raise PolicyViolationError(rejected_decision)

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
            log_runtime(
                level="INFO",
                component="policy",
                message="Pre-execution policy evaluation started",
                trace_id=trace_id,
                span_id=span_id,
                event_id=start_event.event_id,
                tool_name=name,
            )

            decision: PolicyDecision = self.enforcement_engine.evaluate_action(
                agent_id=self.agent_id,
                environment=self.environment,
                tool_name=name,
                tool_input=tool_input,
            )

            # 3. Record policy evaluation evidence & publish
            self._record_and_publish(
                event=start_event,
                policy_id=decision.policy_id,
                policy_version=decision.policy_version,
                decision=decision.outcome.value,
                reason=decision.reason,
                controls=decision.controls.as_citation() if decision.controls else None,
            )

            # 4. Pre-execution Interception: BLOCK
            if decision.outcome == PolicyOutcome.BLOCK:
                log_runtime(
                    level="WARN",
                    component="policy",
                    message=f"Policy matched: {decision.reason}",
                    trace_id=trace_id,
                    span_id=span_id,
                    event_id=start_event.event_id,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision="BLOCK",
                    tool_name=name,
                )
                log_runtime(
                    level="INFO",
                    component="enforcement",
                    message="Underlying tool execution skipped",
                    trace_id=trace_id,
                    span_id=span_id,
                    event_id=start_event.event_id,
                    decision="BLOCK",
                    tool_name=name,
                )

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
                self._record_and_publish(
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
                approval = self.approval_store.create_approval(
                    trace_id=trace_id,
                    span_id=span_id,
                    event_id=start_event.event_id,
                    tool_name=name,
                    tool_arguments=tool_input,
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    reason=decision.reason,
                )

                log_runtime(
                    level="WARN",
                    component="approval",
                    message=f"Tool execution requires approval [{approval.approval_id}]: {decision.reason}",
                    trace_id=trace_id,
                    span_id=span_id,
                    event_id=start_event.event_id,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision="ASK",
                    tool_name=name,
                )

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
                    metadata={
                        "decision": decision.outcome.value,
                        "reason": decision.reason,
                        "approval_id": approval.approval_id,
                    },
                )
                self._record_and_publish(
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
                log_runtime(
                    level="INFO",
                    component="enforcement",
                    message=f"Action permitted: executing tool '{name}'",
                    trace_id=trace_id,
                    span_id=span_id,
                    event_id=start_event.event_id,
                    decision=decision.outcome.value,
                    tool_name=name,
                )
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
                self._record_and_publish(
                    event=result_event,
                    policy_id=decision.policy_id,
                    policy_version=decision.policy_version,
                    decision=decision.outcome.value,
                    reason=decision.reason,
                    controls=decision.controls.as_citation() if decision.controls else None,
                )
                return result
            except Exception as e:
                # Distinguish between governance exceptions and unexpected runtime faults
                if not isinstance(e, (PolicyViolationError, PendingApprovalException)):
                    log_runtime(
                        level="ERROR",
                        component="runtime",
                        message=f"Tool execution failed with error: {str(e)}",
                        trace_id=trace_id,
                        span_id=span_id,
                        event_id=start_event.event_id,
                        tool_name=name,
                    )
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
                    self._record_and_publish(event=error_event)
                raise e

        return wrapper

    def wrap(self, agent_obj: Any) -> Any:
        """
        Generic agent wrapper. Scans for methods starting with tool_ or registered_tools.
        """
        for attr_name in dir(agent_obj):
            if attr_name.startswith("tool_") or attr_name in getattr(agent_obj, "registered_tools", []):
                attr = getattr(agent_obj, attr_name)
                if callable(attr):
                    setattr(agent_obj, attr_name, self.wrap_tool(attr, tool_name=attr_name))
        return agent_obj
