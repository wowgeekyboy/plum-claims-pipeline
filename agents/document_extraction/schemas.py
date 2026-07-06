"""
DocumentExtraction agent — schemas.

PURPOSE
=======
Takes the (verified) documents and extracts structured fields:
  - patient name, doctor name, registration number
  - date, diagnosis, medicines, tests ordered
  - line items with amounts, total amount, hospital name

Two modes:
  1. TEST MODE (content provided): use the dict directly. Fast, deterministic.
  2. PRODUCTION MODE (image only): use Gemini 2.0 Flash vision to extract.

WHY TWO MODES
=============
The test cases provide `content` already (deterministic — no LLM flakiness in tests).
But the deployed system must work on real uploaded images, which need vision.
We support both, and the test mode is the default in our test runner.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agents.core.enums import DocumentType


class LineItem(BaseModel):
    """A single line item in a bill."""
    description: str
    amount: float
    quantity: int | None = 1


class ExtractedField(BaseModel):
    """A single extracted field with its confidence.

    Why per-field confidence? Some fields are easier to read than others.
    A rubber stamp over a registration number drops that field's confidence
    without invalidating the whole document.
    """
    value: Any
    confidence: float = Field(1.0, ge=0.0, le=1.0)
    raw_text: str | None = None
    extraction_method: str = Field("test_mode", description="test_mode | gemini_vision | hybrid")


class ExtractedDocument(BaseModel):
    """Full extraction result for one document."""
    file_id: str
    document_type: DocumentType

    # Patient & doctor
    patient_name: str | None = None
    doctor_name: str | None = None
    doctor_registration: str | None = None

    # Date
    date: str | None = None  # ISO format YYYY-MM-DD

    # Medical content
    diagnosis: str | None = None
    medicines: list[str] = Field(default_factory=list)
    tests_ordered: list[str] = Field(default_factory=list)
    treatment: str | None = None

    # Bill content
    hospital_name: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    total_amount: float | None = None

    # Quality metadata
    is_readable: bool = True
    extraction_confidence: float = Field(1.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)

    # Per-field confidence (optional)
    field_confidences: dict[str, float] = Field(default_factory=dict)


class DocumentExtractionInput(BaseModel):
    """Input to the DocumentExtraction agent.

    Receives the document inputs (with optional `content` for test mode or
    `image_base64` for production mode). Also receives the verification result
    so we don't waste LLM calls on already-rejected documents.
    """
    claim_id: str
    documents: list[dict] = Field(
        default_factory=list,
        description="Document inputs to extract from",
    )
    skip_if_unverified: bool = Field(
        True,
        description="If True, returns empty list when verification failed (orchestrator should respect this)",
    )


class DocumentExtractionResult(BaseModel):
    """Output of the DocumentExtraction agent."""
    extracted_documents: list[ExtractedDocument] = Field(default_factory=list)
    documents_skipped: list[str] = Field(
        default_factory=list,
        description="File IDs of documents that were skipped (e.g. unreadable)",
    )
    average_confidence: float = Field(1.0, ge=0.0, le=1.0)
    total_extraction_time_ms: float = 0.0
    llm_calls_made: int = Field(0, description="Number of Gemini calls — useful for cost tracking")
