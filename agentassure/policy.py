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


class ControlMapping(BaseModel):
    """
    The external controls a policy rule produces evidence for.

    This is what separates a governance layer from a rules engine: when a rule
    fires, the resulting evidence record cites the named clause it evidences,
    so an assessor can trace a runtime decision back to the control framework
    the organisation is certified against.
    """
    iso42001: Optional[str] = None   # e.g. "A.9.4"
    eu_ai_act: Optional[str] = None  # e.g. "Art. 14"
    note: Optional[str] = None       # why this rule maps to that clause

    def as_citation(self) -> str:
        parts = []
        if self.iso42001:
            parts.append(f"ISO/IEC 42001 {self.iso42001}")
        if self.eu_ai_act:
            parts.append(f"EU AI Act {self.eu_ai_act}")
        return " | ".join(parts)


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
    controls: Optional[ControlMapping] = None

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
    # Capability boundaries are themselves a control, so they carry a mapping
    # too — otherwise a forbidden-tool block would be the only decision in the
    # evidence log without a clause behind it.
    controls: Optional[ControlMapping] = None


class PolicyDecision(BaseModel):
    outcome: PolicyOutcome
    policy_id: Optional[str] = None
    policy_version: Optional[int] = None
    reason: str
    severity: SeverityLevel = SeverityLevel.LOW
    controls: Optional[ControlMapping] = None
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
                controls=self.capabilities.controls,
            )

        if tool_name in self.capabilities.approval_required:
            return PolicyDecision(
                outcome=PolicyOutcome.ASK,
                policy_id="CAPABILITY-APPROVAL-REQUIRED",
                policy_version=1,
                reason=f"Tool '{tool_name}' requires human approval by capability policy",
                severity=SeverityLevel.MEDIUM,
                controls=self.capabilities.controls,
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

        # Inject cost and token usage from current trace if active
        try:
            from agentassure.trace import TraceContext
            from agentassure.cost import default_cost_tracker
            trace_id, _, _ = TraceContext.get_current()
            cost_rec = default_cost_tracker.get(trace_id)
            if cost_rec:
                eval_context["cost_usd"] = cost_rec.cost_usd
                eval_context["total_tokens"] = cost_rec.total_tokens
                eval_context["input_tokens"] = cost_rec.input_tokens
                eval_context["output_tokens"] = cost_rec.output_tokens
                eval_context["model"] = cost_rec.model
        except Exception:
            pass

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
                controls=rule.controls,
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

    def get_rule(self, policy_id: str) -> Optional[PolicyRule]:
        for r in self.rules:
            if r.id == policy_id:
                return r
        return None

    def add_or_update_rule(self, rule: PolicyRule) -> PolicyRule:
        """Adds a new rule or updates an existing rule by ID, automatically handling versioning."""
        for idx, existing in enumerate(self.rules):
            if existing.id == rule.id:
                # If version wasn't explicitly bumped higher than current, bump it by 1
                if rule.version <= existing.version:
                    rule.version = existing.version + 1
                self.rules[idx] = rule
                return rule
        self.rules.append(rule)
        return rule

    def toggle_rule(self, policy_id: str, enabled: bool) -> Optional[PolicyRule]:
        """Toggles a policy rule between ENFORCE and DISABLED."""
        rule = self.get_rule(policy_id)
        if not rule:
            return None
        rule.mode = PolicyMode.ENFORCE if enabled else PolicyMode.DISABLED
        return rule

    def save_to_yaml(self, filepath: str) -> None:
        """Saves current policy rules and capabilities back to a YAML file."""
        data = {
            "policies": [r.model_dump(mode="json", exclude_none=True) for r in self.rules],
            "capabilities": self.capabilities.model_dump(mode="json", exclude_none=True),
        }
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def validate_policy_rule(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validates a candidate policy rule definition.
    Catches missing required fields, invalid actions/modes, malformed conditions, and invalid threshold types.
    """
    errors: List[str] = []

    if not isinstance(data, dict):
        return {"valid": False, "errors": ["Policy definition must be a JSON object"]}

    policy_id = data.get("id")
    if not policy_id or not isinstance(policy_id, str) or not policy_id.strip():
        errors.append("Field 'id' is required and must be a non-empty string")

    name = data.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        errors.append("Field 'name' is required and must be a non-empty string")

    action = data.get("action")
    valid_actions = {"ALLOW", "BLOCK", "ASK", "SHADOW"}
    if action:
        if not isinstance(action, str) or action.upper() not in valid_actions:
            errors.append(f"Invalid action '{action}'. Must be one of: {', '.join(sorted(valid_actions))}")

    mode = data.get("mode")
    valid_modes = {"enforce", "audit", "disabled"}
    if mode:
        if not isinstance(mode, str) or mode.lower() not in valid_modes:
            errors.append(f"Invalid mode '{mode}'. Must be one of: {', '.join(sorted(valid_modes))}")

    severity = data.get("severity")
    valid_severities = {"low", "medium", "high", "critical"}
    if severity:
        if not isinstance(severity, str) or severity.lower() not in valid_severities:
            errors.append(f"Invalid severity '{severity}'. Must be one of: {', '.join(sorted(valid_severities))}")

    condition = data.get("condition")
    if condition is not None:
        if not isinstance(condition, dict):
            errors.append("Field 'condition' must be an object containing field, operator, and value")
        else:
            cond_field = condition.get("field")
            if not cond_field or not isinstance(cond_field, str) or not cond_field.strip():
                errors.append("Condition field 'field' is required and must be a non-empty string")

            cond_op = condition.get("operator")
            valid_ops = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "contains"}
            if not cond_op or not isinstance(cond_op, str) or cond_op.lower() not in valid_ops:
                errors.append(f"Invalid condition operator '{cond_op}'. Must be one of: {', '.join(sorted(valid_ops))}")
            else:
                op_lower = cond_op.lower()
                cond_val = condition.get("value")
                if cond_val is None:
                    errors.append("Condition field 'value' is required")
                elif op_lower in ("gt", "gte", "lt", "lte"):
                    try:
                        float(cond_val)
                    except (ValueError, TypeError):
                        errors.append(f"Condition value '{cond_val}' is not a valid number for operator '{cond_op}'")
                elif op_lower == "in":
                    if not isinstance(cond_val, (list, tuple)):
                        errors.append(f"Condition value for 'in' operator must be a list of values")

    # Final pydantic validation check
    try:
        PolicyRule.model_validate(data)
    except Exception as e:
        err_msg = str(e)
        if err_msg not in errors:
            errors.append(f"Pydantic validation error: {err_msg}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }

