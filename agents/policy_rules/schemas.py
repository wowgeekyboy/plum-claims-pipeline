"""
PolicyRulesEngine agent — schemas.

PURPOSE
=======
Applies ALL financial and coverage rules to compute the final approved amount:
  1. Per-claim limit check (TC008: ₹7500 > ₹5000 → REJECT)
  2. Network hospital discount (TC010: 20% first, on ₹4500 → ₹3600)
  3. Co-pay (10% on the post-discount amount → ₹3240)
  4. Sub-limit per category
  5. Pre-authorization required check (TC007: MRI > ₹10K without pre-auth)
  6. Exclusions — line-item level (TC006: root canal approved, whitening rejected)
  7. Annual OPD limit

THE ORDER OF OPERATIONS MATTERS
================================
Network discount BEFORE co-pay (TC010 explicitly tests this). This is a
common business rule: the discount reduces the amount the member is
"responsible" for, and co-pay is a percentage of that responsibility.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents.core.enums import RejectionReason


class LineItemDecision(BaseModel):
    """Decision on a single line item in a bill (TC006: dental case)."""
    description: str
    amount: float
    is_approved: bool
    approved_amount: float = 0.0
    reason: str | None = None
    exclusion_matched: str | None = Field(
        None,
        description="If rejected, which exclusion list matched (e.g. 'dental_exclusions: Teeth Whitening')",
    )


class PolicyEvaluation(BaseModel):
    """Full output of the PolicyRulesEngine."""
    is_valid: bool
    claimed_amount: float
    approved_amount: float = 0.0

    # Sub-limit
    category_sub_limit: float
    sub_limit_applied: bool = False
    sub_limit_capped_amount: float | None = None

    # Network discount
    is_network_hospital: bool = False
    network_discount_percent: float = 0.0
    network_discount_amount: float = 0.0
    amount_after_network_discount: float | None = None

    # Co-pay
    copay_percent: float = 0.0
    copay_amount: float = 0.0
    amount_after_copay: float | None = None

    # Per-claim limit
    per_claim_limit: float
    per_claim_exceeded: bool = False

    # Pre-auth
    pre_auth_required: bool = False
    pre_auth_obtained: bool = True  # assume yes unless told otherwise
    high_value_tests: list[str] = Field(default_factory=list)

    # Line items
    line_item_decisions: list[LineItemDecision] = Field(default_factory=list)

    # Rejection
    rejection_reasons: list[RejectionReason] = Field(default_factory=list)
    user_message: str = ""
    notes: list[str] = Field(default_factory=list)
    confidence: float = Field(1.0, ge=0.0, le=1.0)

    # Calculation breakdown — for transparency
    calculation_steps: list[str] = Field(
        default_factory=list,
        description="Step-by-step math: 'claimed ₹1500', 'network disc 20% = -₹300', etc.",
    )


class PolicyRulesInput(BaseModel):
    """Input to PolicyRulesEngine."""
    claim_id: str
    claim_category: str
    claimed_amount: float
    treatment_date: str  # ISO date
    hospital_name: str | None = None
    line_items: list[dict] = Field(
        default_factory=list,
        description="Extracted line items: [{description, amount, quantity}]",
    )
    diagnosis: str | None = None
    tests_ordered: list[str] = Field(default_factory=list)
    pre_auth_obtained: bool = Field(
        False,
        description=(
            "Whether pre-authorization was obtained. Defaults to False — "
            "conservative: assume no pre-auth unless explicitly provided. "
            "This matches real-world behavior: members must request pre-auth "
            "before high-value procedures."
        ),
    )
    ytd_claims_amount: float = 0.0
    annual_opd_used: float = 0.0
