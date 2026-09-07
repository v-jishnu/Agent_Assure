"""
Pre-Execution Enforcement Engine for AgentAssure
"""

from typing import Any, Dict, List, Optional
from agentassure.policy import (
    PolicyEngine, PolicyDecision, PolicyOutcome, SeverityLevel, ControlMapping,
)
from agentassure.detectors import BaseDetector, DetectorSignal


class PolicyViolationError(Exception):
    """
    Raised when a tool execution is blocked by policy enforcement.
    """
    def __init__(self, decision: PolicyDecision):
        self.decision = decision
        super().__init__(f"Tool execution BLOCKED by policy [{decision.policy_id or 'UNKNOWN'}]: {decision.reason}")


class PendingApprovalException(Exception):
    """
    Raised when a tool execution requires human approval before proceeding.
    """
    def __init__(self, decision: PolicyDecision):
        self.decision = decision
        super().__init__(f"Tool execution PENDING APPROVAL [{decision.policy_id or 'UNKNOWN'}]: {decision.reason}")


class EnforcementEngine:
    def __init__(self, policy_engine: PolicyEngine, detectors: Optional[List[BaseDetector]] = None):
        self.policy_engine = policy_engine
        self.detectors = detectors or []

    def evaluate_action(
        self,
        agent_id: str,
        environment: str,
        tool_name: str,
        tool_input: Dict[str, Any]
    ) -> PolicyDecision:

        # 1. Run detectors first
        for detector in self.detectors:
            signal: Optional[DetectorSignal] = detector.detect(tool_name, tool_input)
            if signal and signal.detected:
                if signal.severity in ("high", "critical"):
                    return PolicyDecision(
                        outcome=PolicyOutcome.BLOCK,
                        policy_id=f"DETECTOR-{signal.detector_name.upper()}",
                        policy_version=1,
                        reason=f"Security detector alert: {signal.reason}",
                        severity=SeverityLevel.HIGH,
                        # A detector carries its own mapping, so a detector
                        # block is as citable as a policy block.
                        controls=signal.controls,
                        metadata=signal.details
                    )

        # 2. Evaluate Policy Engine rules and capabilities
        return self.policy_engine.evaluate(
            agent_id=agent_id,
            environment=environment,
            tool_name=tool_name,
            tool_input=tool_input
        )

    def enforce(self, decision: PolicyDecision) -> PolicyDecision:
        if decision.outcome == PolicyOutcome.BLOCK:
            raise PolicyViolationError(decision)
        elif decision.outcome == PolicyOutcome.ASK:
            raise PendingApprovalException(decision)
        return decision
