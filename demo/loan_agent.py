"""
AgentAssure Demo: BFSI Loan Processing Agent Runtime Governance
"""

import sys
import os

# Add root directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agentassure import AgentAssure, TraceContext, PolicyViolationError, PendingApprovalException

# Global execution counters to prove tool execution vs pre-execution interception
TOOL_EXECUTION_COUNTERS = {
    "get_customer": 0,
    "check_credit": 0,
    "calculate_loan": 0,
    "approve_loan": 0,
    "send_email": 0,
    "delete_customer": 0,
}


def get_customer(customer_id: str) -> dict:
    TOOL_EXECUTION_COUNTERS["get_customer"] += 1
    return {"customer_id": customer_id, "name": "Jane Doe", "credit_score": 750, "email": "jane.doe@example.com"}


def check_credit(customer_id: str) -> dict:
    TOOL_EXECUTION_COUNTERS["check_credit"] += 1
    return {"customer_id": customer_id, "status": "APPROVED", "risk_level": "LOW"}


def calculate_loan(amount: float, term_months: int) -> dict:
    TOOL_EXECUTION_COUNTERS["calculate_loan"] += 1
    monthly_payment = (amount * 1.08) / term_months
    return {"amount": amount, "term_months": term_months, "monthly_payment": round(monthly_payment, 2)}


def approve_loan(customer_id: str, amount: float) -> dict:
    TOOL_EXECUTION_COUNTERS["approve_loan"] += 1
    return {"status": "SUCCESS", "loan_id": "LN-98765", "customer_id": customer_id, "approved_amount": amount}


def send_email(to_address: str, content: str) -> dict:
    TOOL_EXECUTION_COUNTERS["send_email"] += 1
    return {"status": "SENT", "to": to_address}


def delete_customer(customer_id: str) -> dict:
    TOOL_EXECUTION_COUNTERS["delete_customer"] += 1
    return {"status": "DELETED", "customer_id": customer_id}


