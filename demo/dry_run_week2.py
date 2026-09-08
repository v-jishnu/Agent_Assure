"""
AgentAssure — Week 2 Person 1 Manual Runtime Dry-Run Script
Executes Dry Run A (Normal/ALLOW), Dry Run B (Block), and Dry Run C (Ask -> Approve -> Execute).
Does not require external Groq API keys to execute.
"""

import os
import sys

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Reconfigure encoding for Windows shells
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from agentassure import AgentAssure, TraceContext, PolicyViolationError, PendingApprovalException
from agentassure.approval import ApprovalStatus
from agentassure.logging import default_log_buffer


TOOL_EXECUTION_COUNTERS = {
    "get_customer": 0,
    "check_credit": 0,
    "calculate_loan": 0,
    "approve_loan": 0,
}


def get_customer(customer_id: str) -> dict:
    TOOL_EXECUTION_COUNTERS["get_customer"] += 1
    return {"customer_id": customer_id, "name": "Jane Doe", "credit_score": 750}


def check_credit(customer_id: str) -> dict:
    TOOL_EXECUTION_COUNTERS["check_credit"] += 1
    return {"customer_id": customer_id, "credit_score": 750, "status": "APPROVED"}


def calculate_loan(amount: float, term_months: int) -> dict:
    TOOL_EXECUTION_COUNTERS["calculate_loan"] += 1
    monthly = round((amount * 1.08) / term_months, 2)
    return {"amount": amount, "term_months": term_months, "monthly_payment": monthly}


def approve_loan(customer_id: str, amount: float, credit_score: int = 750) -> dict:
    TOOL_EXECUTION_COUNTERS["approve_loan"] += 1
    return {
        "status": "SUCCESS",
        "loan_id": f"LN-{int(amount)}",
        "customer_id": customer_id,
        "approved_amount": amount,
    }


