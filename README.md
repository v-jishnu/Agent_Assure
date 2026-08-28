# AgentAssure — Runtime Governance & Assurance Layer for AI Agents

**AgentAssure** is a runtime governance and assurance layer that attaches to existing AI agents.

It does **not** replace an agent's business logic. The agent continues its normal work while AgentAssure observes execution, evaluates actions against configurable governance policies, intervenes **before** sensitive actions execute, and records auditable, tamper-evident evidence.

---

## Key Features (Week 1)

1. **Canonical Event & Trace Model**: Normalizes agent executions into structured, traceable `AgentEvent`s with standard parent-child span tracking.
2. **Declarative Policy Engine**: YAML-based policies with configurable scopes (agent, environment, tool), conditions (`gt`, `lt`, `eq`, `in`, `contains`), and capability rules (`allowed`, `approval_required`, `forbidden`).
3. **Pre-Execution Interception & Enforcement**:
   - `ALLOW`: Action is permitted and executes normally.
   - `BLOCK`: Intercepts action *before* execution, preventing underlying tool function execution.
   - `ASK`: Intercepts action *before* execution and marks request as pending human approval.
4. **Explainable Anomaly & Security Detectors**:
   - `PIIDetector`: Detects email, phone, SSN data exposure.
   - `SecretDetector`: Detects API key / secret credential leakage.
   - `RestrictedToolDetector`: Flag dangerous system-level tool calls.
5. **Tamper-Evident Evidence Store**: SQLite storage utilizing SHA-256 hash chaining `hash(record_n) = SHA256(hash(record_{n-1}) + record_n)` with built-in integrity verification (`verify_integrity()`).
6. **Developer SDK Wrapper**: Lightweight wrapper (`assure.wrap_tool(func)`) for easy integration without modifying underlying business logic.

---

## Repository Structure

```text
agentassure/
│
├── agentassure/
│   ├── __init__.py         # Package exports
│   ├── events.py           # Canonical AgentEvent model
│   ├── trace.py            # Trace & Span Context management
│   ├── policy.py           # Declarative Policy Engine & Scope matching
│   ├── detectors.py        # Explainable PII, Secret, & Restricted Tool detectors
│   ├── enforcement.py      # Pre-execution enforcement (BLOCK, ASK, ALLOW)
│   ├── sdk.py              # AgentAssure Developer SDK wrapper
│   └── adapters/
│       ├── __init__.py
│       └── langgraph.py    # LangGraph framework adapter
│
├── server/
│   ├── __init__.py
│   ├── evidence.py         # SQLite append-only evidence store & hash integrity
│   ├── api.py              # FastAPI REST endpoints
│   └── websocket.py        # Live WebSocket manager stub
│
├── policies/
│   └── loan.yaml           # BFSI Loan Agent policy configuration
│
├── demo/
│   └── loan_agent.py       # Standalone Loan Processing Agent governance demo
│
├── tests/
│   ├── test_events.py      # Event schema tests
│   ├── test_trace.py       # Trace hierarchy tests
│   ├── test_policy.py      # Policy engine & condition tests
│   ├── test_enforcement.py # Interception & tool execution prevention tests
│   └── test_evidence.py   # SQLite hash chain & tamper detection tests
│
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Quick Start & Installation

### 1. Install Dependencies

```powershell
pip install -r requirements.txt
```

### 2. Run the Week 1 Demonstration Script

Run the BFSI Loan Processing Agent governance demonstration:

```powershell
python demo/loan_agent.py
```

Expected Output Highlights:
- Compliant loan request (₹250,000) -> `ALLOW` (Underlying tool executes).
- Non-compliant loan request (₹800,000 > ₹500,000 limit) -> `BLOCK` (Pre-execution interception occurs; underlying tool function does **NOT** execute).
- High-value loan request (₹400,000) -> `ASK` (Paused pending human approval; underlying tool function does **NOT** execute).
- Evidence store integrity verification -> `SECURE (100% VALID)`.

### 3. Run Automated Tests

Run the complete test suite:

```powershell
pytest tests/ -v
```

---

## Pre-Execution Interception Guarantee

AgentAssure guarantees that when a policy returns `BLOCK` or `ASK`, the underlying tool function call is intercepted **before** execution.

```python
from agentassure import AgentAssure

assure = AgentAssure(policy_path="policies/loan.yaml")
governed_approve_loan = assure.wrap_tool(approve_loan, tool_name="approve_loan")

# Throws PolicyViolationError without executing approve_loan()
governed_approve_loan(customer_id="CUST-102", amount=800000)
```
