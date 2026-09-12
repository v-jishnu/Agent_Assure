"""
Tests for AgentAssure Cost & Token Tracking.
"""

import pytest
from agentassure.cost import (
    CostTracker,
    CostRecord,
    estimate_cost,
    PRICING_TABLE,
    default_cost_tracker,
)
from agentassure.sdk import AgentAssure
from agentassure.policy import PolicyEngine, PolicyRule, PolicyCondition, PolicyOutcome, PolicyMode
from agentassure.enforcement import PolicyViolationError
from agentassure.trace import TraceContext


def test_estimate_cost():
    # Direct match
    cost_gpt4o = estimate_cost("gpt-4o", input_tokens=1000, output_tokens=1000)
    assert cost_gpt4o == 0.005 + 0.015

    # Prefix match
    cost_versioned = estimate_cost("gpt-4o-2024-11-20", input_tokens=2000, output_tokens=500)
    expected = (2000 * 0.005 / 1000) + (500 * 0.015 / 1000)
    assert pytest.approx(cost_versioned) == expected

    # Unknown model should not crash
    cost_unknown = estimate_cost("unknown-model-xyz", 100, 100)
    assert cost_unknown == 0.0


def test_cost_tracker():
    tracker = CostTracker()
    trace_id = "tr_test_cost_1"

    rec1 = tracker.record(trace_id, model="gpt-4o", input_tokens=500, output_tokens=100, latency_ms=250.0)
    assert rec1.total_tokens == 600
    assert rec1.llm_calls == 1

    rec2 = tracker.record(trace_id, model="gpt-4o", input_tokens=300, output_tokens=50, latency_ms=150.0)
    assert rec2.input_tokens == 800
    assert rec2.output_tokens == 150
    assert rec2.total_tokens == 950
    assert rec2.llm_calls == 2
    assert rec2.latency_ms == 400.0

    retrieved = tracker.get(trace_id)
    assert retrieved is not None
    assert retrieved.total_tokens == 950

    d = retrieved.to_dict()
    assert d["total_tokens"] == 950
    assert "cost_usd" in d

    tracker.clear(trace_id)
    assert tracker.get(trace_id) is None


def test_sdk_track_llm_call(tmp_path):
    db_path = str(tmp_path / "test_cost.db")
    aa = AgentAssure(db_path=db_path)

    trace_id = "tr_sdk_cost_1"
    with TraceContext(trace_id=trace_id):
        rec = aa.track_llm_call(
            model="gpt-4o",
            input_tokens=1000,
            output_tokens=200,
            latency_ms=300.0,
        )
        assert rec.total_tokens == 1200
        assert default_cost_tracker.get(trace_id).total_tokens == 1200


def test_policy_cost_condition_enforcement(tmp_path):
    db_path = str(tmp_path / "cost_enforcement.db")
    trace_id = "tr_cost_enforce"

    # Record usage that exceeds $0.05
    default_cost_tracker.record(trace_id, model="gpt-4", input_tokens=2000, output_tokens=1000)

    engine = PolicyEngine()
    cost_rule = PolicyRule(
        id="BUDGET-EXCEEDED",
        name="Block on budget overrun",
        action=PolicyOutcome.BLOCK,
        mode=PolicyMode.ENFORCE,
        condition=PolicyCondition(
            field="cost_usd",
            operator="gt",
            value=0.05,
        )
    )
    engine.add_or_update_rule(cost_rule)

    aa = AgentAssure(db_path=db_path)
    aa.policy_engine = engine
    aa.enforcement_engine.policy_engine = engine

    @aa.govern
    def execute_tool(param: str):
        return f"executed: {param}"

    with TraceContext(trace_id=trace_id):
        with pytest.raises(PolicyViolationError) as exc_info:
            execute_tool(param="test")

        assert "BUDGET-EXCEEDED" in str(exc_info.value)
