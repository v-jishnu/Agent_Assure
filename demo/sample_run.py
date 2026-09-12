from agentassure import AgentAssure

aa = AgentAssure()

@aa.govern
def verify_identity(applicant_id: str):
    print(f"Verifying applicant {applicant_id}...")
    return {"status": "verified"}

@aa.govern
def disburse_loan(applicant_id: str, amount: float):
    print(f"Disbursing ${amount} to {applicant_id}...")
    return {"status": "success", "amount": amount}

if __name__ == "__main__":
    verify_identity("APP-456")
    try:
        # Over 50000 will be blocked or require approval under loan policy
        disburse_loan("APP-456", 75000.0)
    except Exception as exc:
        print(f"Interception caught: {exc}")
