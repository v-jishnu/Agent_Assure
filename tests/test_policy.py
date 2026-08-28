"""
Unit Tests for Policy Engine, Scopes, Conditions, and Capability Rules
"""

import pytest
import os
import tempfile
import yaml
from agentassure.policy import (
    PolicyEngine,
    PolicyRule,
    PolicyScope,
    PolicyCondition,
    CapabilitiesPolicy,
    PolicyOutcome,
    PolicyMode,
    SeverityLevel,
)


def test_scope_evaluation():
    engine = PolicyEngine()
    scope = PolicyScope(agent="loan-agent", environment="demo", tools=["approve_loan"])

    assert engine.evaluate_scope(scope, agent_id="loan-agent", environment="demo", tool_name="approve_loan") is True
    assert engine.evaluate_scope(scope, agent_id="other-agent", environment="demo", tool_name="approve_loan") is False
    assert engine.evaluate_scope(scope, agent_id="loan-agent", environment="prod", tool_name="approve_loan") is False
    assert engine.evaluate_scope(scope, agent_id="loan-agent", environment="demo", tool_name="get_customer") is False


def test_condition_evaluation_operators():
    engine = PolicyEngine()

    cond_gt = PolicyCondition(field="amount", operator="gt", value=500000)
    assert engine.evaluate_condition(cond_gt, {"amount": 600000}) is True
    assert engine.evaluate_condition(cond_gt, {"amount": 400000}) is False

    cond_eq = PolicyCondition(field="status", operator="eq", value="PENDING")
    assert engine.evaluate_condition(cond_eq, {"status": "PENDING"}) is True
    assert engine.evaluate_condition(cond_eq, {"status": "APPROVED"}) is False

    cond_in = PolicyCondition(field="tier", operator="in", value=["VIP", "PREMIUM"])
    assert engine.evaluate_condition(cond_in, {"tier": "VIP"}) is True
    assert engine.evaluate_condition(cond_in, {"tier": "BASIC"}) is False


def test_capability_policy():
    caps = CapabilitiesPolicy(
        allowed=["get_customer"],
        approval_required=["transfer_money"],
        forbidden=["delete_customer"],
    )
    engine = PolicyEngine(capabilities=caps)

    decision_forbidden = engine.evaluate("loan-agent", "demo", "delete_customer", {})
    assert decision_forbidden.outcome == PolicyOutcome.BLOCK
    assert decision_forbidden.policy_id == "CAPABILITY-FORBIDDEN"

    decision_ask = engine.evaluate("loan-agent", "demo", "transfer_money", {})
    assert decision_ask.outcome == PolicyOutcome.ASK
    assert decision_ask.policy_id == "CAPABILITY-APPROVAL-REQUIRED"

    decision_allowed = engine.evaluate("loan-agent", "demo", "get_customer", {})
    assert decision_allowed.outcome == PolicyOutcome.ALLOW


def test_policy_yaml_loading_and_version():
    yaml_content = """
policies:
  - id: FIN-001
    name: Loan Limit Rule
    version: 2
    scope:
      agent: loan-agent
      tools: [approve_loan]
    condition:
      field: amount
      operator: gt
      value: 500000
    action: block
"""
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yaml") as f:
        f.write(yaml_content)
        f_path = f.name
    # File is now closed after with block


    try:
        engine = PolicyEngine.load_from_yaml(f_path)
        decision = engine.evaluate("loan-agent", "demo", "approve_loan", {"amount": 700000})
        assert decision.outcome == PolicyOutcome.BLOCK
        assert decision.policy_id == "FIN-001"
        assert decision.policy_version == 2
    finally:
        if os.path.exists(f_path):
            os.remove(f_path)


def test_invalid_policy_file_raises_error():
    with pytest.raises(ValueError):
        PolicyEngine.load_from_yaml("non_existent_policy_file.yaml")
