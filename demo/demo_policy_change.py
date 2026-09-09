"""
AgentAssure — Week 2 Person 3 Primary Demo Script
Demonstrates externalized governance policy change (BLOCK -> ASK) and human approval sign-off.
Proves runtime governance is external to agent business logic.
"""

import os
import sys

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Reconfigure encoding for Windows console
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from agentassure import AgentAssure, TraceContext, PolicyViolationError, PendingApprovalException
from agentassure.approval import ApprovalStatus


TOOL_EXECUTION_COUNTERS = {
    "get_customer": 0,
    "check_credit": 0,
    "calculate_loan": 0,
    "approve_loan": 0,
}


def get_customer(customer_id: str) -> dict:
    TOOL_EXECUTION_COUNTERS["get_customer"] += 1
    return {"customer_id": customer_id, "name": "Ravi Sharma", "credit_score": 720}


def check_credit(customer_id: str) -> dict:
    TOOL_EXECUTION_COUNTERS["check_credit"] += 1
    return {"customer_id": customer_id, "credit_score": 720, "status": "APPROVED"}


def calculate_loan(amount: float, term_months: int) -> dict:
    TOOL_EXECUTION_COUNTERS["calculate_loan"] += 1
    monthly = round((amount * 1.08) / term_months, 2)
    return {"amount": amount, "term_months": term_months, "monthly_payment": monthly}


def approve_loan(customer_id: str, amount: float, credit_score: int = 720) -> dict:
    TOOL_EXECUTION_COUNTERS["approve_loan"] += 1
    return {
        "status": "SUCCESS",
        "loan_id": f"LN-DEMO-{int(amount)}",
        "customer_id": customer_id,
        "approved_amount": amount,
    }


