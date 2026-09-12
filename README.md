# AgentAssure — Runtime Governance for AI Agents

[![CI Tests](https://github.com/v-jishnu/Agent_Assure/actions/workflows/tests.yml/badge.svg)](https://github.com/v-jishnu/Agent_Assure/actions/workflows/tests.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> **AgentAssure is a lightweight runtime governance layer for AI agents that turns organizational policies into executable controls, enforces them at the point of action, and gives developers an immediate trace of what happened, why, and what it cost.**

---

```text
            ORGANIZATIONAL POLICY (PDF, DOCX, TXT, MD)
                                 │
                             RAG / Ingest
                                 │
                                 ▼
                     DECLARATIVE POLICY (YAML)
                                 │
                                 ▼
AI AGENT ────────► [ AgentAssure Runtime ] ────────► TARGET ENVIRONMENT
                          │              │
                          ▼              ▼
                 Point-of-Action    Cryptographic
                   Enforcement      Evidence Store
                (ALLOW/BLOCK/ASK)  (SHA-256 Hash Chain)
                          │              │
                          ▼              ▼
                    CLI Summary     Trace JSON
```

---

## Why AgentAssure?

Most agent governance solutions are heavy dashboard platforms or complex multi-agent "supervisors" that add latency, hallucinate, and break production workflows.

AgentAssure is deliberately built as **lightweight runtime software**:
- **Point-of-action enforcement**: Governs actions right as tools are invoked, before sensitive execution occurs.
- **Zero-invasive `@aa.govern` decorator**: Add governance to any existing tool with one line of code.
- **Cost & Token tracking**: Built-in pricing for GPT-4o, Claude 3.5, Gemini, and Groq without forcing external SDK dependencies.
- **Deterministic by default**: Runs completely offline without mandatory LLM API keys.
- **RAG Policy Ingestion**: Extract executable YAML policies from raw compliance manuals, with automated LLM detection and deterministic heuristic fallback.
- **Tamper-evident audit trail**: Evidence records form a cryptographic SHA-256 hash chain with ISO/IEC 42001 & EU AI Act regulatory citations.

---

## 3-Minute Quickstart

### 1. Install

```bash
pip install git+https://github.com/v-jishnu/Agent_Assure.git
```

Or clone and set up locally:

```bash
# Linux / macOS
bash scripts/setup.sh

# Windows
.\scripts\setup.ps1
```

### 2. Initialize in your repository

```bash
agentassure init
```

This creates `agentassure.yaml` and a default policy in `policies/policy.yaml`.

### 3. Add `@aa.govern` to your agent's tools

```python
from agentassure import AgentAssure

aa = AgentAssure()

@aa.govern
def disburse_loan(applicant_id: str, amount: float):
    # Enforced against policies/policy.yaml before execution!
    return {"status": "SUCCESS", "disbursed": amount}

if __name__ == "__main__":
    # If amount exceeds limit in policy, AgentAssure raises PolicyViolationError!
    disburse_loan(applicant_id="app_123", amount=75000)
```

### 4. Run governed

```bash
agentassure run python my_agent.py
```

You will see live execution followed by an immediate Rich terminal summary:

```text
╭────────────────── AgentAssure Run Summary: tr_8f21c ──────────────────╮
│ Agent ID: loan-agent              Duration: 1420ms                    │
│ Policy Resolution: deterministic  Total Events: 2                     │
│                                                                       │
│ GOVERNANCE DECISIONS                                                  │
│   • disburse_loan ──► [BLOCK]                                         │
│     Reason: Policy 'Restrict High Value Disbursal' matched:           │
│             field 'amount'=75000 gt 50000                             │
│     Citations: ISO/IEC 42001 A.9.4 | EU AI Act Art. 14                │
│                                                                       │
│ COST & TOKEN USAGE                                                    │
│   Model: gpt-4o                   Total Tokens: 4,850                 │
│   Prompt: 4,200 | Completion: 650 Est. Cost: $0.030750                │
╰───────────────────────────────────────────────────────────────────────╯
Trace JSON: .agentassure/traces/tr_8f21c.json
```

---

## Key Features

### 1. CLI-First Developer Experience

AgentAssure provides an intuitive suite of CLI commands:

```bash
agentassure run python agent.py       # Run governed agent subprocess
agentassure policy list               # Inspect loaded policies and rules
agentassure policy validate           # Validate YAML policy syntax
agentassure trace list                # View traces & cryptographic integrity
agentassure trace show <trace_id>     # Display detailed trace event tree
agentassure approvals list            # View pending human-in-the-loop approvals
agentassure approvals approve <id>    # Sign off on a blocked high-risk action
agentassure ingest company_policy.pdf # Ingest policy documents into YAML rules
```

### 2. Declarative Policy Engine

Define behavioral boundaries, tool whitelists/blacklists, and budget limits in YAML:

```yaml
version: 1
agent: loan-agent

rules:
  - id: LOAN-POL-001
    name: High Value Disbursal Sign-Off
    severity: high
    action: ASK          # ALLOW | BLOCK | ASK | SHADOW
    mode: enforce        # enforce | audit | disabled
    scope:
      tools: [disburse_loan]
    condition:
      field: amount
      operator: gt
      value: 50000
    controls:
      iso42001: "A.9.4"
      eu_ai_act: "Art. 14"

  - id: BUDGET-001
    name: Max Session Cost
    action: BLOCK
    mode: enforce
    condition:
      field: cost_usd
      operator: gt
      value: 0.50
```

### 3. Framework Adapters

AgentAssure seamlessly integrates into popular agent frameworks:

#### OpenAI Automatic Token Capture
```python
from agentassure.adapters.openai import instrument
instrument()  # Automatically tracks prompt/completion tokens and costs
```

#### LangGraph
```python
from agentassure.adapters.langgraph import LangGraphAdapter
adapter = LangGraphAdapter(aa)
governed_tool = adapter.wrap_tool(my_tool)
```

#### Model Context Protocol (MCP)
```python
from agentassure.adapters.mcp import MCPAdapter
mcp_governor = MCPAdapter(aa)

@mcp_governor.govern_tool(tool_name="database_query")
async def db_query(query: str):
    ...
```

### 4. Cryptographic Tamper-Evident Evidence Store

Every event, policy decision, input, and output is saved into SQLite as a SHA-256 hash chain:
- **Redaction at Rest**: Credit card numbers, API keys, and Indian PII (Aadhaar, PAN) are masked before persistence.
- **Verification**: Run `agentassure trace list` to verify hash-chain integrity across all sessions.

### 5. RAG Policy Ingestion (Auto-detect + Heuristic Fallback)

Ingest PDF, DOCX, TXT, or Markdown documents directly into policy rules:

```bash
agentassure ingest Risk_Management_Policy.pdf
```

Resolver priority:
1. `OPENAI_API_KEY` present → OpenAI GPT extraction
2. `GROQ_API_KEY` present → Groq extraction
3. **No keys present** → Deterministic keyword & clause heuristic fallback (works offline!)

---

## Documentation

For comprehensive guides, API references, and architecture details, see:
- [USER_GUIDE.md](USER_GUIDE.md) — Complete user guide and walkthrough
- [policies/](policies/) — Example declarative policy files

---

## Optional Web Dashboard

AgentAssure is CLI-first, but includes an optional web dashboard for compliance officers:

```bash
python -m server.api
# Open http://localhost:8000 in your browser
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
