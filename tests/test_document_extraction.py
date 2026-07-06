"""
Tests for DocumentExtractionAgent.

Covered test cases:
  TC004 — clean consultation: prescription + hospital bill with line items
  TC005 — diabetes diagnosis extracted (for waiting period check downstream)
  TC006 — dental line items (root canal vs whitening)
  TC007 — MRI test name extracted
  TC011 — graceful failure (failure simulation)
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
    ComponentFailure,
    DocumentQuality,
    DocumentType,
)
from agents.core.policy_loader import get_policy  # noqa: E402
from agents.core.state import make_initial_state  # noqa: E402
from agents.document_extraction.agent import DocumentExtractionAgent  # noqa: E402


@pytest.fixture
def policy():
    return get_policy()


@pytest.fixture
def agent(policy):
    return DocumentExtractionAgent(policy)


def make_claim(documents, category=ClaimCategory.CONSULTATION, member_id="EMP001"):
    return ClaimInput(
        member_id=member_id,
        policy_id="PLUM_GHI_2024",
        claim_category=category,
        treatment_date=date(2024, 11, 1),
        claimed_amount=1500.0,
        documents=documents,
    )


# ----------------------------------------------------------------------
# TC004 — Clean consultation
# ----------------------------------------------------------------------

def test_tc004_extract_prescription_and_bill(agent):
    """TC004: Extract prescription (Viral Fever) + hospital bill (3 line items)."""
    claim = make_claim([
        DocumentInput(
            file_id="F007",
            actual_type=DocumentType.PRESCRIPTION,
            content={
                "doctor_name": "Dr. Arun Sharma",
                "doctor_registration": "KA/45678/2015",
                "patient_name": "Rajesh Kumar",
                "date": "2024-11-01",
                "diagnosis": "Viral Fever",
                "medicines": ["Paracetamol 650mg", "Vitamin C 500mg"],
            },
        ),
        DocumentInput(
            file_id="F008",
            actual_type=DocumentType.HOSPITAL_BILL,
            content={
                "hospital_name": "City Clinic, Bengaluru",
                "patient_name": "Rajesh Kumar",
                "date": "2024-11-01",
                "line_items": [
                    {"description": "Consultation Fee", "amount": 1000},
                    {"description": "CBC Test", "amount": 300},
                    {"description": "Dengue NS1 Test", "amount": 200},
                ],
                "total": 1500,
            },
        ),
    ])
    state = make_initial_state(claim)
    result = agent.run(state)

    assert len(result.extracted_documents) == 2
    assert result.documents_skipped == []
    assert result.llm_calls_made == 0
    assert result.average_confidence == 1.0

    # Prescription checks
    rx = result.extracted_documents[0]
    assert rx.diagnosis == "Viral Fever"
    assert rx.doctor_name == "Dr. Arun Sharma"
    assert rx.medicines == ["Paracetamol 650mg", "Vitamin C 500mg"]
    assert rx.patient_name == "Rajesh Kumar"

    # Bill checks
    bill = result.extracted_documents[1]
    assert bill.hospital_name == "City Clinic, Bengaluru"
    assert bill.total_amount == 1500
    assert len(bill.line_items) == 3
    assert bill.line_items[0].amount == 1000
    assert bill.line_items[1].description == "CBC Test"
    assert bill.line_items[2].amount == 200


# ----------------------------------------------------------------------
# TC005 — Diabetes diagnosis (for waiting period)
# ----------------------------------------------------------------------

def test_tc005_extract_diabetes(agent):
    """TC005: Extract diabetes diagnosis from prescription."""
    claim = make_claim([
        DocumentInput(
            file_id="F009",
            actual_type=DocumentType.PRESCRIPTION,
            content={
                "doctor_name": "Dr. Sunil Mehta",
                "doctor_registration": "GJ/56789/2014",
                "patient_name": "Vikram Joshi",
                "diagnosis": "Type 2 Diabetes Mellitus",
                "medicines": ["Metformin 500mg", "Glimepiride 1mg"],
            },
        ),
    ], member_id="EMP005")
    state = make_initial_state(claim)
    result = agent.run(state)

    assert len(result.extracted_documents) == 1
    doc = result.extracted_documents[0]
    assert doc.diagnosis == "Type 2 Diabetes Mellitus"
    assert doc.medicines == ["Metformin 500mg", "Glimepiride 1mg"]


# ----------------------------------------------------------------------
# TC006 — Dental line items
# ----------------------------------------------------------------------

def test_tc006_extract_dental(agent):
    """TC006: Extract dental line items (root canal + whitening)."""
    claim = make_claim([
        DocumentInput(
            file_id="F011",
            actual_type=DocumentType.HOSPITAL_BILL,
            content={
                "hospital_name": "Smile Dental Clinic",
                "patient_name": "Priya Singh",
                "line_items": [
                    {"description": "Root Canal Treatment", "amount": 8000},
                    {"description": "Teeth Whitening", "amount": 4000},
                ],
                "total": 12000,
            },
        ),
    ], category=ClaimCategory.DENTAL, member_id="EMP002")
    state = make_initial_state(claim)
    result = agent.run(state)

    bill = result.extracted_documents[0]
    assert bill.total_amount == 12000
    assert len(bill.line_items) == 2
    assert bill.line_items[0].description == "Root Canal Treatment"
    assert bill.line_items[0].amount == 8000
    assert bill.line_items[1].description == "Teeth Whitening"
    assert bill.line_items[1].amount == 4000


# ----------------------------------------------------------------------
# TC007 — MRI test name
# ----------------------------------------------------------------------

def test_tc007_extract_mri(agent):
    """TC007: Extract MRI test name (for pre-auth check downstream)."""
    claim = make_claim([
        DocumentInput(
            file_id="F012",
            actual_type=DocumentType.PRESCRIPTION,
            content={
                "doctor_name": "Dr. Venkat Rao",
                "doctor_registration": "AP/67890/2017",
                "diagnosis": "Suspected Lumbar Disc Herniation",
                "tests_ordered": ["MRI Lumbar Spine"],
            },
        ),
    ], category=ClaimCategory.DIAGNOSTIC, member_id="EMP007")
    state = make_initial_state(claim)
    result = agent.run(state)

    rx = result.extracted_documents[0]
    assert rx.tests_ordered == ["MRI Lumbar Spine"]
    assert rx.diagnosis == "Suspected Lumbar Disc Herniation"


# ----------------------------------------------------------------------
# Unreadable doc — skipped
# ----------------------------------------------------------------------

def test_unreadable_doc_skipped(agent):
    """Unreadable docs are skipped and surfaced in documents_skipped."""
    claim = make_claim([
        DocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION,
                      quality=DocumentQuality.GOOD, content={"patient_name": "X"}),
        DocumentInput(file_id="F002", actual_type=DocumentType.HOSPITAL_BILL,
                      quality=DocumentQuality.UNREADABLE),
    ])
    state = make_initial_state(claim)
    result = agent.run(state)

    assert "F002" in result.documents_skipped
    assert len(result.extracted_documents) == 2
    # The unreadable one should have is_readable=False
    unreadable_extracted = [d for d in result.extracted_documents if d.file_id == "F002"][0]
    assert unreadable_extracted.is_readable is False
    assert unreadable_extracted.extraction_confidence == 0.0


# ----------------------------------------------------------------------
# TC011 — Graceful failure
# ----------------------------------------------------------------------

def test_tc011_graceful_failure(agent):
    """When simulate_component_failure=DOCUMENT_EXTRACTION, the agent fails gracefully."""
    claim = make_claim([
        DocumentInput(file_id="F021", actual_type=DocumentType.PRESCRIPTION,
                      content={"diagnosis": "Chronic Joint Pain"}),
        DocumentInput(file_id="F022", actual_type=DocumentType.HOSPITAL_BILL,
                      content={"total": 4000, "line_items": []}),
    ], category=ClaimCategory.ALTERNATIVE_MEDICINE, member_id="EMP006")
    # Override the simulate flag
    claim = claim.model_copy(update={"simulate_component_failure": ComponentFailure.DOCUMENT_EXTRACTION})

    state = make_initial_state(claim)
    # Manually trigger the failure path via the node
    from agents.document_extraction.agent import make_document_extraction_node
    node = make_document_extraction_node(get_policy())
    update = node(state)

    assert update["extracted_documents"] == []
    assert len(update["trace"]) == 1
    trace = update["trace"][0]
    assert trace.status.value == "FAILED"
    assert "simulated" in (trace.error or "").lower() or "simulated" in str(trace.notes).lower()


# ----------------------------------------------------------------------
# Run all tests
# ----------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("DocumentExtraction Agent Tests")
    print("=" * 60)
    pytest.main([__file__, "-v", "--tb=short"])
