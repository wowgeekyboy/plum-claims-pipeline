"""
DocumentExtractionAgent — implementation.

Transforms raw documents into structured ExtractedDocument objects.

Two modes:
  - TEST MODE: when document.content is provided (test_cases.json style),
    use the dict directly. Fast, deterministic, no LLM.
  - PRODUCTION MODE: when document.image_base64 is provided, use Gemini
    vision to extract. (Implemented in a follow-up — for now, test mode.)

Test cases that depend on this agent:
  TC004 — clean consultation (prescription + bill extraction)
  TC005 — diabetes diagnosis (for waiting period)
  TC006 — dental line items (root canal vs whitening)
  TC007 — MRI test (for pre-auth check)
  TC011 — graceful failure (component failure)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.core.domain import DocumentInput, Policy
from agents.core.enums import (
    AgentName,
    AgentStatus,
    DocumentQuality,
    DocumentType,
)
from agents.core.state import AgentState
from agents.core.trace import AgentTrace
from agents.document_extraction.schemas import (
    DocumentExtractionResult,
    ExtractedDocument,
    LineItem,
)


class DocumentExtractionAgent:
    """Extracts structured fields from documents.

    Currently implements TEST MODE only (uses content dict directly).
    Production mode (Gemini vision) will be added in a follow-up — but
    the public interface stays the same.
    """

    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    def run(self, state: AgentState) -> DocumentExtractionResult:
        """Extract from all verified documents."""
        documents = state["claim_input"].documents
        # Don't extract if verification failed (orchestrator should respect this,
        # but we double-check here)
        verification = state.get("document_verification", {})
        if verification and not verification.get("is_valid", True):
            return DocumentExtractionResult(
                extracted_documents=[],
                documents_skipped=[d.file_id for d in documents],
                average_confidence=0.0,
                total_extraction_time_ms=0.0,
                llm_calls_made=0,
            )

        extracted: list[ExtractedDocument] = []
        skipped: list[str] = []
        confidences: list[float] = []
        llm_calls = 0
        started = datetime.now(timezone.utc)

        for doc in documents:
            if doc.quality == DocumentQuality.UNREADABLE:
                # Skip unreadable — they can't be extracted
                skipped.append(doc.file_id)
                extracted.append(
                    ExtractedDocument(
                        file_id=doc.file_id,
                        document_type=doc.actual_type or DocumentType.UNKNOWN,
                        is_readable=False,
                        extraction_confidence=0.0,
                        warnings=["Document marked UNREADABLE — extraction skipped"],
                    )
                )
                confidences.append(0.0)
                continue

            if doc.content is not None:
                # TEST MODE — use content directly
                ext = self._extract_from_content(doc)
                extracted.append(ext)
                confidences.append(ext.extraction_confidence)
            elif doc.image_base64 is not None:
                # PRODUCTION MODE — would call Gemini here
                # For now, return an UNKNOWN doc with a warning
                ext = ExtractedDocument(
                    file_id=doc.file_id,
                    document_type=doc.actual_type or DocumentType.UNKNOWN,
                    is_readable=True,
                    extraction_confidence=0.0,
                    warnings=["Production extraction not yet implemented — please use test mode"],
                )
                extracted.append(ext)
                confidences.append(0.0)
                llm_calls += 0
            else:
                # No content, no image — empty extraction
                ext = ExtractedDocument(
                    file_id=doc.file_id,
                    document_type=doc.actual_type or DocumentType.UNKNOWN,
                    is_readable=True,
                    extraction_confidence=0.0,
                    warnings=["No content or image provided — extraction returned empty"],
                )
                extracted.append(ext)
                confidences.append(0.0)

        completed = datetime.now(timezone.utc)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return DocumentExtractionResult(
            extracted_documents=extracted,
            documents_skipped=skipped,
            average_confidence=avg_conf,
            total_extraction_time_ms=(completed - started).total_seconds() * 1000,
            llm_calls_made=llm_calls,
        )

    # ------------------------------------------------------------------
    # Test mode extraction
    # ------------------------------------------------------------------

    def _extract_from_content(self, doc: DocumentInput) -> ExtractedDocument:
        """Extract fields from a content dict (test mode).

        The content dict comes from test_cases.json — it's the "ground truth"
        that the system would otherwise have to extract from an image.
        """
        content = doc.content or {}
        doc_type = doc.actual_type or DocumentType.UNKNOWN

        # Per-type field mapping
        line_items: list[LineItem] = []
        total_amount: float | None = None
        warnings: list[str] = []
        field_confidences: dict[str, float] = {}

        # ---- Prescription ----
        if doc_type == DocumentType.PRESCRIPTION:
            patient_name = content.get("patient_name")
            doctor_name = content.get("doctor_name")
            doctor_reg = content.get("doctor_registration")
            date_str = content.get("date")
            diagnosis = content.get("diagnosis")
            medicines = content.get("medicines", [])
            tests_ordered = content.get("tests_ordered", [])
            treatment = content.get("treatment")

        # ---- Hospital bill / Pharmacy bill / Lab report ----
        elif doc_type in (DocumentType.HOSPITAL_BILL, DocumentType.PHARMACY_BILL, DocumentType.LAB_REPORT, DocumentType.DIAGNOSTIC_REPORT, DocumentType.DISCHARGE_SUMMARY, DocumentType.DENTAL_REPORT):
            patient_name = content.get("patient_name")
            doctor_name = content.get("doctor_name") or content.get("referring_doctor")
            doctor_reg = content.get("doctor_registration")
            date_str = content.get("date") or content.get("report_date")
            diagnosis = content.get("diagnosis")
            medicines = content.get("medicines", [])
            tests_ordered = content.get("tests_ordered", [])
            treatment = content.get("treatment") or content.get("test_name")
            hospital_name = content.get("hospital_name") or content.get("lab_name")

            # Line items
            for li in content.get("line_items", []):
                line_items.append(
                    LineItem(
                        description=li.get("description", "Unknown"),
                        amount=float(li.get("amount", 0)),
                        quantity=li.get("quantity", 1),
                    )
                )

            # Total
            if "total" in content:
                total_amount = float(content["total"])
            elif line_items:
                total_amount = sum(li.amount for li in line_items)

        else:
            # Generic — just dump what we can
            patient_name = content.get("patient_name")
            doctor_name = content.get("doctor_name")
            doctor_reg = content.get("doctor_registration")
            date_str = content.get("date")
            diagnosis = content.get("diagnosis")
            medicines = content.get("medicines", [])
            tests_ordered = content.get("tests_ordered", [])
            treatment = content.get("treatment")

        hospital_name = content.get("hospital_name") or content.get("lab_name") or content.get("pharmacy_name")

        # Per-field confidence — in test mode, everything is 1.0
        # In production mode, this would be per-field LLM confidence
        for field_name in ["patient_name", "doctor_name", "doctor_registration", "date", "diagnosis", "total_amount"]:
            field_confidences[field_name] = 1.0

        return ExtractedDocument(
            file_id=doc.file_id,
            document_type=doc_type,
            patient_name=patient_name,
            doctor_name=doctor_name,
            doctor_registration=doctor_reg,
            date=date_str,
            diagnosis=diagnosis,
            medicines=medicines or [],
            tests_ordered=tests_ordered or [],
            treatment=treatment,
            hospital_name=hospital_name,
            line_items=line_items,
            total_amount=total_amount,
            is_readable=True,
            extraction_confidence=1.0,  # test mode is deterministic
            warnings=warnings,
            field_confidences=field_confidences,
        )


# ----------------------------------------------------------------------
# LangGraph node wrapper
# ----------------------------------------------------------------------

def make_document_extraction_node(policy: Policy):
    """Create the LangGraph node function for DocumentExtraction."""
    agent = DocumentExtractionAgent(policy)

    def document_extraction_node(state: AgentState) -> dict:
        # Check for failure simulation (TC011)
        from agents.core.enums import ComponentFailure
        sim = state.get("simulate_component_failure")
        if sim == ComponentFailure.DOCUMENT_EXTRACTION:
            started = datetime.now(timezone.utc)
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.DOCUMENT_EXTRACTION,
                status=AgentStatus.FAILED,
                started_at=started,
                completed_at=completed,
                duration_ms=0.0,
                confidence_contribution=0.0,
                error="Simulated component failure (TC011)",
                notes=["Failure injection — graceful degradation path"],
            )
            return {
                "extracted_documents": [],
                "trace": [trace],
                "errors": state.get("errors", []) + ["DocumentExtraction: simulated failure"],
            }

        started = datetime.now(timezone.utc)
        try:
            result = agent.run(state)
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.DOCUMENT_EXTRACTION,
                status=AgentStatus.SUCCESS,
                started_at=started,
                completed_at=completed,
                duration_ms=result.total_extraction_time_ms,
                confidence_contribution=result.average_confidence,
                input_summary={"num_documents": len(state["claim_input"].documents)},
                output_summary={
                    "num_extracted": len(result.extracted_documents),
                    "num_skipped": len(result.documents_skipped),
                    "llm_calls": result.llm_calls_made,
                    "avg_confidence": result.average_confidence,
                },
                notes=[f"extracted: {len(result.extracted_documents)} docs", f"skipped: {len(result.documents_skipped)}"],
            )
            return {
                "extracted_documents": [d.model_dump(mode="json") for d in result.extracted_documents],
                "trace": [trace],
            }
        except Exception as e:
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.DOCUMENT_EXTRACTION,
                status=AgentStatus.FAILED,
                started_at=started,
                completed_at=completed,
                duration_ms=0.0,
                confidence_contribution=0.0,
                error=str(e),
            )
            return {
                "extracted_documents": [],
                "trace": [trace],
                "errors": state.get("errors", []) + [f"DocumentExtraction failed: {e}"],
            }

    return document_extraction_node
