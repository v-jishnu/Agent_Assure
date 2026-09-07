# AgentAssure — Runtime Governance & Assurance Layer for AI Agents

> **Week-1 Technical Specification & Source of Truth**  
> *Post-Dashboard Integration & Core Architecture Refactoring*

AgentAssure is a **runtime governance and assurance layer** that attaches to an existing AI agent. It does **not** replace an agent's business logic. The agent carries out its normal autonomous tasks while AgentAssure observes execution, evaluates actions against configurable governance policies, intervenes **before** sensitive actions execute, and records auditable, tamper-evident evidence.

---

## Executive Summary for Technical Managers

| Capability | What AgentAssure Does | Value / Compliance Standard |
| :--- | :--- | :--- |
| **Observe** | Normalizes all agent execution into canonical `AgentEvent` traces with parent-child span tracking. | Full execution visibility & audit trail |
| **Govern** | Evaluates tool calls against declarative YAML policies and real-time security detectors. | Configurable boundaries & thresholds |
| **Enforce** | Pre-execution interception (`ALLOW`, `BLOCK`, `ASK`). Guarantees blocked tools never execute. | Prevents rogue agent actions |
| **Assure** | Maintains a SHA-256 tamper-evident append-only hash chain with data minimisation masking at rest. | ISO 42001 (A.8.4, A.9.4) & EU AI Act (Art. 10, 14) |
| **Console** | Zero-build, single-process FastAPI dashboard (`/`) displaying live stats, traces, and hash integrity. | Real-time governance dashboard |

---

## Layer-by-Layer Architecture

AgentAssure is structured into five clean, decoupled layers with strict separation of concerns:

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ LAYER 1: DEVELOPER SDK & GOVERNANCE CORE (agentassure/)                                │
│ • AgentAssure (sdk.py)               • AgentEvent & TraceContext (events.py, trace.py) │
│ • PolicyEngine (policy.py)           • Detectors (detectors.py - Indian PII, Secrets)  │
│ • EnforcementEngine (enforcement.py) • EvidenceStore (evidence.py - SHA-256 Hash Chain) │
└───────────────────────────────────────────┬─────────────────────────────────────────────┘
                                            │
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ LAYER 2: REST API & GOVERNANCE CONSOLE SERVER (server/)                                 │
│ • FastAPI Backend (server/api.py)     • Dashboard Web UI (server/static/index.html)     │
│ • Endpoints: /api/v1/stats, /api/v1/traces, /api/v1/evidence, /api/v1/evidence/verify   │
└───────────────────────────────────────────┬─────────────────────────────────────────────┘
                                            │
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ LAYER 3: POLICY & COMPLIANCE CONFIGURATION (policies/)                                  │
│ • Declarative YAML policies (policies/loan.yaml)                                        │
│ • Maps rules to ISO/IEC 42001 & EU AI Act control citations                             │
└───────────────────────────────────────────┬─────────────────────────────────────────────┘
                                            │
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ LAYER 4: REAL LLM AGENT DEMO (demo/)                                                    │
│ • Autonomous Loan Processing Agent (demo/loan_agent.py, agent_loop.py, llm.py)          │
│ • Driven by real Groq tool calling; tests policy against unscripted model decisions     │
└───────────────────────────────────────────┬─────────────────────────────────────────────┘
                                            │
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ LAYER 5: AUTOMATED TEST & VERIFICATION SUITE (tests/)                                   │
│ • 29 Unit & Integration Tests (events, trace, policy, enforcement, controls, evidence) │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Breakdown

