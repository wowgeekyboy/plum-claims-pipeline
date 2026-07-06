"""
Tests for DocumentVerificationAgent.

Test cases covered:
  TC001 — wrong doc type (2 prescriptions for consultation)
  TC002 — unreadable document (blurry photo)
  TC003 — patient name mismatch
  Plus: clean approval, missing required doc, low quality warning
"""

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.core.domain import ClaimInput, DocumentInput  # noqa: E402
from agents.core.enums import (  # noqa: E402
    ClaimCategory,
    DocumentError,
    DocumentQuality,
    DocumentType,
)
from agents.core.policy_loader import get_policy  # noqa: E402
from agents.core.state import make_initial_state  # noqa: E402
from agents.document_verification.agent import DocumentVerificationAgent  # noqa: E402


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def policy():
    return get_policy()


@pytest.fixture
def agent(policy):
    return DocumentVerificationAgent(policy)


def make_claim(
    documents: list[DocumentInput],
    member_id: str = "EMP001",
    category: ClaimCategory = ClaimCategory.CONSULTATION,
    claimed_amount: float = 1500.0,
) -> ClaimInput:
    """Helper to build a ClaimInput for testing."""
    return ClaimInput(
        member_id=member_id,
        policy_id="PLUM_GHI_2024",
        claim_category=category,
        treatment_date=date(2024, 11, 1),
        claimed_amount=claimed_amount,
        documents=documents,
    )


# ----------------------------------------------------------------------
# TC001 — Wrong document type
# ----------------------------------------------------------------------

def test_tc001_wrong_document_type(agent):
    """Two prescriptions for a consultation claim — should fail with WRONG_DOCUMENT_TYPE."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F001", file_name="dr_sharma_prescription.jpg",
                          actual_type=DocumentType.PRESCRIPTION, quality=DocumentQuality.GOOD),
            DocumentInput(file_id="F002", file_name="another_prescription.jpg",
                          actual_type=DocumentType.PRESCRIPTION, quality=DocumentQuality.GOOD),
        ]
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert not result.is_valid, "Should be invalid"
    assert result.stop_processing, "Should stop the pipeline"
    assert DocumentError.WRONG_DOCUMENT_TYPE in result.errors
    # User message must be specific
    msg = result.user_message.lower()
    assert "prescription" in msg
    assert "hospital bill" in msg
    print(f"  TC001 message: {result.user_message}")


# ----------------------------------------------------------------------
# TC002 — Unreadable document
# ----------------------------------------------------------------------

def test_tc002_unreadable_document(agent):
    """Pharmacy bill is unreadable. Should ask for re-upload, NOT reject."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F003", file_name="prescription.jpg",
                          actual_type=DocumentType.PRESCRIPTION, quality=DocumentQuality.GOOD),
            DocumentInput(file_id="F004", file_name="blurry_bill.jpg",
                          actual_type=DocumentType.PHARMACY_BILL, quality=DocumentQuality.UNREADABLE),
        ],
        category=ClaimCategory.PHARMACY,
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert not result.is_valid
    assert DocumentError.UNREADABLE_DOCUMENT in result.errors
    msg = result.user_message.lower()
    assert "blurry_bill" in msg or "re-upload" in msg
    # Must NOT use a generic "rejected" message
    assert "rejected" not in msg or "not rejected" in msg  # allow if phrasing is clear
    print(f"  TC002 message: {result.user_message}")


# ----------------------------------------------------------------------
# TC003 — Patient mismatch
# ----------------------------------------------------------------------

def test_tc003_patient_mismatch(agent):
    """Prescription for Rajesh Kumar, bill for Arjun Mehta."""
    claim = make_claim(
        documents=[
            DocumentInput(
                file_id="F005", file_name="prescription_rajesh.jpg",
                actual_type=DocumentType.PRESCRIPTION,
                patient_name_on_doc="Rajesh Kumar",
            ),
            DocumentInput(
                file_id="F006", file_name="bill_arjun.jpg",
                actual_type=DocumentType.HOSPITAL_BILL,
                patient_name_on_doc="Arjun Mehta",
            ),
        ],
        member_id="EMP001",  # Rajesh Kumar
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert not result.is_valid
    assert DocumentError.PATIENT_NAME_MISMATCH in result.errors
    msg = result.user_message
    assert "Rajesh Kumar" in msg
    assert "Arjun Mehta" in msg
    print(f"  TC003 message: {result.user_message}")


def test_tc003_patient_mismatch_via_content(agent):
    """Same as TC003 but names come from .content, not .patient_name_on_doc."""
    claim = make_claim(
        documents=[
            DocumentInput(
                file_id="F005", actual_type=DocumentType.PRESCRIPTION,
                content={"patient_name": "Rajesh Kumar"},
            ),
            DocumentInput(
                file_id="F006", actual_type=DocumentType.HOSPITAL_BILL,
                content={"patient_name": "Arjun Mehta"},
            ),
        ],
        member_id="EMP001",
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert not result.is_valid
    assert DocumentError.PATIENT_NAME_MISMATCH in result.errors
    assert "Arjun Mehta" in result.user_message


# ----------------------------------------------------------------------
# Happy path — TC004
# ----------------------------------------------------------------------

def test_tc004_clean_consultation(agent):
    """Prescription + hospital bill, all good."""
    claim = make_claim(
        documents=[
            DocumentInput(
                file_id="F007", actual_type=DocumentType.PRESCRIPTION,
                content={"patient_name": "Rajesh Kumar"},
            ),
            DocumentInput(
                file_id="F008", actual_type=DocumentType.HOSPITAL_BILL,
                content={"patient_name": "Rajesh Kumar", "total": 1500},
            ),
        ],
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert result.is_valid, f"Should be valid, got: {result.user_message}"
    assert not result.stop_processing
    assert result.user_message == ""
    assert DocumentType.PRESCRIPTION in result.documents_present
    assert DocumentType.HOSPITAL_BILL in result.documents_present
    assert not result.documents_missing
    assert result.confidence == 1.0


# ----------------------------------------------------------------------
# Missing required doc
# ----------------------------------------------------------------------

def test_missing_required_document(agent):
    """Only prescription, no hospital bill for consultation."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION),
        ],
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert not result.is_valid
    assert DocumentError.MISSING_REQUIRED_DOCUMENT in result.errors
    assert "hospital bill" in result.user_message.lower()
    assert DocumentType.HOSPITAL_BILL in result.documents_missing


# ----------------------------------------------------------------------
# Low quality — warning only, not a stop
# ----------------------------------------------------------------------

def test_low_quality_warning_only(agent):
    """POOR quality should be a warning, not a stop."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION),
            DocumentInput(file_id="F002", actual_type=DocumentType.HOSPITAL_BILL,
                          quality=DocumentQuality.POOR),
        ],
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    # Should be valid (POOR is a warning, not an error)
    assert result.is_valid
    assert any("low quality" in w.lower() for w in result.warnings)


# ----------------------------------------------------------------------
# DENTAL — only hospital bill required
# ----------------------------------------------------------------------

def test_dental_just_bill(agent):
    """DENTAL category only requires HOSPITAL_BILL — prescription should be optional."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F011", actual_type=DocumentType.HOSPITAL_BILL),
        ],
        category=ClaimCategory.DENTAL,
        member_id="EMP002",  # Priya Singh
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert result.is_valid, f"Got: {result.user_message}"


# ----------------------------------------------------------------------
# Run all tests
# ----------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("DocumentVerification Agent Tests")
    print("=" * 60)
    pytest.main([__file__, "-v", "--tb=short"])
