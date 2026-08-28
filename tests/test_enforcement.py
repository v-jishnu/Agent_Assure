"""
Unit Tests for Pre-Execution Enforcement Engine and Interception Mechanics
"""

import pytest
import os
import tempfile
from agentassure.sdk import AgentAssure
from agentassure.enforcement import PolicyViolationError, PendingApprovalException
from agentassure.policy import PolicyEngine, PolicyRule, PolicyScope, PolicyCondition, PolicyOutcome, CapabilitiesPolicy


def test_enforcement_allow_block_ask():
    # Setup test policy rules
    rule_block = PolicyRule(
        id="RULE-BLOCK",
        name="Block large amount",
        version=1,
        scope=PolicyScope(tools=["transfer_funds"]),
        condition=PolicyCondition(field="amount", operator="gt", value=10000),
        action=PolicyOutcome.BLOCK,
    )
    rule_ask = PolicyRule(
        id="RULE-ASK",
        name="Ask medium amount",
        version=1,
        scope=PolicyScope(tools=["transfer_funds"]),
        condition=PolicyCondition(field="amount", operator="gt", value=5000),
        action=PolicyOutcome.ASK,
    )

    policy_engine = PolicyEngine(rules=[rule_block, rule_ask])

    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".db")
    db_path = f.name
    f.close()


    try:
        assure = AgentAssure(db_path=db_path)
        assure.enforcement_engine.policy_engine = policy_engine

        execution_counter = {"count": 0}

        def transfer_funds(amount: float):
            execution_counter["count"] += 1
            return {"status": "SUCCESS", "transferred": amount}

        governed_transfer = assure.wrap_tool(transfer_funds, tool_name="transfer_funds")

        # 1. ALLOW (Amount: 1000 <= 5000)
        res = governed_transfer(amount=1000)
        assert res["status"] == "SUCCESS"
        assert execution_counter["count"] == 1

        # 2. ASK (Amount: 7000 > 5000) -> Pre-execution Interception
        with pytest.raises(PendingApprovalException) as exc_ask:
            governed_transfer(amount=7000)
        assert exc_ask.value.decision.outcome == PolicyOutcome.ASK
        assert execution_counter["count"] == 1  # Tool function MUST NOT HAVE EXECUTED!

        # 3. BLOCK (Amount: 15000 > 10000) -> Pre-execution Interception
        with pytest.raises(PolicyViolationError) as exc_block:
            governed_transfer(amount=15000)
        assert exc_block.value.decision.outcome == PolicyOutcome.BLOCK
        assert execution_counter["count"] == 1  # Tool function MUST NOT HAVE EXECUTED!

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
