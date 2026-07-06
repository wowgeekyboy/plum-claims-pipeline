"""
FraudDetection agent — schemas.

PURPOSE
=======
Detects claim patterns that may indicate fraud. Does NOT reject — it
routes to MANUAL_REVIEW for human investigation.

The agent looks for:
  1. Same-day claims exceeding the limit (TC009: 4 claims in one day > 2 limit)
  2. Monthly claims exceeding the limit
  3. High-value claims (auto-flagged for review)
  4. Document alterations (cancelled amounts, multiple stamps)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents.core.enums import FraudSignal


class FraudEvaluation(BaseModel):
    """Output of FraudDetection agent."""
    fraud_score: float = Field(0.0, ge=0.0, le=1.0)
    signals_triggered: list[FraudSignal] = Field(default_factory=list)
    requires_manual_review: bool = False
    notes: list[str] = Field(default_factory=list)
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    user_message: str = Field("", description="Message to ops team, not the member")

    # Detail per signal
    same_day_claims_count: int = 0
    monthly_claims_count: int = 0
    claimed_amount: float = 0.0
    is_high_value: bool = False


class FraudDetectionInput(BaseModel):
    """Input to FraudDetection agent."""
    claim_id: str
    member_id: str
    treatment_date: str
    claimed_amount: float
    same_day_claims_count: int = Field(0, description="How many claims the member has on this day, including this one")
    monthly_claims_count: int = Field(0, description="How many claims in this month, including this one")
    document_warnings: list[str] = Field(
        default_factory=list,
        description="Warnings from extraction (e.g. 'cancellation marks detected')",
    )
