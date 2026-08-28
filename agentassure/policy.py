"""
Declarative Policy Engine for AgentAssure
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Union
import yaml
from pydantic import BaseModel, Field, field_validator


class PolicyOutcome(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ASK = "ASK"
    SHADOW = "SHADOW"


class PolicyMode(str, Enum):
    ENFORCE = "enforce"
    AUDIT = "audit"
    DISABLED = "disabled"


class SeverityLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PolicyScope(BaseModel):
    agent: Optional[Union[str, List[str]]] = None
    environment: Optional[Union[str, List[str]]] = None
    role: Optional[Union[str, List[str]]] = None
    tools: Optional[List[str]] = None
    tool_category: Optional[str] = None
    data_classification: Optional[str] = None


class PolicyCondition(BaseModel):
    field: str
    operator: str  # gt, lt, eq, ne, gte, lte, in, contains
    value: Any


class PolicyTrigger(BaseModel):
    event: str = "tool.pre_execute"


class PolicyRule(BaseModel):
    id: str
    name: str
    description: Optional[str] = ""
    version: int = 1
    scope: Optional[PolicyScope] = None
    trigger: Optional[PolicyTrigger] = Field(default_factory=PolicyTrigger)
    condition: Optional[PolicyCondition] = None
    severity: SeverityLevel = SeverityLevel.MEDIUM
    action: PolicyOutcome = PolicyOutcome.BLOCK
    mode: PolicyMode = PolicyMode.ENFORCE

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, v):
        if isinstance(v, str):
            return v.upper()
        return v

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v

    @field_validator("severity", mode="before")
    @classmethod
    def normalize_severity(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v




class CapabilitiesPolicy(BaseModel):
    allowed: List[str] = Field(default_factory=list)
    approval_required: List[str] = Field(default_factory=list)
    forbidden: List[str] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    outcome: PolicyOutcome
    policy_id: Optional[str] = None
    policy_version: Optional[int] = None
    reason: str
    severity: SeverityLevel = SeverityLevel.LOW
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PolicyEngine:
    def __init__(self, rules: Optional[List[PolicyRule]] = None, capabilities: Optional[CapabilitiesPolicy] = None):
        self.rules: List[PolicyRule] = rules or []
        self.capabilities: CapabilitiesPolicy = capabilities or CapabilitiesPolicy()

    @classmethod
    def load_from_yaml(cls, filepath: str) -> "PolicyEngine":
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            raise ValueError(f"Failed to load policy file {filepath}: {str(e)}")

        return cls.load_from_dict(data)

    @classmethod
    def load_from_dict(cls, data: Dict[str, Any]) -> "PolicyEngine":
        rules = []
        for r in data.get("policies", []):
            rules.append(PolicyRule.model_validate(r))

        caps_data = data.get("capabilities", {})
        capabilities = CapabilitiesPolicy.model_validate(caps_data)

        return cls(rules=rules, capabilities=capabilities)

    def evaluate_scope(self, scope: Optional[PolicyScope], agent_id: str, environment: str, tool_name: Optional[str]) -> bool:
        if not scope:
            return True

        if scope.agent:
            agents = [scope.agent] if isinstance(scope.agent, str) else scope.agent
            if agent_id not in agents:
                return False

        if scope.environment:
            envs = [scope.environment] if isinstance(scope.environment, str) else scope.environment
            if environment not in envs:
                return False

        if scope.tools and tool_name:
            if tool_name not in scope.tools:
                return False

        return True

    def _extract_field_value(self, field_path: str, context: Dict[str, Any]) -> Any:
        parts = field_path.split(".")
        curr = context
        for part in parts:
            if isinstance(curr, dict):
                if part in curr:
                    curr = curr[part]
                elif f"args.{part}" in field_path and "args" not in context:
                    # Fallback if field is args.X but context is direct input dict
                    return context.get(part)
                else:
                    return None
            else:
                return None
        return curr

    def evaluate_condition(self, condition: PolicyCondition, context: Dict[str, Any]) -> bool:
        val = self._extract_field_value(condition.field, context)
        if val is None and not condition.field.startswith("args.") and "args" in context:
            val = self._extract_field_value(f"args.{condition.field}", context)

        target = condition.value
        op = condition.operator.lower()

        if val is None:
            return False

        try:
            if op == "eq":
                return val == target
            elif op == "ne":
                return val != target
            elif op == "gt":
                return float(val) > float(target)
            elif op == "gte":
                return float(val) >= float(target)
            elif op == "lt":
                return float(val) < float(target)
            elif op == "lte":
                return float(val) <= float(target)
            elif op == "in":
                return val in target
            elif op == "contains":
                return target in val
        except (ValueError, TypeError):
            return False

        return False

    def evaluate_capabilities(self, tool_name: str) -> Optional[PolicyDecision]:
        if tool_name in self.capabilities.forbidden:
            return PolicyDecision(
                outcome=PolicyOutcome.BLOCK,
                policy_id="CAPABILITY-FORBIDDEN",
                policy_version=1,
                reason=f"Tool '{tool_name}' is forbidden by capability policy",
                severity=SeverityLevel.HIGH,
            )

        if tool_name in self.capabilities.approval_required:
            return PolicyDecision(
                outcome=PolicyOutcome.ASK,
                policy_id="CAPABILITY-APPROVAL-REQUIRED",
                policy_version=1,
                reason=f"Tool '{tool_name}' requires human approval by capability policy",
                severity=SeverityLevel.MEDIUM,
            )

        return None

    def evaluate(self, agent_id: str, environment: str, tool_name: str, tool_input: Dict[str, Any]) -> PolicyDecision:
        # 1. Check capability policies first
        cap_decision = self.evaluate_capabilities(tool_name)
        if cap_decision:
            return cap_decision

        # Context built for condition evaluation (both direct input and args.* wrapper)
        eval_context = {
            "args": tool_input if isinstance(tool_input, dict) else {},
            "input": tool_input,
        }
        if isinstance(tool_input, dict):
            eval_context.update(tool_input)

        # 2. Check rules in order
        for rule in self.rules:
            if rule.mode == PolicyMode.DISABLED:
                continue

            if not self.evaluate_scope(rule.scope, agent_id, environment, tool_name):
                continue

            if rule.condition:
                if not self.evaluate_condition(rule.condition, eval_context):
                    continue

            # Rule matches!
            reason = f"Policy '{rule.name}' ({rule.id} v{rule.version}) matched"
            if rule.condition:
                field_val = self._extract_field_value(rule.condition.field, eval_context)
                reason += f": field '{rule.condition.field}'={field_val} {rule.condition.operator} {rule.condition.value}"

            outcome = rule.action
            if rule.mode == PolicyMode.AUDIT and outcome in (PolicyOutcome.BLOCK, PolicyOutcome.ASK):
                outcome = PolicyOutcome.SHADOW

            return PolicyDecision(
                outcome=outcome,
                policy_id=rule.id,
                policy_version=rule.version,
                reason=reason,
                severity=rule.severity,
                metadata={"rule_name": rule.name, "mode": rule.mode.value},
            )

        # Default ALLOW
        return PolicyDecision(
            outcome=PolicyOutcome.ALLOW,
            policy_id=None,
            policy_version=None,
            reason="No restrictive policies triggered; action permitted",
            severity=SeverityLevel.LOW,
        )
