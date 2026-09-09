"""
Week 2 Person 3 Verification & Integration Test Suite for AgentAssure
Tests policy studio backend, validation, hot reload, policy editing/versioning,
approval resolution workflows, evidence tampering detection, and correlation invariants.
"""

import os
import shutil
import tempfile
import pytest
from fastapi.testclient import TestClient

from agentassure import AgentAssure, TraceContext, PolicyViolationError, PendingApprovalException
from agentassure.approval import ApprovalStatus, ApprovalStore
from agentassure.evidence import EvidenceStore
from agentassure.policy import PolicyEngine, PolicyRule, validate_policy_rule
from server.api import app


@pytest.fixture
def test_env():
    """Sets up a temporary directory with test policy file and evidence DB."""
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_p3_evidence.db")
    policy_path = os.path.join(temp_dir, "test_p3_policy.yaml")

    # Initial test policy definition
    initial_yaml = """
policies:
  - id: FIN-001
    name: Loan approval limit
    description: Block loan approval requests exceeding ₹500,000.
    version: 1
    scope:
      agent: loan-agent
      environment: demo
      tools:
        - approve_loan
    trigger:
      event: tool.pre_execute
    condition:
      field: amount
      operator: gt
      value: 500000
    severity: high
    action: block
    mode: enforce
    controls:
      iso42001: "A.9.4"
      eu_ai_act: "Art. 14"

capabilities:
  allowed:
    - get_customer
    - check_credit
    - approve_loan
  approval_required: []
  forbidden:
    - delete_customer
"""
    with open(policy_path, "w", encoding="utf-8") as f:
        f.write(initial_yaml)

    old_db = os.environ.get("AGENTASSURE_DB")
    old_pol = os.environ.get("AGENTASSURE_POLICY")
    os.environ["AGENTASSURE_DB"] = db_path
    os.environ["AGENTASSURE_POLICY"] = policy_path

    # Patch server.api globals
    import server.api as api_mod
    old_api_db = api_mod.DB_PATH
    old_api_pol = api_mod.POLICY_PATH
    old_api_ev = api_mod.evidence_store
    old_api_appr = api_mod.approval_store
    old_api_pe = api_mod.policy_engine

    api_mod.DB_PATH = db_path
    api_mod.POLICY_PATH = policy_path
    api_mod.evidence_store = EvidenceStore(db_path)
    api_mod.approval_store = ApprovalStore(db_path)
    api_mod.policy_engine = PolicyEngine.load_from_yaml(policy_path)

    yield {
        "temp_dir": temp_dir,
        "db_path": db_path,
        "policy_path": policy_path,
    }

    # Restore server.api globals
    api_mod.DB_PATH = old_api_db
    api_mod.POLICY_PATH = old_api_pol
    api_mod.evidence_store = old_api_ev
    api_mod.approval_store = old_api_appr
    api_mod.policy_engine = old_api_pe

    # Cleanup env
    if old_db:
        os.environ["AGENTASSURE_DB"] = old_db
    else:
        os.environ.pop("AGENTASSURE_DB", None)

    if old_pol:
        os.environ["AGENTASSURE_POLICY"] = old_pol
    else:
        os.environ.pop("AGENTASSURE_POLICY", None)

    shutil.rmtree(temp_dir, ignore_errors=True)


# -------------------------------------------------------------------------
# 1. POLICY VALIDATION TESTS
# -------------------------------------------------------------------------
def test_policy_validation_valid_and_invalid():
    # Valid rule
    valid_rule = {
        "id": "VAL-001",
        "name": "Valid rule",
        "action": "BLOCK",
        "mode": "enforce",
        "severity": "high",
        "condition": {"field": "amount", "operator": "gt", "value": 100000},
    }
    res = validate_policy_rule(valid_rule)
    assert res["valid"] is True
    assert len(res["errors"]) == 0

    # Missing required id & name
    invalid_missing = {"action": "BLOCK"}
    res = validate_policy_rule(invalid_missing)
    assert res["valid"] is False
    assert any("id" in err for err in res["errors"])
    assert any("name" in err for err in res["errors"])

    # Invalid action and mode
    invalid_enum = {"id": "BAD-1", "name": "Bad Enum", "action": "INVALID_ACTION", "mode": "bad_mode"}
    res = validate_policy_rule(invalid_enum)
    assert res["valid"] is False
    assert any("Invalid action" in err for err in res["errors"])

    # Malformed condition operator and invalid threshold type
    invalid_cond = {
        "id": "BAD-2",
        "name": "Bad Condition",
        "condition": {"field": "amount", "operator": "gt", "value": "not_a_number"},
    }
    res = validate_policy_rule(invalid_cond)
    assert res["valid"] is False
    assert any("not a valid number" in err for err in res["errors"])


