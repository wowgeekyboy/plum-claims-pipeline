"""
Decision agent — schemas.

PURPOSE
=======
The final agent. Takes all upstream signals (document_verification, extraction,
member_validation, policy_evaluation, fraud_evaluation) and produces THE final
decision: APPROVED / PARTIAL / REJECTED / MANUAL_REVIEW.

Decision priority (highest first):
  1. MANUAL_REVIEW (fraud) — never override
  2. REJECTED (waiting period, excluded condition, pre-auth missing, per-claim exceeded)
  3. PARTIAL (some line items approved, some rejected)
  4. APPROVED (full or near-full)

Confidence score = weighted average of upstream agents' confidences, with
penalties for failed/skipped agents.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents.core.enums import DecisionType, RejectionReason


class Decision(BaseModel):
    """The final output of the entire pipeline."""
    decision: DecisionType
    approved_amount: float = 0.0
    rejection_reasons: list[RejectionReason] = Field(default_factory=list)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    user_message: str = Field("", description="Message to show the member")
    ops_notes: list[str] = Field(
        default_factory=list,
        description="Notes for the ops team (not shown to the member)",
    )
    requires_manual_review: bool = False
    next_steps: list[str] = Field(
        default_factory=list,
        description="What the member should do next (for REJECTED/MANUAL_REVIEW)",
    )


class DecisionInput(BaseModel):
    """Input to the Decision agent.

    Receives the full state — or just the relevant pieces — and synthesizes
    the final decision.
    """
    claim_id: str
    claimed_amount: float

    # Upstream results (any may be None if that agent failed)
    document_verification: dict | None = None
    member_validation: dict | None = None
    policy_evaluation: dict | None = None
    fraud_evaluation: dict | None = None

    # For graceful failure (TC011)
    failed_agents: list[str] = Field(
        default_factory=list,
        description="Names of agents that failed or were skipped",
    )