def main():
    db_file = "demo_evidence.db"
    policy_file = os.path.join(os.path.dirname(__file__), "..", "policies", "loan.yaml")

    print("=" * 76)
    print("      AGENTASSURE WEEK 2 — RUNTIME BACKEND FOUNDATION DRY RUN")
    print("=" * 76)

    assure = AgentAssure(
        policy_path=policy_file,
        db_path=db_file,
        agent_id="loan-agent",
        environment="demo",
    )

    governed_get_customer = assure.wrap_tool(get_customer, tool_name="get_customer")
    governed_check_credit = assure.wrap_tool(check_credit, tool_name="check_credit")
    governed_calc_loan = assure.wrap_tool(calculate_loan, tool_name="calculate_loan")
    governed_approve_loan = assure.wrap_tool(approve_loan, tool_name="approve_loan")

    # -------------------------------------------------------------------------
    # DRY RUN A — NORMAL (ALLOW)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 76)
    print("> DRY RUN A: Compliant Loan Request (₹250,000)")
    print("  Expected: get_customer -> check_credit -> calculate_loan -> approve_loan -> ALLOW")
    print("-" * 76)

    assure.set_session("sess_dry_run_A")
    with TraceContext() as trace_a:
        print(f"  [Trace] Started: {trace_a.trace_id}")
        c = governed_get_customer(customer_id="CUST-101")
        print(f"  [Step 1] Customer profile: {c['name']} (Credit: {c['credit_score']})")

        cr = governed_check_credit(customer_id="CUST-101")
        print(f"  [Step 2] Credit status: {cr['status']}")

        calc = governed_calc_loan(amount=250000, term_months=36)
        print(f"  [Step 3] Monthly installment: ₹{calc['monthly_payment']}")

        appr = governed_approve_loan(customer_id="CUST-101", amount=250000)
        print(f"  [Step 4] Loan disbursed: {appr['status']} ({appr['loan_id']})")

    records_a = assure.evidence_store.get_records(trace_id=trace_a.trace_id)
    print(f"  [Result] Trace completed successfully with {len(records_a)} evidence records.")

    # -------------------------------------------------------------------------
    # DRY RUN B — BLOCK (VIOLATION)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 76)
    print("> DRY RUN B: Loan Above Delegated Limit (₹800,000)")
    print("  Expected: FIN-001 triggers -> BLOCK -> approve_loan does NOT execute")
    print("-" * 76)

    assure.set_session("sess_dry_run_B")
    before_approve_count = TOOL_EXECUTION_COUNTERS["approve_loan"]

    with TraceContext() as trace_b:
        print(f"  [Trace] Started: {trace_b.trace_id}")
        try:
            governed_approve_loan(customer_id="CUST-102", amount=800000)
            print("  [ERROR] Execution was not intercepted!")
        except PolicyViolationError as e:
            print(f"  [Interception] BLOCKED by Policy [{e.decision.policy_id} v{e.decision.policy_version}]")
            print(f"  [Reason] {e.decision.reason}")
            print(f"  [Citation] {e.decision.controls.as_citation() if e.decision.controls else 'N/A'}")

    after_approve_count = TOOL_EXECUTION_COUNTERS["approve_loan"]
    assert before_approve_count == after_approve_count, "CRITICAL: Tool executed despite BLOCK!"
    print(f"  [Proof] approve_loan call counter unchanged: {after_approve_count} (Function never executed)")

    # -------------------------------------------------------------------------
    # DRY RUN C — ASK (APPROVAL LIFECYCLE)
    # -------------------------------------------------------------------------
    print("\n" + "-" * 76)
    print("> DRY RUN C: High-Value Loan Needing Human Sign-Off (₹400,000)")
    print("  Expected: FIN-002 triggers -> ASK -> Pending Approval Created -> Tool Suspended")
    print("-" * 76)

    assure.set_session("sess_dry_run_C")
    pending_appr_id = None
    before_approve_count_c = TOOL_EXECUTION_COUNTERS["approve_loan"]

    with TraceContext() as trace_c:
        print(f"  [Trace] Started: {trace_c.trace_id}")
        try:
            governed_approve_loan(customer_id="CUST-103", amount=400000)
            print("  [ERROR] Execution was not paused!")
        except PendingApprovalException as e:
            print(f"  [Interception] PENDING APPROVAL [{e.decision.policy_id} v{e.decision.policy_version}]")
            print(f"  [Reason] {e.decision.reason}")

        # Check approval in database
        approvals = assure.approval_store.list_approvals(trace_id=trace_c.trace_id)
        assert len(approvals) >= 1
        appr_obj = approvals[0]
        pending_appr_id = appr_obj.approval_id
        print(f"  [Approval Store] Created Approval ID: {pending_appr_id} (Status: {appr_obj.status.value})")

    after_approve_count_c = TOOL_EXECUTION_COUNTERS["approve_loan"]
    assert before_approve_count_c == after_approve_count_c
    print(f"  [Proof] approve_loan execution suspended: counter is {after_approve_count_c}")

    # Now simulate Operator resolving approval
    print("\n  [Operator Action] Resolving approval via control plane...")
    resolved = assure.resolve_approval(
        approval_id=pending_appr_id,
        status=ApprovalStatus.APPROVED,
        resolved_by="senior_loan_officer",
    )
    print(f"  [Approval Store] Approval {resolved.approval_id} transitioned to: {resolved.status.value}")

    # Re-executing under same trace now succeeds!
    print("  [Agent Resume] Re-invoking approve_loan with approved governance sign-off...")
    with TraceContext(trace_id=trace_c.trace_id):
        res_approved = governed_approve_loan(customer_id="CUST-103", amount=400000)
        print(f"  [Result] Execution completed: {res_approved['status']} ({res_approved['loan_id']})")

    final_approve_count = TOOL_EXECUTION_COUNTERS["approve_loan"]
    assert final_approve_count == before_approve_count_c + 1
    print(f"  [Proof] approve_loan executed exactly once after approval! Counter: {final_approve_count}")

    # -------------------------------------------------------------------------
    # INTEGRITY & CORRELATION VERIFICATION
    # -------------------------------------------------------------------------
    print("\n" + "=" * 76)
    print("      EVIDENCE STORE INTEGRITY & CORRELATED LOG AUDIT")
    print("=" * 76)

    is_valid, errors = assure.evidence_store.verify_integrity()
    records = assure.evidence_store.get_records()
    approvals_all = assure.approval_store.list_approvals()
    logs = default_log_buffer.get_logs(limit=10)

    print(f"  Total Evidence Records Stored : {len(records)}")
    print(f"  Total Approvals Recorded     : {len(approvals_all)}")
    print(f"  Hash Chain Integrity         : {'SECURE (100% VALID)' if is_valid else 'FAILED'}")
    print(f"  Correlated Log Buffer Count   : {len(default_log_buffer._buffer)}")
    print("\n  Sample correlated runtime log:")
    for l in logs[-3:]:
        print(f"    [{l.level}] trace={l.trace_id} comp={l.component}: {l.message[:70]}")

    print("\n[Dry Run Completed Successfully]")


if __name__ == "__main__":
    main()
