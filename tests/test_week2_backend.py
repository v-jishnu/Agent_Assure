"""
Week 2 Backend & Runtime Foundation Integration Tests for AgentAssure
"""

import os
import sqlite3
import tempfile
import pytest
from fastapi.testclient import TestClient

from agentassure.events import EventType, EventStatus
from agentassure.policy import (
    CapabilitiesPolicy,
    PolicyCondition,
    PolicyEngine,
    PolicyOutcome,
    PolicyRule,
    PolicyScope,
)
from agentassure.sdk import AgentAssure, PolicyViolationError, PendingApprovalException
import server.api as api_module
from server.api import app, evidence_store, approval_store


@pytest.fixture
def temp_environment():
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".db")
    db_path = f.name
    f.close()

    old_db = api_module.DB_PATH
    api_module.DB_PATH = db_path
    api_module.evidence_store = api_module.EvidenceStore(db_path)
    api_module.approval_store = api_module.ApprovalStore(db_path)

    # Set up sample policies
    rule_block = PolicyRule(
        id="RULE-BLOCK",
        name="Block excessive amount",
        version=1,
        scope=PolicyScope(tools=["disburse_loan"]),
        condition=PolicyCondition(field="amount", operator="gt", value=500000),
        action=PolicyOutcome.BLOCK,
    )
    rule_ask = PolicyRule(
        id="RULE-ASK",
        name="Require approval for high amount",
        version=1,
        scope=PolicyScope(tools=["disburse_loan"]),
        condition=PolicyCondition(field="amount", operator="gt", value=200000),
        action=PolicyOutcome.ASK,
    )
    test_policy_engine = PolicyEngine(rules=[rule_block, rule_ask])
    api_module.policy_engine = test_policy_engine

    assure = AgentAssure(db_path=db_path, agent_id="test-loan-agent")
    assure.enforcement_engine.policy_engine = test_policy_engine
    assure.policy_engine = test_policy_engine

    client = TestClient(app)

    yield {
        "db_path": db_path,
        "assure": assure,
        "client": client,
    }

    if os.path.exists(db_path):
        os.remove(db_path)
    api_module.DB_PATH = old_db


# -------------------------------------------------------------------------
# 1. WEEK 1 REGRESSION: ALLOW, BLOCK, ASK
# -------------------------------------------------------------------------
def test_week1_regression_allow_block_ask(temp_environment):
    assure = temp_environment["assure"]
    counter = {"exec": 0}

    def disburse_loan(amount: float, customer: str):
        counter["exec"] += 1
        return {"status": "PAID", "amount": amount, "customer": customer}

    governed = assure.wrap_tool(disburse_loan, tool_name="disburse_loan")

    # 1. ALLOW (amount = 100,000 <= 200,000)
    res = governed(amount=100000, customer="CUST-1")
    assert res["status"] == "PAID"
    assert counter["exec"] == 1

    # 2. ASK (amount = 300,000 > 200,000)
    with pytest.raises(PendingApprovalException) as exc_ask:
        governed(amount=300000, customer="CUST-2")
    assert exc_ask.value.decision.outcome == PolicyOutcome.ASK
    assert counter["exec"] == 1  # Guaranteed: function did NOT execute

    # 3. BLOCK (amount = 800,000 > 500,000)
    with pytest.raises(PolicyViolationError) as exc_block:
        governed(amount=800000, customer="CUST-3")
    assert exc_block.value.decision.outcome == PolicyOutcome.BLOCK
    assert counter["exec"] == 1  # Guaranteed: function did NOT execute


# -------------------------------------------------------------------------
# 2. PERSISTENCE WITHOUT UI
# -------------------------------------------------------------------------
def test_persistence_without_ui(temp_environment):
    assure = temp_environment["assure"]
    db_path = temp_environment["db_path"]

    def check_score(customer: str):
        return {"score": 740}

    governed = assure.wrap_tool(check_score, tool_name="check_score")
    governed(customer="CUST-99")

    # Verify directly from SQLite
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT count(*) FROM evidence_records")
    count = cursor.fetchone()[0]
    conn.close()

    assert count >= 2  # tool_call started + tool_result completed
    is_valid, errors = assure.evidence_store.verify_integrity()
    assert is_valid is True
    assert len(errors) == 0


