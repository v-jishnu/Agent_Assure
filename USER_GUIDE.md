# AgentAssure — Comprehensive User Guide

> **AgentAssure is a lightweight runtime governance layer for AI agents that turns organizational policies into executable controls, enforces them at the point of action, and gives developers an immediate trace of what happened, why, and what it cost.**

---

## Table of Contents

1. [Architecture & Mental Model](#1-architecture--mental-model)
2. [Installation & Setup](#2-installation--setup)
3. [Repository Initialization (`agentassure init`)](#3-repository-initialization-agentassure-init)
4. [Governing Existing Agents](#4-governing-existing-agents)
   - [The `@aa.govern` Decorator](#the-aagovern-decorator)
   - [LLM Cost & Token Tracking](#llm-cost--token-tracking)
5. [Framework Adapters](#5-framework-adapters)
   - [OpenAI Adapter](#openai-adapter)
   - [LangGraph Adapter](#langgraph-adapter)
   - [Model Context Protocol (MCP) Adapter](#model-context-protocol-mcp-adapter)
6. [Running Governed Agents (`agentassure run`)](#6-running-governed-agents-agentassure-run)
7. [CLI Command Reference](#7-cli-command-reference)
8. [Policy Engine & YAML Reference](#8-policy-engine--yaml-reference)
   - [Policy Schema](#policy-schema)
   - [Cost & Budget Enforcements](#cost--budget-enforcements)
   - [Regulatory Compliance Citing](#regulatory-compliance-citing)
9. [Policy Ingest & RAG Extraction](#9-policy-ingest--rag-extraction)
10. [Audit Evidence & Cryptographic Verification](#10-audit-evidence--cryptographic-verification)
11. [Human-in-the-Loop Approvals](#11-human-in-the-loop-approvals)
12. [JSON Output Format](#12-json-output-format)
13. [Optional Web Dashboard](#13-optional-web-dashboard)

---

## 1. Architecture & Mental Model

```text
       ORGANIZATIONAL POLICY (PDF / DOCX / TXT / Markdown)
                           │
                           ▼
          agentassure ingest (RAG / Heuristic)
                           │
                           ▼
                 POLICIES (YAML)
                           │
                           ▼
AI AGENT ─────────► [ AgentAssure Runtime ] ─────────► TARGET ENVIRONMENT
                       │             │
                       ▼             ▼
              Point-of-Action   Tamper-Evident
                Enforcement     Evidence Store
              (ALLOW/BLOCK/ASK) (SHA-256 Hash Chain)
                       │             │
                       ▼             ▼
                 CLI Summary    JSON Trace Export
```

### Key Guarantees
- **Non-Invasive**: Wrap tool functions with `@aa.govern` without restructuring agent logic.
- **Zero-Dependency Core**: Governance core requires no external servers, databases, or API keys.
- **Deterministic by Default**: Works offline with zero LLM API keys. RAG is an optional accelerator for policy ingestion, with automatic fallback to heuristics.
- **Tamper-Evident**: Audit records form a SHA-256 hash chain that detects any retroactive tampering.

---

## 2. Installation & Setup

### From Source or Git

```bash
# Clone the repository
git clone https://github.com/v-jishnu/Agent_Assure.git
cd Agent_Assure

# Linux / macOS automated setup
bash scripts/setup.sh

# Windows PowerShell automated setup
.\scripts\setup.ps1
```

Or install directly into an existing virtual environment:

```bash
pip install git+https://github.com/v-jishnu/Agent_Assure.git
```

For policy ingestion from PDF and DOCX files, install the optional ingest dependencies:

```bash
pip install "agentassure[ingest]"
```

Verify installation:

```bash
agentassure --help
```

---

## 3. Repository Initialization (`agentassure init`)

In your agent project root directory, run:

```bash
agentassure init
```

The interactive wizard will:
1. Prompt for your Agent ID (e.g. `loan-processing-agent`) and Environment (e.g. `production`).
2. Generate an `agentassure.yaml` configuration file.
3. Scaffold an example policy in `policies/policy.yaml`.
4. Initialize `.agentassure/` for SQLite evidence storage and trace exports.
5. Update your `.gitignore` to keep local SQLite evidence out of Git.

### Configuration (`agentassure.yaml`)

```yaml
agent_id: loan-agent
environment: production
policies: ./policies/
db: ./.agentassure/evidence.db
output:
  cli: true
  json: ./.agentassure/traces/
```

Configuration resolution order:
1. Explicit function/CLI arguments
2. Environment variables (`AGENTASSURE_POLICY`, `AGENTASSURE_DB`, `AGENTASSURE_AGENT_ID`, `AGENTASSURE_ENVIRONMENT`)
3. `agentassure.yaml` in current working directory
4. Built-in defaults

---

## 4. Governing Existing Agents

### The `@aa.govern` Decorator

AgentAssure provides a single decorator to govern any tool function:

```python
from agentassure import AgentAssure

aa = AgentAssure()

@aa.govern
def disburse_loan(applicant_id: str, amount: float):
    # This action is governed before execution!
    # Policies can check 'amount', 'applicant_id', etc.
    return {"status": "SUCCESS", "disbursed": amount}

# With custom tool name:
@aa.govern(tool_name="wire_transfer")
def transfer(dest: str, amount: float):
    return {"transferred": amount}
```

When an agent executes `disburse_loan(...)`:
1. Inputs are inspected against declarative policies in `policies/`.
2. Sensitive data (PII, API keys) is redacted before logging.
3. If an `ALLOW` rule matches, execution proceeds normally.
4. If a `BLOCK` rule matches, execution is intercepted and raises `PolicyViolationError`.
5. If an `ASK` rule matches, an approval request is registered and `PendingApprovalException` is raised.
6. A cryptographic evidence record is appended to the SQLite hash chain.

### LLM Cost & Token Tracking

AgentAssure tracks token counts and estimates USD costs based on a built-in pricing table (GPT-4o, Claude 3.5, Gemini, Llama 3, etc.):

```python
from agentassure import AgentAssure

aa = AgentAssure()

# Explicitly record an LLM call:
aa.track_llm_call(
    model="gpt-4o",
    input_tokens=1200,
    output_tokens=350,
    latency_ms=420.0
)
```

---

## 5. Framework Adapters

### OpenAI Adapter

Automatically intercept and record token counts from every OpenAI chat completion:

```python
from agentassure.adapters.openai import instrument
from openai import OpenAI

# Call once at agent startup
instrument()

client = OpenAI()
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Analyze loan risk for applicant A-123"}]
)
# Token counts and estimated USD cost are automatically recorded into the current trace!
```

### LangGraph Adapter

Wrap tools in LangGraph or LangChain workflows:

```python
from agentassure import AgentAssure
from agentassure.adapters.langgraph import LangGraphAdapter

aa = AgentAssure()
adapter = LangGraphAdapter(aa)

# Wrap a single tool:
governed_tool = adapter.wrap_tool(raw_tool_function, tool_name="credit_check")
```

### Model Context Protocol (MCP) Adapter

Govern tool calls in Model Context Protocol (MCP) servers:

```python
from agentassure import AgentAssure
from agentassure.adapters.mcp import MCPAdapter

aa = AgentAssure()
mcp_governor = MCPAdapter(aa)

# Option 1: Decorate individual MCP tool implementations (async or sync)
@mcp_governor.govern_tool(tool_name="query_database")
async def query_db(query: str):
    return await db.execute(query)

# Option 2: Wrap an entire MCP server's call_tool dispatcher
governed_dispatcher = mcp_governor.wrap_dispatcher(server.call_tool)
```

---

## 6. Running Governed Agents (`agentassure run`)

Use `agentassure run` to execute your agent subprocess with automatic governance injection:

```bash
agentassure run python my_agent.py
```

### What Happens During `agentassure run`:
1. Loads `agentassure.yaml` and prepares the environment.
2. Injects `AGENTASSURE_ACTIVE=1`, `AGENTASSURE_POLICY`, `AGENTASSURE_DB`, etc.
3. Streams the agent process output live.
4. Reads the evidence records generated during the run.
5. Displays a Rich terminal summary panel:
   - Total duration and event count
   - Governance outcome (`ALLOW`, `BLOCK`, `ASK`)
   - Policy citations (ISO/IEC 42001, EU AI Act)
   - Model usage, token consumption, and estimated USD cost
6. Exports a complete JSON trace to `.agentassure/traces/<trace_id>.json`.

---

## 7. CLI Command Reference

| Command | Description |
|---|---|
| `agentassure init` | Initialize AgentAssure config and directories in repo |
| `agentassure run <cmd...>` | Execute agent under runtime governance |
| `agentassure policy list` | List active policy rules and regulatory citations |
| `agentassure policy validate` | Validate syntax of policy YAML files |
| `agentassure trace list` | List recorded execution traces with integrity status |
| `agentassure trace show <id>` | Display full execution trace tree with event details |
| `agentassure approvals list` | List pending human-in-the-loop approvals |
| `agentassure approvals approve <id>` | Approve a pending tool execution |
| `agentassure approvals reject <id>` | Reject a pending tool execution |
| `agentassure ingest <file>` | Extract policy rules from PDF, DOCX, TXT, or MD |

---

## 8. Policy Engine & YAML Reference

### Policy Schema

Policies are written in declarative YAML files located in `policies/`:

```yaml
version: 1
agent: loan-agent

rules:
  - id: LOAN-POL-001
    name: Restrict High Value Disbursal
    description: Loans exceeding $50,000 require senior officer sign-off
    severity: high
    action: ASK          # ALLOW | BLOCK | ASK | SHADOW
    mode: enforce        # enforce | audit | disabled
    scope:
      tools:
        - disburse_loan
        - transfer_funds
    condition:
      field: amount
      operator: gt       # gt | gte | lt | lte | eq | ne | in | contains
      value: 50000
    controls:
      iso42001: "A.9.4"
      eu_ai_act: "Art. 14"
      note: Human oversight required for high financial exposure actions.

  - id: SEC-POL-002
    name: Block Direct Database Modifications
    severity: critical
    action: BLOCK
    mode: enforce
    scope:
      tools:
        - raw_sql_execute
    controls:
      iso42001: "A.6.2"
```

### Cost & Budget Enforcements

Policies can evaluate running trace costs and token consumption in real-time:

```yaml
  - id: COST-POL-001
    name: Enforce Session Cost Ceiling
    description: Block agent actions if accumulated session cost exceeds $0.50
    severity: high
    action: BLOCK
    mode: enforce
    condition:
      field: cost_usd
      operator: gt
      value: 0.50
    controls:
      note: Financial budget guardrail to prevent recursive agent runaways.
```

### Regulatory Compliance Citing

Every evidence record generated by AgentAssure carries the citations specified under `controls:`. When auditors inspect traces, they can verify which controls were enforced at each tool invocation.

---

## 9. Policy Ingest & RAG Extraction

Convert unstructured documents (PDF corporate bylaws, compliance memos, security standards) into executable YAML rules:

```bash
agentassure ingest compliance_handbook.pdf
```

### Auto-Detection & Fallback Logic:
1. **OpenAI**: If `OPENAI_API_KEY` is present, uses OpenAI GPT models for structured extraction.
2. **Groq**: If `GROQ_API_KEY` is present, uses Groq fast inference models.
3. **Deterministic Heuristic Fallback**: If no API key is present, AgentAssure automatically uses deterministic regex and clause analysis. **No API key is required to use ingest!**

Each candidate rule is presented in the CLI for review before being added to your policy file.

---

## 10. Audit Evidence & Cryptographic Verification

AgentAssure writes every event to a SQLite evidence store using a SHA-256 hash chain:

```text
Record 1: Hash = SHA256(Record 1 fields + GENESIS_HASH)
Record 2: Hash = SHA256(Record 2 fields + Record 1 Hash)
Record 3: Hash = SHA256(Record 3 fields + Record 2 Hash)
```

To verify the integrity of all traces across your repository:

```bash
agentassure trace list
```

If any database row was altered or deleted by an attacker or buggy script, `agentassure` detects the broken hash chain and alerts the operator.

Programmatic verification:

```python
from agentassure.evidence import EvidenceStore

store = EvidenceStore(".agentassure/evidence.db")
is_valid, errors = store.verify_integrity()
assert is_valid, f"Evidence tampering detected: {errors}"
```

---

## 11. Human-in-the-Loop Approvals

When a tool triggers an `ASK` outcome:
1. The tool execution halts and returns a pending approval ID.
2. View pending requests:
   ```bash
   agentassure approvals list
   ```
3. Resolve an approval:
   ```bash
   agentassure approvals approve app_92c81 --by "risk-officer@company.com"
   ```
4. Re-running the agent allows the approved action to execute without violation.

---

## 12. JSON Output Format

After each `agentassure run`, a JSON trace file is written to `.agentassure/traces/<trace_id>.json`:

```json
{
  "trace_id": "tr_8f21c90a",
  "agent_id": "loan-agent",
  "exported_at": "2026-09-12T09:30:00Z",
  "duration_ms": 1420.5,
  "governance": {
    "allowed": 3,
    "blocked": 1,
    "pending": 0,
    "approved": 0
  },
  "cost": {
    "model": "gpt-4o",
    "input_tokens": 4200,
    "output_tokens": 650,
    "total_tokens": 4850,
    "cost_usd": 0.03075,
    "llm_calls": 2
  },
  "events": [
    {
      "event_id": "evt_001",
      "tool_name": "disburse_loan",
      "decision": "BLOCK",
      "policy_id": "LOAN-POL-001",
      "reason": "Policy 'Restrict High Value Disbursal' matched: field 'amount'=75000 gt 50000",
      "controls": "ISO/IEC 42001 A.9.4 | EU AI Act Art. 14",
      "record_hash": "a591e0123...4f8c"
    }
  ]
}
```

---

## 13. Optional Web Dashboard

AgentAssure includes an optional real-time web dashboard for compliance officers and visual trace inspection:

```bash
python -m server.api
# or
uvicorn server.api:app --reload --port 8000
```

Navigate to `http://localhost:8000` to inspect live traces, explore the evidence hash chain, review approvals, and manage policy rules.