def main():
    db_file = "demo_evidence.db"
    policy_file = os.path.join(os.path.dirname(__file__), "..", "policies", "loan.yaml")

    print("=" * 78)
    print("      AGENTASSURE WEEK 2 — PRIMARY DEMO: EXTERNALIZED GOVERNANCE STORY")
    print("=" * 78)

    assure = AgentAssure(
        policy_path=policy_file,
        db_path=db_file,
        agent_id="loan-agent",
        environment="demo",
    )

    governed_get = assure.wrap_tool(get_customer, tool_name="get_customer")
    governed_check = assure.wrap_tool(check_credit, tool_name="check_credit")
    governed_calc = assure.wrap_tool(calculate_loan, tool_name="calculate_loan")
    governed_approve = assure.wrap_tool(approve_loan, tool_name="approve_loan")

    # -------------------------------------------------------------------------
    # PHASE 1 & 2: OBSERVE & GOVERN
    # -------------------------------------------------------------------------
    rule_fin001 = assure.policy_engine.get_rule("FIN-001")
    print("\n[Phase 1 & 2: Observe Policy Configuration]")
    print(f"  Policy ID   : {rule_fin001.id} (v{rule_fin001.version})")
    print(f"  Name        : {rule_fin001.name}")
    print(f"  Condition   : {rule_fin001.condition.field} {rule_fin001.condition.operator} {rule_fin001.condition.value}")
    print(f"  Action      : {rule_fin001.action}")
    print(f"  Mode        : {rule_fin001.mode}")

    # -------------------------------------------------------------------------
    # PHASE 3: ENFORCE BLOCK (₹800,000 Request)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 78)
    print("> PHASE 3: Enforcing Governance Policy (₹800,000 Disbursement Request)")
    print("  Expected: FIN-001 triggers -> BLOCK -> approve_loan does NOT execute")
    print("-" * 78)

    assure.set_session("sess_demo_phase3_block")
    before_approve_count = TOOL_EXECUTION_COUNTERS["approve_loan"]

    with TraceContext() as trace_block:
        print(f"  [Trace Started] ID: {trace_block.trace_id}")
        governed_get("CUST-102")
        governed_check("CUST-102")
        try:
            governed_approve(customer_id="CUST-102", amount=800000)
            print("  [ERROR] Execution was not intercepted!")
        except PolicyViolationError as e:
            print(f"  [Interception] BLOCKED by Policy [{e.decision.policy_id} v{e.decision.policy_version}]")
            print(f"  [Reason]       {e.decision.reason}")
            print(f"  [Citation]     {e.decision.controls.as_citation() if e.decision.controls else 'N/A'}")

    after_approve_count = TOOL_EXECUTION_COUNTERS["approve_loan"]
    assert before_approve_count == after_approve_count
    print(f"  [Verification] approve_loan execution count unchanged ({after_approve_count}) — function body NEVER ran.")

    # -------------------------------------------------------------------------
    # PHASE 5: CHANGE GOVERNANCE POLICY (BLOCK -> ASK) WITHOUT CODE CHANGE
    # -------------------------------------------------------------------------
    print("\n" + "-" * 78)
    print("> PHASE 5: Policy Modification (BLOCK -> ASK) Without Changing Agent Code")
    print("  Updating FIN-001 action to ASK in Policy Studio...")
    print("-" * 78)

    rule_fin001.action = "ASK"
    assure.policy_engine.add_or_update_rule(rule_fin001)
    assure.policy_engine.save_to_yaml(policy_file)
    print(f"  [Policy Studio] FIN-001 updated to v{rule_fin001.version} with action=ASK")

    print("\n  Re-running exact same ₹800,000 disbursement request...")
    assure.set_session("sess_demo_phase5_ask")
    pending_appr_id = None

    with TraceContext() as trace_ask:
        print(f"  [Trace Started] ID: {trace_ask.trace_id}")
        governed_get("CUST-102")
        governed_check("CUST-102")
        try:
            governed_approve(customer_id="CUST-102", amount=800000)
            print("  [ERROR] Execution was not suspended!")
        except PendingApprovalException as e:
            print(f"  [Interception] PENDING APPROVAL [{e.decision.policy_id} v{e.decision.policy_version}]")
            print(f"  [Reason]       {e.decision.reason}")

        approvals = assure.approval_store.list_approvals(trace_id=trace_ask.trace_id)
        assert len(approvals) >= 1
        pending_appr_id = approvals[0].approval_id
        print(f"  [Approval Queue] Created Pending Approval ID: {pending_appr_id} (Status: PENDING)")

    assert TOOL_EXECUTION_COUNTERS["approve_loan"] == before_approve_count
    print(f"  [Verification] approve_loan execution suspended: counter remains {before_approve_count}")

    # -------------------------------------------------------------------------
    # PHASE 6: HUMAN APPROVAL RESOLUTION & EXECUTION
    # -------------------------------------------------------------------------
    print("\n" + "-" * 78)
    print("> PHASE 6: Human Approval Resolution (Operator approves in queue)")
    print("-" * 78)

    resolved = assure.resolve_approval(
        approval_id=pending_appr_id,
        status=ApprovalStatus.APPROVED,
        resolved_by="senior_credit_officer",
    )
    print(f"  [Operator Action] Approval {resolved.approval_id} resolved as APPROVED by {resolved.resolved_by}")

    print("\n  [Runtime Path] Re-invoking approve_loan under approved trace context...")
    with TraceContext(trace_id=trace_ask.trace_id):
        result = governed_approve(customer_id="CUST-102", amount=800000)
        print(f"  [Execution Completed] Status: {result['status']}, Loan ID: {result['loan_id']}")

    final_approve_count = TOOL_EXECUTION_COUNTERS["approve_loan"]
    assert final_approve_count == before_approve_count + 1
    print(f"  [Verification] approve_loan executed exactly once after approval! Counter: {final_approve_count}")

    # Reset FIN-001 back to BLOCK for clean state
    rule_fin001.action = "BLOCK"
    assure.policy_engine.add_or_update_rule(rule_fin001)
    assure.policy_engine.save_to_yaml(policy_file)

    # -------------------------------------------------------------------------
    # FINAL VERIFICATION
    # -------------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("      FINAL DEMO VERIFICATION — INTEGRITY & ONE EVENT ONE TRUTH")
    print("=" * 78)
    is_valid, errors = assure.evidence_store.verify_integrity()
    records = assure.evidence_store.get_records()

    print(f"  Total Evidence Records Stored : {len(records)}")
    print(f"  Evidence Chain Integrity      : {'SECURE (100% VALID)' if is_valid else 'FAILED'}")
    print("\n[Primary Demo Completed Successfully — Externalized Governance Verified!]")


if __name__ == "__main__":
    main()