# -------------------------------------------------------------------------
# 3. LIVE WEBSOCKET DELIVERY & 4. TRACE IDENTITY CONSISTENCY
# -------------------------------------------------------------------------
def test_live_delivery_and_trace_identity(temp_environment):
    assure = temp_environment["assure"]
    client = temp_environment["client"]

    def transfer(amount: float):
        return {"status": "SUCCESS"}

    governed = assure.wrap_tool(transfer, tool_name="transfer")

    with client.websocket_connect("/ws/events") as websocket:
        init_msg = websocket.receive_json()
        assert init_msg["type"] == "connected"

        # Execute governed tool
        res = governed(amount=50000)
        assert res["status"] == "SUCCESS"

        # Receive started event
        start_event = websocket.receive_json()
        assert start_event["event_type"] == "tool_call"
        assert start_event["tool_name"] == "transfer"
        trace_id = start_event["trace_id"]
        span_id = start_event["span_id"]
        event_id = start_event["event_id"]

        # Receive completed event
        result_event = websocket.receive_json()
        assert result_event["event_type"] == "tool_result"
        assert result_event["trace_id"] == trace_id

        # Verify trace identity consistency against REST API
        rest_resp = client.get(f"/traces/{trace_id}/events")
        assert rest_resp.status_code == 200
        rest_events = rest_resp.json()

        assert any(e["event_id"] == event_id for e in rest_events)
        matched_rest = next(e for e in rest_events if e["event_id"] == event_id)
        assert matched_rest["trace_id"] == trace_id
        assert matched_rest["span_id"] == span_id


# -------------------------------------------------------------------------
# 5. LOG CORRELATION
# -------------------------------------------------------------------------
def test_log_correlation(temp_environment):
    assure = temp_environment["assure"]
    client = temp_environment["client"]

    def risk_eval(customer: str):
        return {"risk": "LOW"}

    governed = assure.wrap_tool(risk_eval, tool_name="risk_eval")
    governed(customer="CUST-CORR")

    records = assure.evidence_store.get_records()
    latest_rec = records[-1]

    # Query logs by trace_id
    resp = client.get(f"/logs?trace_id={latest_rec.trace_id}")
    assert resp.status_code == 200
    logs = resp.json()
    assert len(logs) > 0
    assert all(l["trace_id"] == latest_rec.trace_id for l in logs if l.get("trace_id"))


# -------------------------------------------------------------------------
# 6. APPROVAL BACKEND PRIMITIVES & LIFECYCLE
# -------------------------------------------------------------------------
def test_approval_lifecycle_and_enforcement(temp_environment):
    assure = temp_environment["assure"]
    client = temp_environment["client"]
    counter = {"exec": 0}

    def disburse_loan(amount: float, customer: str):
        counter["exec"] += 1
        return {"status": "PAID", "amount": amount}

    governed = assure.wrap_tool(disburse_loan, tool_name="disburse_loan")

    # Step 1: Trigger ASK -> PendingApprovalException
    with pytest.raises(PendingApprovalException):
        governed(amount=250000, customer="CUST-ASK")

    assert counter["exec"] == 0  # Function did not execute!

    # Step 2: Query /approvals
    resp = client.get("/approvals?status=PENDING")
    assert resp.status_code == 200
    approvals = resp.json()
    assert len(approvals) >= 1

    appr = approvals[0]
    appr_id = appr["approval_id"]
    trace_id = appr["trace_id"]
    assert appr["status"] == "PENDING"
    assert appr["tool_name"] == "disburse_loan"

    # Step 3: Approve via POST /approvals/{id}/approve
    approve_resp = client.post(
        f"/approvals/{appr_id}/approve",
        json={"resolved_by": "credit_head", "comment": "Verified"},
    )
    assert approve_resp.status_code == 200
    approved_data = approve_resp.json()
    assert approved_data["status"] == "APPROVED"
    assert approved_data["resolved_by"] == "credit_head"

    # Verify double approval returns 400
    bad_approve = client.post(f"/approvals/{appr_id}/approve")
    assert bad_approve.status_code == 400

    # Step 4: Tool execution follows the approved decision!
    from agentassure.trace import TraceContext
    with TraceContext(trace_id=trace_id):
        # Retry/continue the governed tool under the same trace
        res = governed(amount=250000, customer="CUST-ASK")
        assert res["status"] == "PAID"
        assert counter["exec"] == 1  # Now executes!