### 1. Developer SDK & Governance Core (`agentassure/`)
- **[sdk.py](file:///c:/Agent_Assure/agentassure/sdk.py)**: `AgentAssure` wrapper class. Wraps agent tool functions (`assure.wrap_tool(fn)`) to inject pre-execution policy evaluation and event telemetry without modifying underlying business logic.
- **[events.py](file:///c:/Agent_Assure/agentassure/events.py)** & **[trace.py](file:///c:/Agent_Assure/agentassure/trace.py)**: Canonical `AgentEvent` schema and context-managed parent-child span tracker.
- **[policy.py](file:///c:/Agent_Assure/agentassure/policy.py)**: YAML policy engine supporting scope matching (agent, environment, tool), condition evaluation (`gt`, `lt`, `eq`, `in`, `contains`), capability boundaries (`allowed`, `approval_required`, `forbidden`), and control citations.
- **[detectors.py](file:///c:/Agent_Assure/agentassure/detectors.py)**: Explainable anomaly & security detectors:
  - `PIIDetector`: Detects Indian BFSI identifiers — Aadhaar, PAN, +91 phone numbers, IFSC codes, credit cards, and email. Canonicalises unicode space variants (e.g. `U+202F NARROW NO-BREAK SPACE`).
  - `SecretDetector`: Detects API key leakage (Groq, OpenAI, AWS, Slack, RSA keys).
  - `RestrictedToolDetector`: Flag dangerous system-level tool execution attempts.
- **[enforcement.py](file:///c:/Agent_Assure/agentassure/enforcement.py)**: Pre-execution interception logic returning structured `PolicyDecision` objects with `PolicyOutcome` (`ALLOW`, `BLOCK`, `ASK`, `SHADOW`).
- **[evidence.py](file:///c:/Agent_Assure/agentassure/evidence.py)**: Self-contained SQLite repository using SHA-256 hash chaining `hash(record_n) = SHA256(canonical_json(record_n))`. Implements PII redaction at rest and runtime hash verification (`verify_integrity()`).

### 2. REST API & Governance Console Server (`server/`)
- **[api.py](file:///c:/Agent_Assure/server/api.py)**: FastAPI web application providing:
  - `GET /api/v1/stats`: Headline statistics, active trace/session counts, decision breakdown (`BLOCK`, `ASK`, `ALLOW`), PII redaction count, and control coverage distribution.
  - `GET /api/v1/traces`: Aggregated agent execution runs sorted newest-first.
  - `GET /api/v1/evidence`: Paginated/filtered audit trail records.
  - `GET /api/v1/evidence/verify`: Hash chain integrity checker.
  - `GET /health`: Server health check endpoint.
  - Mounts `server/static/` at `/` for single-process uvicorn serving.
- **[index.html](file:///c:/Agent_Assure/server/static/index.html)**: Vanilla HTML5/CSS3/JS governance console with zero build steps or Node toolchain. Displays real-time status badges, trace drill-downs, compliance citations, and hash integrity indicators.

### 3. Policy & Compliance Control Mapping (`policies/loan.yaml`)
Every policy rule and capability boundary declares the external compliance control it evidences:

```yaml
rules:
  - id: "FIN-001"
    name: "Disbursement Ceiling"
    tool: "approve_loan"
    condition: "input.amount > 500000"
    action: "BLOCK"
    reason: "Disbursement amount exceeds delegated limit of INR 500,000"
    controls:
      iso42001: "A.9.4"      # Intended use of the AI system
      eu_ai_act: "Art. 14"   # Human oversight
```

When an action is blocked, the resulting `PolicyDecision` carries the control citation (e.g. `ISO/IEC 42001 A.9.4 | EU AI Act Art. 14`) directly into the audit evidence store.

### 4. Real LLM Agent Demo (`demo/`)
- **[loan_agent.py](file:///c:/Agent_Assure/demo/loan_agent.py)**: Runs a BFSI retail loan processing agent powered by a real Groq LLM model (`llama-3.3-70b-versatile`). The model autonomously selects tools and parameters across 6 realistic scenarios:
  1. Compliant loan request (₹250,000) -> `ALLOW`
  2. Loan above delegated limit (₹800,000) -> `BLOCK` (FIN-001)
  3. High-value loan (₹400,000) -> `ASK` (FIN-002, pending human sign-off)
  4. Applicant below credit floor (610 score) -> `BLOCK` (FIN-003)
  5. Forbidden capability (`delete_customer`) -> `BLOCK` (Capability Policy)
  6. Identity KYC lookup -> `ALLOW` (Real Aadhaar/PAN processed by agent, but PII is masked before persisting to evidence log)

---

## Pre-Execution Interception Guarantee

AgentAssure guarantees that when a policy decision evaluates to `BLOCK` or `ASK`, the underlying tool function **never executes**.

```python
from agentassure import AgentAssure, PolicyViolationError

assure = AgentAssure(policy_path="policies/loan.yaml")
governed_approve_loan = assure.wrap_tool(approve_loan, tool_name="approve_loan")

# Throws PolicyViolationError BEFORE approve_loan() function body can execute
try:
    governed_approve_loan(customer_id="CUST-102", amount=800000)
except PolicyViolationError as e:
    print(f"Blocked by policy: {e.decision.policy_id}")
    # Underlying approve_loan function execution count remains unchanged!
```

---

## Verification & Execution Guide

### 1. Install Dependencies
```powershell
pip install -r requirements.txt
```

### 2. Configure Environment (Optional for Demo Agent)
```powershell
copy .env.example .env
# Edit .env and add GROQ_API_KEY if running the live LLM demo
```

### 3. Run Automated Tests
```powershell
pytest
```
*Expected Result*: All 29 unit and integration tests pass cleanly.

### 4. Run the LLM Governance Demo
```powershell
python demo/loan_agent.py
```

### 5. Launch the Governance Console Server
```powershell
.venv\Scripts\python.exe -m uvicorn server.api:app --reload
```
Open [http://localhost:8000](http://localhost:8000) in your browser to view the live governance dashboard.

---

## Deployment Blueprint

The governance console is deployed as a **read-only audit dashboard** over a committed evidence snapshot (`deploy/seed_evidence.db`). 

- `render.yaml` and `Procfile` are included for Render / Heroku-style hosts.
- The server process requires **no model API keys** and makes zero LLM calls, ensuring fast boot times, zero quota burn, and total security isolation.
