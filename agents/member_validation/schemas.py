"""
MemberValidation agent — schemas.

PURPOSE
=======
Validates that the member who submitted the claim is:
  1. On the policy (exists in the member roster)
  2. Eligible for this category of claim
  3. Past the relevant waiting period (initial 30 days, condition-specific 90/180/270/365/730 days)

This is the gate that catches TC005 (diabetes claim within 90-day waiting period).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from agents.core.enums import RejectionReason


class WaitingPeriodStatus(BaseModel):
    """Status of one specific waiting period rule."""
    condition: str = Field(..., description="e.g. 'initial', 'diabetes', 'maternity'")
    required_days: int
    elapsed_days: int
    is_eligible: bool
    eligible_from_date: date | None = Field(
        None,
        description="The date the member becomes eligible (for messaging)",
    )
    message: str = Field("", description="Human-readable explanation")


class MemberValidationResult(BaseModel):
    """Output of the MemberValidation agent."""
    is_valid: bool
    member_found: bool
    member_id: str
    member_name: str | None = None
    relationship: str | None = None
    join_date: date | None = None

    # Waiting period checks
    waiting_period_checks: list[WaitingPeriodStatus] = Field(default_factory=list)
    initial_waiting_passed: bool = True
    condition_waiting_passed: bool = True
    days_until_eligible: int = 0

    # Errors
    rejection_reasons: list[RejectionReason] = Field(default_factory=list)
    user_message: str = Field("", description="Specific message if invalid")
    confidence: float = Field(1.0, ge=0.0, le=1.0)


class MemberValidationInput(BaseModel):
    """Input to MemberValidation."""
    claim_id: str
    member_id: str
    treatment_date: date
    diagnosis: str | None = Field(
        None,
        description="The diagnosis from extraction. Used to look up specific waiting periods.",
    )
    claim_category: str