def test_api_policy_validation_endpoint(test_env):
    client = TestClient(app)
    # Test valid submission via API
    resp = client.post("/policies/validate", json={
        "id": "FIN-002",
        "name": "High value loan",
        "action": "ASK",
        "condition": {"field": "amount", "operator": "gt", "value": 300000}
    })
    assert resp.status_code == 200
    assert resp.json()["valid"] is True

    # Test invalid submission via API
    resp_invalid = client.post("/policies/validate", json={
        "id": "",
        "name": "Invalid",
        "action": "FOO"
    })
    assert resp_invalid.status_code == 200
    assert resp_invalid.json()["valid"] is False


# -------------------------------------------------------------------------
# 2. POLICY EDITING & VERSIONING TESTS
# -------------------------------------------------------------------------
def test_policy_editing_and_versioning(test_env):
    client = TestClient(app)

    # Initial get
    r = client.get("/policies/FIN-001")
    assert r.status_code == 200
    initial_rule = r.json()
    assert initial_rule["version"] == 1
    assert initial_rule["action"] == "BLOCK"

    # Update FIN-001: change threshold from 500k to 1M
    updated_payload = dict(initial_rule)
    updated_payload["condition"]["value"] = 1000000

    r_put = client.put("/policies/FIN-001", json=updated_payload)
    assert r_put.status_code == 200
    updated_data = r_put.json()
    assert updated_data["version"] == 2
    assert updated_data["condition"]["value"] == 1000000

    # Verify persistent get
    r_get = client.get("/policies/FIN-001")
    assert r_get.json()["version"] == 2
    assert r_get.json()["condition"]["value"] == 1000000


def test_policy_toggle_enable_disable(test_env):
    client = TestClient(app)

    # Toggle off
    r_off = client.post("/policies/FIN-001/toggle", json={"enabled": False})
    assert r_off.status_code == 200
    assert r_off.json()["mode"] == "disabled"

    # Toggle on
    r_on = client.post("/policies/FIN-001/toggle", json={"enabled": True})
    assert r_on.status_code == 200
    assert r_on.json()["mode"] == "enforce"


# -------------------------------------------------------------------------
# 3. POLICY CHANGE RUNTIME BEHAVIOR (SCENARIO D)
# -------------------------------------------------------------------------
def test_policy_change_runtime_behavior_block_to_ask(test_env):
    """
    Demonstrates externalized governance:
    Changing policy from BLOCK to ASK alters runtime agent behavior
    without modifying agent tool or business logic code!
    """
    db_path = test_env["db_path"]
    policy_path = test_env["policy_path"]

    assure = AgentAssure(
        policy_path=policy_path,
        db_path=db_path,
        agent_id="loan-agent",
        environment="demo",
    )

    call_count = 0
    def approve_loan(customer_id: str, amount: float):
        nonlocal call_count
        call_count += 1
        return {"status": "SUCCESS", "amount": amount}

    governed_tool = assure.wrap_tool(approve_loan, tool_name="approve_loan")

    # 1. Run request with amount = ₹800,000 under default FIN-001 (BLOCK)
    assure.set_session("sess_p3_block")
    with TraceContext() as trace1:
        with pytest.raises(PolicyViolationError) as exc_info:
            governed_tool(customer_id="CUST-102", amount=800000)

        assert exc_info.value.decision.outcome.value == "BLOCK"
        assert exc_info.value.decision.policy_id == "FIN-001"
        assert call_count == 0  # Function body never ran!

    # 2. Change policy FIN-001 in policy engine: BLOCK -> ASK
    fin_001 = assure.policy_engine.get_rule("FIN-001")
    fin_001.action = "ASK"
    assure.policy_engine.add_or_update_rule(fin_001)

    # 3. Run exact same request with amount = ₹800,000 (No agent code change!)
    assure.set_session("sess_p3_ask")
    with TraceContext() as trace2:
        with pytest.raises(PendingApprovalException) as exc_info2:
            governed_tool(customer_id="CUST-102", amount=800000)

        assert exc_info2.value.decision.outcome.value == "ASK"
        assert call_count == 0  # Function body still suspended!

        # Check pending approval was created in store
        approvals = assure.approval_store.list_approvals(trace_id=trace2.trace_id)
        assert len(approvals) == 1
        assert approvals[0].status == ApprovalStatus.PENDING


