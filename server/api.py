"""
FastAPI Server Endpoints for AgentAssure (Evidence, Approvals, Integrity)
"""

from typing import List, Optional
from fastapi import FastAPI
from server.evidence import EvidenceStore, EvidenceRecord


app = FastAPI(title="AgentAssure Governance API", version="0.1.0")
evidence_store = EvidenceStore()


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


@app.get("/health")
def health():
    return {"status": "ok", "service": "AgentAssure Server"}
