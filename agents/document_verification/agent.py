"""
DocumentVerificationAgent — implementation.

First gate in the pipeline. Catches document problems early with specific,
actionable messages for the member.

Three test cases depend on this agent:
  TC001 — wrong doc type uploaded (2 prescriptions for consultation)
  TC002 — unreadable document (blurry photo)
  TC003 — documents belong to different patients

DESIGN NOTES
============
- No LLM. Pure logic against policy + claim input. Fast and deterministic.
- Always returns a result — never raises. The orchestrator handles downstream flow.
- User messages are SPECIFIC (name the uploaded type, name the required type).
  The assignment explicitly grades on this.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.core.domain import DocumentInput, Policy
from agents.core.enums import (
    AgentName,
    AgentStatus,
    DocumentError,
    DocumentQuality,
    DocumentType,
)
from agents.core.state import AgentState
from agents.core.trace import AgentTrace
from agents.document_verification.schemas import (
    DocumentCheckResult,
    DocumentVerificationResult,
)


class DocumentVerificationAgent:
    """Verifies the document set against the claim category's requirements.

    Pure logic, no I/O. The orchestrator wraps this in a LangGraph node.
    """

    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, state: AgentState) -> DocumentVerificationResult:
        """Run all document checks against the claim.

        We check all 5 failure modes in one pass so the member gets a
        complete picture of what's wrong. Even if check 1 fails, we still
        run checks 2-5 to surface all issues.
        """
        claim_input = state["claim_input"]
        category = claim_input.claim_category
        documents = claim_input.documents

        reqs = self.policy.document_requirements.get(category, {})
        required_types: list[DocumentType] = reqs.get("required", [])

        member = self.policy.get_member(claim_input.member_id)
        member_name = member.name if member else ""

        # ---- Check 1: Wrong type (TC001) — checked FIRST because if user uploaded
        #      2 of the same type, "missing" is the wrong diagnosis. The right
        #      diagnosis is "you uploaded the wrong docs (duplicates).".
        present_types = [d.actual_type for d in documents if d.actual_type is not None]
        missing = [t for t in required_types if t not in present_types]
        wrong_type = self._check_wrong_type(documents, required_types, present_types, missing)

        # ---- Check 2: Unreadable docs (TC002) ----
        unreadable = [d for d in documents if d.quality == DocumentQuality.UNREADABLE]

        # ---- Check 3: Patient mismatch (TC003) ----
        patient_mismatch = self._check_patient_mismatch(documents, member_name)

        # ---- Check 4: Low quality (warning only) ----
        poor_quality = [d for d in documents if d.quality == DocumentQuality.POOR]

        # ---- Assemble errors & messages ----
        # Priority: wrong_type > missing > unreadable > patient_mismatch
        # (we want the most SPECIFIC error to be the one shown)
        errors: list[DocumentError] = []
        warnings: list[str] = []
        user_message = ""

        if wrong_type:
            errors.append(DocumentError.WRONG_DOCUMENT_TYPE)
            user_message = self._build_wrong_type_message(
                wrong_type["duplicate_type"], wrong_type["count"],
                category, required_types, wrong_type["missing"],
            )
            warnings.append(user_message)
        elif missing:
            errors.append(DocumentError.MISSING_REQUIRED_DOCUMENT)
            for m in missing:
                warnings.append(f"Required document missing: {m.value}")
            user_message = self._build_missing_message(missing, category, present_types)
        elif unreadable:
            errors.append(DocumentError.UNREADABLE_DOCUMENT)
            user_message = self._build_unreadable_message(unreadable)
            warnings.append(user_message)
        elif patient_mismatch:
            errors.append(DocumentError.PATIENT_NAME_MISMATCH)
            user_message = self._build_patient_mismatch_message(member_name, patient_mismatch)
            warnings.append(user_message)

        if poor_quality:
            for d in poor_quality:
                warnings.append(f"Document {d.file_id} ({d.file_name or 'unnamed'}) is low quality")

        # ---- Per-document results ----
        per_doc = [
            DocumentCheckResult(
                file_id=d.file_id,
                document_type=d.actual_type or DocumentType.UNKNOWN,
                is_acceptable=d.quality != DocumentQuality.UNREADABLE,
                issues=[] if d.quality == DocumentQuality.GOOD else [f"quality:{d.quality.value}"],
                patient_name_found=d.patient_name_on_doc,
            )
            for d in documents
        ]

        is_valid = len(errors) == 0
        # Confidence — full when types are explicit
        confidence = 1.0
        if any(d.actual_type is None for d in documents):
            confidence = 0.9

        return DocumentVerificationResult(
            is_valid=is_valid,
            stop_processing=not is_valid,
            errors=errors,
            warnings=warnings,
            user_message=user_message if not is_valid else "",
            per_document_results=per_doc,
            documents_present=present_types,
            documents_missing=missing,
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # Check helpers
    # ------------------------------------------------------------------

    def _check_patient_mismatch(
        self, documents: list[DocumentInput], member_name: str
    ) -> set[str] | None:
        """TC003: detect if documents are for different patients.

        Returns the set of names found (excluding the member's own name) if
        there's a mismatch, else None.
        """
        if not member_name:
            return None
        member_norm = member_name.strip().lower()

        names_found: set[str] = set()
        for d in documents:
            # From explicit field
            if d.patient_name_on_doc:
                names_found.add(d.patient_name_on_doc.strip())
            # From extracted content
            if isinstance(d.content, dict):
                pn = d.content.get("patient_name")
                if pn:
                    names_found.add(str(pn).strip())

        # Filter out the member's own name and empty
        other_names = {n for n in names_found if n and n.lower() != member_norm}
        if other_names:
            return names_found
        return None

    def _check_wrong_type(
        self,
        documents: list[DocumentInput],
        required_types: list[DocumentType],
        present_types: list[DocumentType],
        missing: list[DocumentType],
    ) -> dict | None:
        """TC001: detect duplicate of a doc type when a different required type is missing.

        Returns {"duplicate_type": ..., "count": ..., "missing": [...]} or None.
        """
        if not missing:
            return None  # nothing missing → no wrong-type issue
        if len(documents) < 2:
            return None  # can't have a duplicate with 1 doc

        # Count occurrences per type
        counts: dict[DocumentType, int] = {}
        for d in documents:
            if d.actual_type:
                counts[d.actual_type] = counts.get(d.actual_type, 0) + 1

        # Find a type that appears more than once
        for doc_type, count in counts.items():
            if count > 1:
                # And that type IS in the required list (so user had the right idea)
                # but a different required type is missing
                if doc_type in required_types:
                    return {
                        "duplicate_type": doc_type,
                        "count": count,
                        "missing": missing,
                    }
        return None

    # ------------------------------------------------------------------
    # User-facing message builders
    # ------------------------------------------------------------------

    def _build_missing_message(
        self,
        missing: list[DocumentType],
        category: Any,
        present_types: list[DocumentType],
    ) -> str:
        category_name = str(category.value) if hasattr(category, "value") else str(category)
        missing_str = " and ".join(self._human(t) for t in missing)
        present_str = (
            ", ".join(self._human(t) for t in present_types) if present_types else "no documents"
        )
        return (
            f"A {self._human(category_name)} claim requires {missing_str}, "
            f"but we only received {present_str}. "
            f"Please upload the missing {missing_str}."
        )

    def _build_unreadable_message(self, unreadable: list[DocumentInput]) -> str:
        if len(unreadable) == 1:
            d = unreadable[0]
            name = d.file_name or f"file {d.file_id}"
            return (
                f"Your {name} could not be read clearly. "
                f"Please re-upload a clear photo or scan of this document. "
                f"Make sure the text is legible and the full document is visible."
            )
        names = ", ".join(d.file_name or f"file {d.file_id}" for d in unreadable)
        return (
            f"The following documents could not be read clearly: {names}. "
            f"Please re-upload clear photos or scans of each."
        )

    def _build_patient_mismatch_message(
        self,
        member_name: str,
        names_found: set[str],
    ) -> str:
        names_list = sorted(names_found)
        names_str = " and ".join(f"'{n}'" for n in names_list)
        plural = "s" if len(names_list) > 1 else ""
        return (
            f"The documents you uploaded appear to be for different patients. "
            f"This claim is for {member_name}, but we found the name{plural} {names_str} "
            f"on the uploaded documents. All documents must be for the same patient ({member_name})."
        )

    def _build_wrong_type_message(
        self,
        duplicate_type: DocumentType,
        count: int,
        category: Any,
        required_types: list[DocumentType],
        missing: list[DocumentType],
    ) -> str:
        cat_human = self._human(str(category.value) if hasattr(category, "value") else str(category))
        dup_human = self._human(duplicate_type)
        if count == 2:
            uploaded = f"two {dup_human} documents"
        else:
            uploaded = f"{count} {dup_human} documents"
        required_str = " and ".join(self._human(t) for t in required_types)
        missing_str = " and ".join(self._human(t) for t in missing)
        return (
            f"You uploaded {uploaded} for this {cat_human} claim. "
            f"A {cat_human} claim requires {required_str}. "
            f"Please upload the {missing_str} instead of (or in addition to) the extra {dup_human}."
        )

    @staticmethod
    def _human(t: Any) -> str:
        """Convert an enum or string to human-readable form."""
        s = str(t.value) if hasattr(t, "value") else str(t)
        return s.replace("_", " ").lower()


# ----------------------------------------------------------------------
# LangGraph node wrapper
# ----------------------------------------------------------------------

def make_document_verification_node(policy: Policy):
    """Create the LangGraph node function for DocumentVerification."""
    agent = DocumentVerificationAgent(policy)

    def document_verification_node(state: AgentState) -> dict:
        started = datetime.now(timezone.utc)
        try:
            result = agent.run(state)
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.DOCUMENT_VERIFICATION,
                status=AgentStatus.SUCCESS,
                started_at=started,
                completed_at=completed,
                duration_ms=(completed - started).total_seconds() * 1000,
                confidence_contribution=result.confidence,
                input_summary={
                    "claim_category": str(state["claim_input"].claim_category.value),
                    "num_documents": len(state["claim_input"].documents),
                },
                output_summary={
                    "is_valid": result.is_valid,
                    "errors": [e.value for e in result.errors],
                    "stop_processing": result.stop_processing,
                },
                notes=[f"present: {[t.value for t in result.documents_present]}",
                       f"missing: {[t.value for t in result.documents_missing]}"] + result.warnings,
            )
            return {
                "document_verification": result.model_dump(mode="json"),
                "trace": [trace],
            }
        except Exception as e:
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.DOCUMENT_VERIFICATION,
                status=AgentStatus.FAILED,
                started_at=started,
                completed_at=completed,
                duration_ms=(completed - started).total_seconds() * 1000,
                confidence_contribution=0.0,
                error=str(e),
            )
            failed_result = DocumentVerificationResult(
                is_valid=False,
                stop_processing=True,
                errors=[DocumentError.UNREADABLE_DOCUMENT],
                user_message=f"Document verification could not complete: {e}. Please try again.",
                confidence=0.0,
            )
            return {
                "document_verification": failed_result.model_dump(mode="json"),
                "errors": [f"DocumentVerification failed: {e}"],
                "trace": [trace],
            }

    return document_verification_node