# -------------------------------------------------------------------------
# 4. APPROVAL WORKFLOW & SAFETY CHECKS
# -------------------------------------------------------------------------
def test_approval_resolution_approve_and_reject_flows(test_env):
    db_path = test_env["db_path"]
    policy_path = test_env["policy_path"]

    assure = AgentAssure(
        policy_path=policy_path,
        db_path=db_path,
        agent_id="loan-agent",
        environment="demo",
    )

    # Set FIN-001 to ASK mode
    fin_001 = assure.policy_engine.get_rule("FIN-001")
    fin_001.action = "ASK"
    assure.policy_engine.add_or_update_rule(fin_001)

    executed_count = 0
    def approve_loan(customer_id: str, amount: float):
        nonlocal executed_count
        executed_count += 1
        return {"status": "SUCCESS", "amount": amount}

    governed_tool = assure.wrap_tool(approve_loan, tool_name="approve_loan")

    # Flow A: Approval APPROVED
    with TraceContext() as trace_appr:
        try:
            governed_tool(customer_id="CUST-103", amount=600000)
        except PendingApprovalException:
            pass

        approvals = assure.approval_store.list_approvals(trace_id=trace_appr.trace_id)
        appr_id = approvals[0].approval_id

        # Resolve via assure
        resolved = assure.resolve_approval(appr_id, status=ApprovalStatus.APPROVED, resolved_by="test_officer")
        assert resolved.status == ApprovalStatus.APPROVED

        # Re-invoke under same trace context
        res = governed_tool(customer_id="CUST-103", amount=600000)
        assert res["status"] == "SUCCESS"
        assert executed_count == 1

    # Flow B: Approval REJECTED
    executed_count_b = 0
    def approve_loan_b(customer_id: str, amount: float):
        nonlocal executed_count_b
        executed_count_b += 1
        return {"status": "SUCCESS"}

    governed_tool_b = assure.wrap_tool(approve_loan_b, tool_name="approve_loan")

    with TraceContext() as trace_rej:
        try:
            governed_tool_b(customer_id="CUST-104", amount=700000)
        except PendingApprovalException:
            pass

        approvals_rej = assure.approval_store.list_approvals(trace_id=trace_rej.trace_id)
        appr_id_rej = approvals_rej[0].approval_id

        # Resolve via assure as REJECTED
        assure.resolve_approval(appr_id_rej, status=ApprovalStatus.REJECTED, resolved_by="test_officer")

        # Re-invoke -> must be BLOCKED
        with pytest.raises(PolicyViolationError):
            governed_tool_b(customer_id="CUST-104", amount=700000)

        assert executed_count_b == 0


def test_approval_safety_checks(test_env):
    client = TestClient(app)

    # 404 for unknown approval
    r_404 = client.post("/approvals/appr_non_existent/approve")
    assert r_404.status_code == 404

    # Create real approval in DB
    db_path = test_env["db_path"]
    appr_store = ApprovalStore(db_path)
    appr = appr_store.create_approval(
        trace_id="tr_safety_test",
        span_id="sp_safety_test",
        event_id="evt_safety_test",
        tool_name="approve_loan",
        tool_arguments={"amount": 400000},
        agent_id="loan-agent",
        session_id="sess_test",
        policy_id="FIN-002",
        policy_version=1,
        reason="Test approval",
    )

    # First approve via API
    r_ok = client.post(f"/approvals/{appr.approval_id}/approve", json={"resolved_by": "officer_1"})
    assert r_ok.status_code == 200
    assert r_ok.json()["status"] == "APPROVED"

    # Second approve attempt (double approval safety check) -> 400 error
    r_double = client.post(f"/approvals/{appr.approval_id}/approve", json={"resolved_by": "officer_2"})
    assert r_double.status_code == 400
    assert "Cannot approve" in r_double.json()["detail"]


# -------------------------------------------------------------------------
# 5. EVIDENCE TAMPER DETECTION & RECOVERY TEST
# -------------------------------------------------------------------------
def test_evidence_tamper_detection_and_recovery(test_env):
    db_path = test_env["db_path"]
    ev_store = EvidenceStore(db_path)

    # Record valid event
    rec = ev_store.record_event(
        event=type("DummyEvt", (), {
            "event_id": "evt_tamper_1",
            "trace_id": "tr_tamper_test",
            "span_id": "sp_tamper_1",
            "parent_span_id": None,
            "agent_id": "loan-agent",
            "session_id": "sess_tamper",
            "event_type": "tool_call",
            "timestamp": "2026-09-09T00:00:00+00:00",
            "tool_name": "approve_loan",
            "input": {"amount": 100000},
            "output": None,
            "redactions": 0,
        })()
    )

    # Check initially valid
    client = TestClient(app)
    v1 = client.get("/evidence/tr_tamper_test/verify")
    assert v1.status_code == 200
    assert v1.json()["valid"] is True

    # Tamper with record hash directly in SQLite
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE evidence_records SET record_hash = 'TAMPERED_HASH' WHERE record_id = ?", (rec.record_id,))
    conn.commit()
    conn.close()

    # Verification must report invalid!
    v2 = client.get("/evidence/tr_tamper_test/verify")
    assert v2.status_code == 200
    assert v2.json()["valid"] is False
    assert len(v2.json()["errors"]) > 0

    # Restore valid hash
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("UPDATE evidence_records SET record_hash = ? WHERE record_id = ?", (rec.record_hash, rec.record_id))
    conn.commit()
    conn.close()

    # Verification passes again!
    v3 = client.get("/evidence/tr_tamper_test/verify")
    assert v3.status_code == 200
    assert v3.json()["valid"] is True
