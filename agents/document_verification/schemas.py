"""
DocumentVerification agent — schemas.

PURPOSE
=======
The first gate of the pipeline. Before any expensive LLM calls, this agent:
  1. Checks that all required documents for the claim category are present
  2. Detects wrong document types (TC001 — two prescriptions for a consultation)
  3. Detects cross-patient document submission (TC003 — names don't match)
  4. Flags unreadable documents with a SPECIFIC re-upload message (TC002)

WHY THIS AGENT RUNS FIRST
=========================
Failing fast on bad input saves:
  - LLM cost (we don't waste Gemini on a wrong-type doc)
  - Time (member gets instant feedback, not 30s later)
  - Trust (specific error messages are part of the assignment's evaluation)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agents.core.enums import DocumentError, DocumentType


class DocumentCheckResult(BaseModel):
    """Per-document check result, used in DocumentVerificationResult."""
    file_id: str
    document_type: DocumentType
    is_acceptable: bool
    issues: list[str] = Field(default_factory=list)
    patient_name_found: str | None = None


class DocumentVerificationInput(BaseModel):
    """Input to the DocumentVerification agent.

    The agent receives the full claim input (not a partial view) because it
    needs to compare the document list against the policy's requirements
    for the claim category.
    """
    claim_id: str
    claim_category: str
    documents: list[dict] = Field(
        default_factory=list,
        description="List of document dicts (file_id, actual_type, quality, patient_name_on_doc)",
    )
    member_name: str = Field(..., description="Member's name on the policy — used for cross-patient check")


class DocumentVerificationResult(BaseModel):
    """Output of the DocumentVerification agent.

    Two critical fields for UX:
      - user_message: the SPECIFIC message shown to the member (TC001, TC002, TC003)
      - stop_processing: if True, the orchestrator does NOT run any further agents
    """
    is_valid: bool
    stop_processing: bool = Field(
        False,
        description="If True, no further agents run. The member sees user_message.",
    )
    errors: list[DocumentError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    user_message: str = Field(
        "",
        description=(
            "Specific, actionable message for the member. Empty if is_valid. "
            "MUST name the uploaded doc type and the required doc type for TC001/TC002/TC003."
        ),
    )
    per_document_results: list[DocumentCheckResult] = Field(default_factory=list)
    documents_present: list[DocumentType] = Field(
        default_factory=list,
        description="List of document types that are present in this claim",
    )
    documents_missing: list[DocumentType] = Field(
        default_factory=list,
        description="List of required document types that are missing",
    )
    confidence: float = Field(
        1.0,
        ge=0.0,
        le=1.0,
        description="How confident we are in the verification (1.0 when types are explicit)",
    )
