"""
FastAPI application — exposes the orchestrator over HTTP.

Endpoints:
  POST /api/claims                  — submit a claim, get a decision
  GET  /api/claims/{claim_id}       — get decision for a claim
  GET  /api/claims/{claim_id}/trace — get full audit trail
  GET  /api/policy                  — get policy info
  GET  /health                      — health check

To run:
  uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

# Make the agents package importable when run from project root
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.api.orchestrator import run_claim  # noqa: E402
from agents.core.domain import ClaimInput  # noqa: E402
from agents.core.enums import ClaimCategory, DocumentQuality, DocumentType  # noqa: E402
from agents.core.policy_loader import get_policy  # noqa: E402


# ----------------------------------------------------------------------
# Request/Response models
# ----------------------------------------------------------------------

class DocumentRequest(BaseModel):
    file_id: str
    file_name: str | None = None
    actual_type: str | None = None
    quality: str = "GOOD"
    content: dict[str, Any] | None = None
    patient_name_on_doc: str | None = None


class ClaimRequest(BaseModel):
    """Request body for POST /api/claims."""
    claim_id: str | None = None
    member_id: str
    policy_id: str = "PLUM_GHI_2024"
    claim_category: str
    treatment_date: str  # ISO format
    claimed_amount: float
    hospital_name: str | None = None
    documents: list[DocumentRequest] = Field(default_factory=list)
    ytd_claims_amount: float = 0.0


# ----------------------------------------------------------------------
# In-memory claim store (for demo — production would use a database)
# ----------------------------------------------------------------------

class ClaimStore:
    """Simple in-memory store for claims + their traces."""

    def __init__(self) -> None:
        self._claims: dict[str, dict] = {}

    def save(self, claim_id: str, decision: dict, trace_markdown: str) -> None:
        self._claims[claim_id] = {
            "decision": decision,
            "trace_markdown": trace_markdown,
            "saved_at": datetime.utcnow().isoformat(),
        }

    def get(self, claim_id: str) -> dict | None:
        return self._claims.get(claim_id)

    def list_all(self) -> list[str]:
        return list(self._claims.keys())


store = ClaimStore()


# ----------------------------------------------------------------------
# FastAPI app
# ----------------------------------------------------------------------

app = FastAPI(
    title="Plum Claims Processing API",
    description="Multi-agent health insurance claims processing pipeline",
    version="1.0.0",
)

# CORS — allow Streamlit to call us
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "service": "plum-claims-pipeline"}


@app.get("/api/policy")
async def get_policy_info() -> dict:
    """Return the current policy configuration."""
    policy = get_policy()
    return {
        "policy_id": policy.policy_id,
        "policy_name": policy.policy_name,
        "insurer": policy.insurer,
        "sum_insured_per_employee": policy.sum_insured_per_employee,
        "annual_opd_limit": policy.annual_opd_limit,
        "per_claim_limit": policy.per_claim_limit,
        "categories": [c.value for c in policy.opd_categories.keys()],
        "network_hospitals_count": len(policy.network_hospitals),
    }


@app.post("/api/claims")
async def submit_claim(req: ClaimRequest) -> dict:
    """Submit a claim and get back the decision.

    Returns the decision, the full trace, and a human-readable trace markdown.
    """
    # Build ClaimInput from the request
    try:
        category = ClaimCategory(req.claim_category)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid claim_category: {req.claim_category}. Must be one of: {[c.value for c in ClaimCategory]}",
        )

    try:
        treatment_date = date.fromisoformat(req.treatment_date)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid treatment_date: {req.treatment_date}. Must be YYYY-MM-DD.",
        )

    # Build documents
    documents = []
    for d in req.documents:
        actual_type = None
        if d.actual_type:
            try:
                actual_type = DocumentType(d.actual_type)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid document type: {d.actual_type}. Must be one of: {[t.value for t in DocumentType]}",
                )
        try:
            quality = DocumentQuality(d.quality)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid quality: {d.quality}. Must be one of: {[q.value for q in DocumentQuality]}",
            )
        documents.append({
            "file_id": d.file_id,
            "file_name": d.file_name,
            "actual_type": actual_type,
            "quality": quality,
            "content": d.content,
            "patient_name_on_doc": d.patient_name_on_doc,
        })

    # Build ClaimInput
    claim = ClaimInput(
        claim_id=req.claim_id,
        member_id=req.member_id,
        policy_id=req.policy_id,
        claim_category=category,
        treatment_date=treatment_date,
        claimed_amount=req.claimed_amount,
        hospital_name=req.hospital_name,
        documents=documents,
        ytd_claims_amount=req.ytd_claims_amount,
    )

    # Run the orchestrator
    try:
        trace = run_claim(claim)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Pipeline error: {e}")

    # Get the final decision
    from agents.api.orchestrator import get_orchestrator
    from agents.core.state import make_initial_state
    state = make_initial_state(claim)
    state["trace"] = []
    final_state = get_orchestrator().invoke(state)
    decision = final_state.get("decision", {})

    # Save to store
    trace_md = trace.to_markdown()
    store.save(trace.claim_id, decision, trace_md)

    # Build response
    return {
        "claim_id": trace.claim_id,
        "decision": decision,
        "trace_markdown": trace_md,
        "agent_traces": [
            {
                "agent": t.agent_name.value,
                "status": t.status.value,
                "duration_ms": t.duration_ms,
                "confidence": t.confidence_contribution,
                "notes": t.notes,
                "error": t.error,
            }
            for t in trace.agent_traces
        ],
    }


@app.get("/api/claims/{claim_id}")
async def get_claim(claim_id: str) -> dict:
    """Get a previously submitted claim's decision."""
    result = store.get(claim_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    return result


@app.get("/api/claims/{claim_id}/trace")
async def get_claim_trace(claim_id: str) -> dict:
    """Get the full audit trace for a claim."""
    result = store.get(claim_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Claim {claim_id} not found")
    return {"claim_id": claim_id, "trace_markdown": result["trace_markdown"]}


@app.get("/api/claims")
async def list_claims() -> dict:
    """List all submitted claim IDs."""
    return {"claim_ids": store.list_all()}


# ----------------------------------------------------------------------
# CLI entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
