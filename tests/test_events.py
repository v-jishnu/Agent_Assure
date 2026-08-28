"""
Unit Tests for AgentEvent Schema and Serialization
"""

import pytest
from agentassure.events import AgentEvent, EventType, EventStatus, generate_event_id


def test_event_creation():
    event = AgentEvent(
        trace_id="tr_12345",
        span_id="sp_67890",
        agent_id="loan-agent",
        session_id="sess_abc",
        event_type=EventType.TOOL_CALL,
        tool_name="approve_loan",
        input={"amount": 200000},
        status=EventStatus.STARTED,
    )

    assert event.event_id.startswith("evt_")
    assert event.trace_id == "tr_12345"
    assert event.span_id == "sp_67890"
    assert event.event_type == EventType.TOOL_CALL
    assert event.input == {"amount": 200000}


def test_event_serialization_roundtrip():
    event = AgentEvent(
        trace_id="tr_999",
        span_id="sp_888",
        parent_span_id="sp_777",
        agent_id="test_agent",
        session_id="test_sess",
        event_type=EventType.POLICY_DECISION,
        tool_name="transfer_money",
        input={"recipient": "ACC-123"},
        output=None,
        status=EventStatus.BLOCKED,
        metadata={"policy_id": "FIN-001"},
    )

    d = event.to_dict()
    assert d["event_type"] == "policy_decision"
    assert d["status"] == "blocked"

    reconstructed = AgentEvent.from_dict(d)
    assert reconstructed.event_id == event.event_id
    assert reconstructed.trace_id == event.trace_id
    assert reconstructed.parent_span_id == "sp_777"
    assert reconstructed.status == EventStatus.BLOCKED
