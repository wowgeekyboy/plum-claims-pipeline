"""
Domain models: the shape of data entering the system.

These are the "input contracts" — what a claim submission looks like,
what a document upload looks like, what a policy record looks like.

Note: we keep these separate from the per-agent schemas in `agents/*/schemas.py`
because domain models are *shared* (multiple agents read them), while agent
schemas are *specific* (one agent's input/output).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agents.core.enums import (
    ClaimCategory,
    ComponentFailure,
    DocumentQuality,
    DocumentType,
    Relationship,
)


# ============================================================
# Document input
# ============================================================

class DocumentInput(BaseModel):
    """A single document uploaded as part of a claim.

    The DocumentVerification agent uses `actual_type` (when present) to verify
    that the right docs are uploaded. The DocumentExtraction agent uses
    `content` (when present) OR `image_base64` to extract fields.

    Why so many optional fields? Real production systems must support:
      - test fixtures (content + actual_type provided)
      - live image uploads (image_base64 + actual_type detected)
      - error cases (no actual_type, no content — system must fail gracefully)

    Flexibility here is what makes the system testable AND deployable.
    """
    model_config = ConfigDict(use_enum_values=False, str_strip_whitespace=True)

    file_id: str = Field(..., description="Unique ID for this document within the claim")
    file_name: str | None = Field(None, description="Original file name (e.g. 'prescription.jpg')")
    actual_type: DocumentType | None = Field(
        None,
        description=(
            "Ground-truth or detected document type. "
            "In test mode, this is provided. In production, the DocumentExtraction "
            "agent detects it from image content."
        ),
    )
    quality: DocumentQuality = Field(
        DocumentQuality.GOOD,
        description="Quality assessment — affects whether the doc is usable",
    )
    content: dict[str, Any] | None = Field(
        None,
        description=(
            "Pre-extracted content for test cases. If provided, the DocumentExtraction "
            "agent uses this directly instead of calling Gemini. Mirrors the `content` "
            "field in test_cases.json."
        ),
    )
    patient_name_on_doc: str | None = Field(
        None,
        description=(
            "Patient name as written on the document. Used by DocumentVerification "
            "to detect cross-patient document submission (TC003)."
        ),
    )
    image_base64: str | None = Field(
        None,
        description="Base64-encoded image bytes (for Gemini vision input)",
    )
    image_mime_type: str | None = Field(
        None,
        description="MIME type, e.g. 'image/jpeg' or 'application/pdf'",
    )


# ============================================================
# Claim input
# ============================================================

class ClaimHistoryItem(BaseModel):
    """A prior claim — used for fraud detection (TC009)."""
    claim_id: str
    date: date
    amount: float
    provider: str | None = None
    diagnosis: str | None = None


class ClaimInput(BaseModel):
    """The full claim submission as it enters the system.

    Mirrors the structure of test_cases.json input fields exactly,
    plus a few extras for failure simulation and history.
    """
    model_config = ConfigDict(use_enum_values=False, str_strip_whitespace=True)

    claim_id: str | None = Field(
        None,
        description=(
            "Optional claim ID. If not provided, the system generates one "
            "(e.g. 'CLM_0001')."
        ),
    )
    member_id: str = Field(..., description="Member ID, e.g. 'EMP001' or 'DEP001'")
    policy_id: str = Field(..., description="Policy ID, e.g. 'PLUM_GHI_2024'")
    claim_category: ClaimCategory = Field(..., description="OPD claim category")
    treatment_date: date = Field(..., description="Date of treatment (used for waiting periods)")
    claimed_amount: float = Field(..., ge=0, description="Total amount claimed in INR")
    hospital_name: str | None = Field(None, description="Hospital/clinic name (for network check)")
    documents: list[DocumentInput] = Field(
        default_factory=list,
        description="All documents uploaded for this claim",
    )

    # Optional context
    ytd_claims_amount: float = Field(
        0.0,
        ge=0,
        description="Year-to-date claims for this member (used for sub-limit checks)",
    )
    pre_auth_obtained: bool = Field(
        False,
        description=(
            "Whether pre-authorization was obtained. Defaults to False (conservative). "
            "Set to True when the member provides a pre-auth reference number."
        ),
    )
    claims_history: list[ClaimHistoryItem] = Field(
        default_factory=list,
        description="Prior claims for this member (used for fraud detection)",
    )

    # For TC011 — graceful failure simulation
    simulate_component_failure: ComponentFailure | None = Field(
        None,
        description="If set, the orchestrator injects a failure into that agent",
    )

    # Metadata
    submitted_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the claim was submitted (server-side timestamp)",
    )


# ============================================================
# Policy & Member (loaded from policy_terms.json)
# ============================================================

class PolicyRule(BaseModel):
    """Generic rule — a placeholder for future per-rule config.

    For now, we load the full policy as raw dict + type it. This keeps
    the code decoupled from the JSON structure (the assignment said
    "do not hardcode policy logic" — meaning the policy can change
    without code changes).
    """
    model_config = ConfigDict(extra="allow")  # accept all extra fields from JSON


class OPDCategoryConfig(BaseModel):
    """Configuration for one OPD category (consultation, dental, etc.)."""
    model_config = ConfigDict(extra="allow")

    sub_limit: float
    copay_percent: float = 0
    network_discount_percent: float = 0
    requires_prescription: bool = False
    requires_pre_auth: bool = False
    pre_auth_threshold: float | None = None
    high_value_tests_requiring_pre_auth: list[str] = Field(default_factory=list)
    covered: bool = True


class WaitingPeriods(BaseModel):
    """All waiting period rules."""
    model_config = ConfigDict(extra="allow")

    initial_waiting_period_days: int = 30
    pre_existing_conditions_days: int = 365
    specific_conditions: dict[str, int] = Field(default_factory=dict)


class Exclusions(BaseModel):
    """All exclusion rules."""
    model_config = ConfigDict(extra="allow")

    conditions: list[str] = Field(default_factory=list)
    dental_exclusions: list[str] = Field(default_factory=list)
    vision_exclusions: list[str] = Field(default_factory=list)


class FraudThresholds(BaseModel):
    """Fraud detection thresholds."""
    model_config = ConfigDict(extra="allow")

    same_day_claims_limit: int = 2
    monthly_claims_limit: int = 6
    high_value_claim_threshold: float = 25000
    auto_manual_review_above: float = 25000
    fraud_score_manual_review_threshold: float = 0.80


class Member(BaseModel):
    """A single member (employee or dependent) from the policy roster."""
    model_config = ConfigDict(extra="allow")

    member_id: str
    name: str
    date_of_birth: date
    gender: str | None = None
    relationship: Relationship
    join_date: date | None = None
    dependents: list[str] = Field(default_factory=list)
    primary_member_id: str | None = None


class Policy(BaseModel):
    """The full policy, loaded from policy_terms.json.

    Note: we keep `raw` as the original dict so any policy field not
    explicitly modeled is still accessible. This makes the system
    forward-compatible with new policy fields without code changes.
    """
    model_config = ConfigDict(extra="allow")

    policy_id: str
    policy_name: str
    insurer: str | None = None

    # OPD category configs (consultation, dental, vision, etc.)
    opd_categories: dict[ClaimCategory, OPDCategoryConfig] = Field(default_factory=dict)

    # Coverage
    sum_insured_per_employee: float = 0
    annual_opd_limit: float = 0
    per_claim_limit: float = 0

    # Rules
    waiting_periods: WaitingPeriods = Field(default_factory=WaitingPeriods)
    exclusions: Exclusions = Field(default_factory=Exclusions)
    pre_authorization: dict[str, Any] = Field(default_factory=dict)
    fraud_thresholds: FraudThresholds = Field(default_factory=FraudThresholds)

    # Document requirements per category
    document_requirements: dict[ClaimCategory, dict[str, list[DocumentType]]] = Field(
        default_factory=dict
    )

    # Network
    network_hospitals: list[str] = Field(default_factory=list)

    # Submission rules
    submission_deadline_days: int = 30
    minimum_claim_amount: float = 500
    currency: str = "INR"

    # Members
    members: list[Member] = Field(default_factory=list)

    # Raw — for any unmodeled fields
    raw: dict[str, Any] = Field(default_factory=dict)

    def get_member(self, member_id: str) -> Member | None:
        """Look up a member by ID. Returns None if not found."""
        for m in self.members:
            if m.member_id == member_id:
                return m
        return None

    def get_category_config(self, category: ClaimCategory) -> OPDCategoryConfig | None:
        """Get OPD config for a category."""
        return self.opd_categories.get(category)
