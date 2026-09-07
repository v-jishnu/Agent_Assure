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
4. **Compliance Control Mapping**: Every policy rule and capability boundary declares the external control it produces evidence for, so a blocked action cites a named clause rather than only a rule id:

   ```yaml
   controls:
     iso42001: "A.9.4"      # Intended use of the AI system
     eu_ai_act: "Art. 14"   # Human oversight
   ```

   The citation is carried on the `PolicyDecision` and persisted with the evidence record. This is what makes the output an audit artifact rather than a log line.

5. **Explainable Anomaly & Security Detectors**:
   - `PIIDetector`: Detects Indian BFSI identifiers — Aadhaar, PAN, +91 phone numbers, IFSC codes, cards and email.
   - `SecretDetector`: Detects API key / secret credential leakage.
   - `RestrictedToolDetector`: Flags dangerous system-level tool calls.

   Detection canonicalises text before matching. A real model was observed writing an Aadhaar using `U+202F NARROW NO-BREAK SPACE` between digit groups — visually identical to a space, but enough to defeat a `[ -]` character class and let the identifier reach the audit log unmasked.

6. **Data Minimisation at Rest**: The agent receives real data — a KYC lookup must return an Aadhaar for the workflow to function — but the evidence store masks identifiers before persisting them. Separating what the system may *process* from what it may *retain* keeps the audit log from becoming a PII repository.

7. **Tamper-Evident Evidence Store**: SQLite storage utilizing SHA-256 hash chaining `hash(record_n) = SHA256(hash(record_{n-1}) + record_n)` with built-in integrity verification (`verify_integrity()`). The hash is computed over canonical JSON of the whole record, so every field is covered.

8. **Developer SDK Wrapper**: Lightweight wrapper (`assure.wrap_tool(func)`) for easy integration without modifying underlying business logic.

9. **LLM-Driven Demo Agent**: The demo agent is driven by a real model via Groq tool calling. It is given a task and a toolset and decides which tools to call with which arguments — so the governance layer is tested against decisions nobody scripted in advance.

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

### 2. Configure the Model Provider

The demo agent uses Groq for inference. Copy the example environment file and add your key:

```powershell
copy .env.example .env
```

`.env` is gitignored and is never committed. The client itself uses only the standard library, so there is no additional dependency to install.

### 3. Run the Demonstration Script

Run the BFSI Loan Processing Agent governance demonstration:

```powershell
python demo/loan_agent.py
```

The model decides what to do in each scenario; the console prints every tool it chose and the governance outcome.

Expected Output Highlights:
- Compliant loan request (₹250,000) -> `ALLOW` (Underlying tool executes).
- Loan above the delegated limit (₹800,000) -> `BLOCK` FIN-001, citing ISO/IEC 42001 A.9.4 | EU AI Act Art. 14 (pre-execution interception; the tool body does **NOT** run).
- High-value loan (₹400,000) -> `ASK` FIN-002, citing A.9.2 | Art. 14 (paused pending human approval).
- Applicant below the credit floor -> `BLOCK` FIN-003, citing A.9.4 | Art. 9.
- Forbidden capability (`delete_customer`) -> `BLOCK` by capability policy.
- KYC identity lookup -> `ALLOW`, but identifiers are masked in the evidence store (`Raw identifiers in evidence : none`).
- Evidence store integrity verification -> `SECURE (100% VALID)`.

Each blocked scenario prints an execution-counter check proving the underlying function never ran.

### 4. Run Automated Tests

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

---

## Deploying the Governance Console

The console is a **read-only view over the evidence store**. It has no
dependency on any model provider — `server/` contains no Groq import — so the
deployed instance needs no API key, calls no LLM, and costs nothing to run.

Agent runs happen locally, where the key lives. The deployment serves a
committed evidence snapshot (`deploy/seed_evidence.db`). This split is
deliberate:

- no `GROQ_API_KEY` ever exists in the deployment;
- no public endpoint can trigger agent runs and burn the model quota;
- cold starts stay fast because nothing calls a model on boot.

### Render (blueprint included)

`render.yaml` is committed, so Render can deploy the repository directly:

1. Push the branch to GitHub.
2. In Render, choose **New → Blueprint** and select the repository.
3. Deploy. No environment variables need to be set by hand — the blueprint
   already points `AGENTASSURE_DB` at the committed snapshot.

Any host that runs a Python web service works the same way; a `Procfile` is
included for Heroku-style platforms:

```
web: uvicorn server.api:app --host 0.0.0.0 --port $PORT
```

Two things a host requires and a local run does not: bind `0.0.0.0` rather
than localhost, and read the port from `$PORT` rather than hardcoding it.

### Refreshing the published evidence

The snapshot is a point-in-time export, so publishing newer evidence is an
explicit step:

```powershell
python demo/loan_agent.py
copy demo_evidence.db deploy\seed_evidence.db
git add -f deploy/seed_evidence.db
git commit -m "chore: refresh evidence snapshot"
```

`*.db` is gitignored with a single exception for this snapshot, so working
databases are never committed by accident.

### Verifying a deployment

```bash
curl https://<your-app>/health
curl https://<your-app>/api/v1/evidence/verify
```

The second call recomputes the entire hash chain on the server and should
return `"status": "SECURE"`. If a record were altered in transit or at rest,
it would report `TAMPERED_DETECTED` instead.

### For the mentor demo, run it locally

A free-tier cold start can take the better part of a minute, and the
deployment shows a fixed snapshot rather than a live agent. For the
presentation itself, run the demo and the console locally — the deployed URL
is better used as something the mentor can open before or after the call.