def test_approval_rejection_blocks_execution(temp_environment):
    assure = temp_environment["assure"]
    client = temp_environment["client"]
    counter = {"exec": 0}

    def disburse_loan(amount: float, customer: str):
        counter["exec"] += 1
        return {"status": "PAID"}

    governed = assure.wrap_tool(disburse_loan, tool_name="disburse_loan")

    # Trigger ASK
    with pytest.raises(PendingApprovalException):
        governed(amount=250000, customer="CUST-REJ")

    approvals = client.get("/approvals?status=PENDING").json()
    appr_id = approvals[0]["approval_id"]
    trace_id = approvals[0]["trace_id"]

    # Reject
    rej_resp = client.post(
        f"/approvals/{appr_id}/reject",
        json={"resolved_by": "risk_officer", "comment": "Too risky"},
    )
    assert rej_resp.status_code == 200
    assert rej_resp.json()["status"] == "REJECTED"

    # Protected tool execution must be blocked by rejection!
    from agentassure.trace import TraceContext
    with TraceContext(trace_id=trace_id):
        with pytest.raises(PolicyViolationError) as exc_blocked:
            governed(amount=250000, customer="CUST-REJ")
        assert "rejected" in exc_blocked.value.decision.reason.lower()
        assert counter["exec"] == 0  # Still did not execute!


# -------------------------------------------------------------------------
# 7. EVIDENCE INTEGRITY AND TAMPER DETECTION
# -------------------------------------------------------------------------
def test_evidence_integrity_verification_endpoint(temp_environment):
    assure = temp_environment["assure"]
    client = temp_environment["client"]
    db_path = temp_environment["db_path"]

    def simple_action(val: int):
        return {"val": val}

    governed = assure.wrap_tool(simple_action, tool_name="simple_action")
    governed(val=42)

    records = assure.evidence_store.get_records()
    trace_id = records[0].trace_id

    # 1. Valid trace verification
    verify_resp = client.get(f"/evidence/{trace_id}/verify")
    assert verify_resp.status_code == 200
    data = verify_resp.json()
    assert data["valid"] is True
    assert data["records_checked"] >= 2
    assert "successfully" in data["message"]

    # 2. Tamper with the SQLite database
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("UPDATE evidence_records SET reason = 'TAMPERED_TEXT' WHERE id = 1")
    conn.commit()
    conn.close()

    # 3. Verification must now detect tampering!
    verify_tampered = client.get(f"/evidence/{trace_id}/verify")
    assert verify_tampered.status_code == 200
    tampered_data = verify_tampered.json()
    assert tampered_data["valid"] is False
    assert len(tampered_data["errors"]) > 0


# -------------------------------------------------------------------------
# 8. REST 404 AND ERROR RESPONSES
# -------------------------------------------------------------------------
def test_rest_404_responses(temp_environment):
    client = temp_environment["client"]

    assert client.get("/traces/non_existent_trace").status_code == 404
    assert client.get("/traces/non_existent_trace/events").status_code == 404
    assert client.get("/events/non_existent_event").status_code == 404
    assert client.get("/policies/UNKNOWN-POLICY").status_code == 404
    assert client.get("/approvals/non_existent_approval").status_code == 404
    assert client.get("/evidence/non_existent_trace").status_code == 404
    assert client.get("/evidence/non_existent_trace/verify").status_code == 404
