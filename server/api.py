"""
FastAPI Server Endpoints for AgentAssure (Evidence, Approvals, Integrity)
"""

import os
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from agentassure.evidence import EvidenceStore, EvidenceRecord


# The demo writes to demo_evidence.db; override with AGENTASSURE_DB to point
# the console at a different evidence file.
DB_PATH = os.environ.get("AGENTASSURE_DB", "demo_evidence.db")
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="AgentAssure Governance API", version="0.1.0")
evidence_store = EvidenceStore(DB_PATH)


@app.get("/api/v1/evidence", response_model=List[EvidenceRecord])
def get_evidence(trace_id: Optional[str] = None, session_id: Optional[str] = None):
    return evidence_store.get_records(trace_id=trace_id, session_id=session_id)


@app.get("/api/v1/evidence/verify")
def verify_integrity():
    is_valid, errors = evidence_store.verify_integrity()
    return {
        "is_valid": is_valid,
        "errors": errors,
        "status": "SECURE" if is_valid else "TAMPERED_DETECTED"
    }


@app.get("/api/v1/stats")
def stats() -> Dict:
    """
    Headline numbers for the governance console.

    Everything is derived from the evidence store, so the console reports what
    the running system actually recorded rather than a separate counter that
    could drift away from the audit trail.
    """
    records = evidence_store.get_records()
    is_valid, errors = evidence_store.verify_integrity()

    decisions = Counter(r.decision for r in records if r.decision)
    by_control: Dict[str, Dict] = {}
    for r in records:
        if not r.controls or r.decision in (None, "ALLOW"):
            continue
        entry = by_control.setdefault(
            r.controls, {"citation": r.controls, "count": 0, "policies": set()}
        )
        entry["count"] += 1
        if r.policy_id:
            entry["policies"].add(r.policy_id)

    return {
        "records": len(records),
        "traces": len({r.trace_id for r in records}),
        "sessions": len({r.session_id for r in records}),
        "blocked": decisions.get("BLOCK", 0),
        "pending_approval": decisions.get("ASK", 0),
        "allowed": decisions.get("ALLOW", 0),
        "redactions": sum(r.redactions for r in records),
        "cited": sum(1 for r in records if r.controls),
        "chain": {"is_valid": is_valid, "errors": errors},
        "by_control": [
            {**v, "policies": sorted(v["policies"])}
            for v in sorted(by_control.values(), key=lambda x: -x["count"])
        ],
    }


@app.get("/api/v1/traces")
def traces() -> List[Dict]:
    """One row per agent run, newest first."""
    records = evidence_store.get_records()
    grouped: Dict[str, Dict] = {}
    for r in records:
        g = grouped.setdefault(r.trace_id, {
            "trace_id": r.trace_id, "session_id": r.session_id,
            "agent_id": r.agent_id, "started_at": r.timestamp,
            "records": 0, "blocked": 0, "pending": 0, "tools": [],
        })
        g["records"] += 1
        if r.decision == "BLOCK":
            g["blocked"] += 1
        elif r.decision == "ASK":
            g["pending"] += 1
        if r.event_type == "tool_call" and r.tool_name:
            g["tools"].append(r.tool_name)
    return sorted(grouped.values(), key=lambda g: g["started_at"], reverse=True)


@app.get("/health")
def health():
    return {"status": "ok", "service": "AgentAssure Server"}


# Serve the governance console from the same process, so one uvicorn command
# runs the whole thing. Mounted last so the API routes above take precedence.
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="console")