def main():
    db_file = "demo_evidence.db"
    if os.path.exists(db_file):
        os.remove(db_file)

    policy_file = os.path.join(os.path.dirname(__file__), "..", "policies", "loan.yaml")

    print("=" * 70)
    print("      AGENTASSURE WEEK 1 DEMO — RUNTIME GOVERNANCE LAYER")
    print("=" * 70)

    # Initialize AgentAssure wrapper
    assure = AgentAssure(
        policy_path=policy_file,
        db_path=db_file,
        agent_id="loan-agent",
        environment="demo"
    )

    # Wrap tools with governance layer
    governed_get_customer = assure.wrap_tool(get_customer, tool_name="get_customer")
    governed_check_credit = assure.wrap_tool(check_credit, tool_name="check_credit")
    governed_calculate_loan = assure.wrap_tool(calculate_loan, tool_name="calculate_loan")
    governed_approve_loan = assure.wrap_tool(approve_loan, tool_name="approve_loan")
    governed_delete_customer = assure.wrap_tool(delete_customer, tool_name="delete_customer")

    # -------------------------------------------------------------------------
    # SCENARIO 1: Compliant Loan Request (INR 250,000) -> ALLOW
    # -------------------------------------------------------------------------
    print("\n> SCENARIO 1: Compliant Loan Request (Amount: INR 250,000)")
    assure.set_session("sess_compliant_001")
    with TraceContext() as trace:
        print(f"  [AgentAssure] Trace started: {trace.trace_id}")
        cust = governed_get_customer(customer_id="CUST-101")
        print(f"  [AgentAssure] tool: get_customer -> ALLOW (Result: {cust['name']})")

        credit = governed_check_credit(customer_id="CUST-101")
        print(f"  [AgentAssure] tool: check_credit -> ALLOW (Status: {credit['status']})")

        calc = governed_calculate_loan(amount=250000, term_months=36)
        print(f"  [AgentAssure] tool: calculate_loan -> ALLOW (Monthly: INR {calc['monthly_payment']})")

        initial_counter = TOOL_EXECUTION_COUNTERS["approve_loan"]
        res = governed_approve_loan(customer_id="CUST-101", amount=250000)
        print(f"  [AgentAssure] tool: approve_loan -> ALLOW | Executed! Loan ID: {res['loan_id']}")
        print(f"  [Verification] approve_loan execution count: {TOOL_EXECUTION_COUNTERS['approve_loan']} (was {initial_counter})")

    # -------------------------------------------------------------------------
    # SCENARIO 2: Non-Compliant Loan Request (INR 800,000 > INR 500,000 limit) -> BLOCK
    # -------------------------------------------------------------------------
    print("\n> SCENARIO 2: Non-Compliant Loan Request (Amount: INR 800,000 > INR 500,000 Limit)")
    assure.set_session("sess_violation_002")
    with TraceContext() as trace:
        print(f"  [AgentAssure] Trace started: {trace.trace_id}")
        governed_get_customer(customer_id="CUST-102")

        initial_counter = TOOL_EXECUTION_COUNTERS["approve_loan"]
        print(f"  [Pre-Check Counter] approve_loan executions before call: {initial_counter}")
        try:
            governed_approve_loan(customer_id="CUST-102", amount=800000)
        except PolicyViolationError as e:
            print(f"  [AgentAssure] Policy Triggered: {e.decision.policy_id} (v{e.decision.policy_version})")
            print(f"  [AgentAssure] Outcome: BLOCK | Reason: {e.decision.reason}")
            print(f"  [AgentAssure] Pre-Execution Interception: Underlying tool was NOT executed.")

        after_counter = TOOL_EXECUTION_COUNTERS["approve_loan"]
        print(f"  [Verification] approve_loan executions after call: {after_counter}")
        assert after_counter == initial_counter, "CRITICAL ERROR: Blocked tool function was executed!"
        print("  [SUCCESS] Verified: Underlying tool call was prevented.")

    # -------------------------------------------------------------------------
    # SCENARIO 3: High-Value Loan Request (INR 400,000) -> ASK (Pending Approval)
    # -------------------------------------------------------------------------
    print("\n> SCENARIO 3: High-Value Loan Request (Amount: INR 400,000) -> ASK (Human Approval)")
    assure.set_session("sess_approval_003")
    with TraceContext() as trace:
        print(f"  [AgentAssure] Trace started: {trace.trace_id}")
        initial_counter = TOOL_EXECUTION_COUNTERS["approve_loan"]
        try:
            governed_approve_loan(customer_id="CUST-103", amount=400000)
        except PendingApprovalException as e:
            print(f"  [AgentAssure] Policy Triggered: {e.decision.policy_id}")
            print(f"  [AgentAssure] Outcome: ASK (PENDING APPROVAL) | Reason: {e.decision.reason}")

        after_counter = TOOL_EXECUTION_COUNTERS["approve_loan"]
        assert after_counter == initial_counter, "CRITICAL ERROR: Pending tool function was executed!"
        print("  [SUCCESS] Verified: Tool execution paused pending approval.")

    # -------------------------------------------------------------------------
    # SCENARIO 4: Forbidden Capability Attempt -> BLOCK
    # -------------------------------------------------------------------------
    print("\n> SCENARIO 4: Forbidden Capability Attempt (delete_customer)")
    assure.set_session("sess_forbidden_004")
    with TraceContext() as trace:
        print(f"  [AgentAssure] Trace started: {trace.trace_id}")
        try:
            governed_delete_customer(customer_id="CUST-999")
        except PolicyViolationError as e:
            print(f"  [AgentAssure] Capability Policy Triggered: {e.decision.policy_id}")
            print(f"  [AgentAssure] Outcome: BLOCK | Reason: {e.decision.reason}")

        assert TOOL_EXECUTION_COUNTERS["delete_customer"] == 0, "CRITICAL ERROR: Forbidden tool executed!"
        print("  [SUCCESS] Verified: Forbidden capability execution blocked.")


    # -------------------------------------------------------------------------
    # EVIDENCE INTEGRITY VERIFICATION
    # -------------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("      EVIDENCE STORE & TAMPER-EVIDENT INTEGRITY VERIFICATION")
    print("=" * 70)
    is_valid, errors = assure.evidence_store.verify_integrity()
    records = assure.evidence_store.get_records()
    print(f" Total Evidence Records Stored: {len(records)}")
    print(f" Hash Chain Integrity Status: {'SECURE (100% VALID)' if is_valid else 'TAMPERED DETECTED'}")

    for rec in records[-4:]:
        print(f"   Record ID: {rec.record_id} | Event: {rec.event_type} | Decision: {rec.decision} | Hash: {rec.record_hash[:16]}...")

    print("\n[AgentAssure Week 1 Demo Complete Successfully]")


if __name__ == "__main__":
    main()
