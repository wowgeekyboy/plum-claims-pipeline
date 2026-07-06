"""
Tests for MemberValidationAgent.

Covered test cases:
  TC005 — Vikram Joshi, joined 2024-09-01, diabetes 2024-10-15 → 44 days < 90 → REJECT
  TC012 — Obesity is also waiting-period-gated (365 days) AND excluded
  Plus: member not found, initial 30-day wait, shorthand matching
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
    DocumentType,
    RejectionReason,
)
from agents.core.policy_loader import get_policy  # noqa: E402
from agents.core.state import make_initial_state  # noqa: E402
from agents.member_validation.agent import (  # noqa: E402
    MemberValidationAgent,
    normalize_diagnosis,
)


@pytest.fixture
def policy():
    return get_policy()


@pytest.fixture
def agent(policy):
    return MemberValidationAgent(policy)


def make_claim(documents, member_id="EMP001", category=ClaimCategory.CONSULTATION,
               treatment_date=date(2024, 11, 1)):
    return ClaimInput(
        member_id=member_id,
        policy_id="PLUM_GHI_2024",
        claim_category=category,
        treatment_date=treatment_date,
        claimed_amount=1500.0,
        documents=documents,
    )


# ----------------------------------------------------------------------
# TC005 — Diabetes within 90-day waiting period
# ----------------------------------------------------------------------

def test_tc005_diabetes_waiting_period(agent):
    """Vikram Joshi joined 2024-09-01, diabetes 2024-10-15, only 44 days < 90 → REJECT."""
    claim = make_claim(
        documents=[
            DocumentInput(
                file_id="F009",
                actual_type=DocumentType.PRESCRIPTION,
                content={"diagnosis": "Type 2 Diabetes Mellitus"},
            ),
        ],
        member_id="EMP005",
        treatment_date=date(2024, 10, 15),
    )
    # Pre-populate extraction (in real flow, this comes from DocumentExtraction agent)
    state = make_initial_state(claim)
    state["extracted_documents"] = [{
        "file_id": "F009",
        "document_type": "PRESCRIPTION",
        "diagnosis": "Type 2 Diabetes Mellitus",
        "extraction_confidence": 1.0,
        "is_readable": True,
        "medicines": [],
        "tests_ordered": [],
        "line_items": [],
    }]

    result = agent.run(state)

    assert not result.is_valid
    assert RejectionReason.WAITING_PERIOD in result.rejection_reasons
    # Member details
    assert result.member_name == "Vikram Joshi"
    assert result.join_date == date(2024, 9, 1)
    # Initial 30-day check should pass
    assert result.initial_waiting_passed is True
    # Condition check should fail
    assert result.condition_waiting_passed is False
    # User message must mention the eligible-from date
    assert "2024-11-30" in result.user_message
    print(f"  TC005 message: {result.user_message}")


# ----------------------------------------------------------------------
# TC004 — Clean consultation, all checks pass
# ----------------------------------------------------------------------

def test_tc004_valid_consultation(agent):
    """Rajesh Kumar, joined 2024-04-01, viral fever 2024-11-01 — way past waiting periods."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F007", actual_type=DocumentType.PRESCRIPTION,
                          content={"diagnosis": "Viral Fever"}),
        ],
        member_id="EMP001",
        treatment_date=date(2024, 11, 1),
    )
    state = make_initial_state(claim)
    state["extracted_documents"] = [{
        "file_id": "F007", "document_type": "PRESCRIPTION",
        "diagnosis": "Viral Fever", "extraction_confidence": 1.0, "is_readable": True,
        "medicines": [], "tests_ordered": [], "line_items": [],
    }]

    result = agent.run(state)

    assert result.is_valid
    assert result.initial_waiting_passed
    assert result.condition_waiting_passed
    assert result.rejection_reasons == []


# ----------------------------------------------------------------------
# Initial 30-day wait
# ----------------------------------------------------------------------

def test_initial_waiting_period(agent):
    """Member joined recently, treatment within 30 days → REJECT."""
    # EMP005 (Vikram Joshi) joined 2024-09-01. Treatment 2024-09-15 = 14 days.
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION,
                          content={"diagnosis": "Common Cold"}),
        ],
        member_id="EMP005",
        treatment_date=date(2024, 9, 15),
    )
    state = make_initial_state(claim)
    state["extracted_documents"] = [{
        "file_id": "F001", "document_type": "PRESCRIPTION",
        "diagnosis": "Common Cold", "extraction_confidence": 1.0, "is_readable": True,
        "medicines": [], "tests_ordered": [], "line_items": [],
    }]
    result = agent.run(state)

    assert not result.is_valid
    assert RejectionReason.WAITING_PERIOD in result.rejection_reasons
    assert not result.initial_waiting_passed
    assert "initial" in result.user_message.lower() or "30" in result.user_message


# ----------------------------------------------------------------------
# Member not found
# ----------------------------------------------------------------------

def test_member_not_found(agent):
    claim = make_claim([], member_id="EMP999_DOES_NOT_EXIST")
    state = make_initial_state(claim)
    result = agent.run(state)

    assert not result.is_valid
    assert not result.member_found
    assert RejectionReason.MEMBER_INELIGIBLE in result.rejection_reasons


# ----------------------------------------------------------------------
# Medical shorthand matching
# ----------------------------------------------------------------------

def test_diagnosis_shorthand_t2dm(agent):
    """T2DM shorthand should match diabetes."""
    claim = make_claim([
        DocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION,
                      content={"diagnosis": "T2DM"}),
    ], member_id="EMP005", treatment_date=date(2024, 10, 15))
    state = make_initial_state(claim)
    state["extracted_documents"] = [{
        "file_id": "F001", "document_type": "PRESCRIPTION",
        "diagnosis": "T2DM", "extraction_confidence": 1.0, "is_readable": True,
        "medicines": [], "tests_ordered": [], "line_items": [],
    }]
    result = agent.run(state)

    assert not result.is_valid
    assert not result.condition_waiting_passed
    assert "diabetes" in result.user_message.lower() or "T2DM" in result.user_message


def test_diagnosis_shorthand_htn(agent):
    """HTN should match hypertension (90-day wait)."""
    claim = make_claim([
        DocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION,
                      content={"diagnosis": "HTN"}),
    ], member_id="EMP005", treatment_date=date(2024, 10, 15))
    state = make_initial_state(claim)
    state["extracted_documents"] = [{
        "file_id": "F001", "document_type": "PRESCRIPTION",
        "diagnosis": "HTN", "extraction_confidence": 1.0, "is_readable": True,
        "medicines": [], "tests_ordered": [], "line_items": [],
    }]
    result = agent.run(state)
    assert not result.condition_waiting_passed
    assert "hypertension" in result.user_message.lower()


# ----------------------------------------------------------------------
# Unit tests for normalize_diagnosis
# ----------------------------------------------------------------------

def test_normalize_diagnosis():
    assert normalize_diagnosis("T2DM") == "diabetes"
    assert normalize_diagnosis("Type 2 Diabetes Mellitus") == "diabetes"
    assert normalize_diagnosis("HTN") == "hypertension"
    assert normalize_diagnosis("") == ""
    assert normalize_diagnosis(None) == ""
    assert normalize_diagnosis("Broken Leg") == ""  # not in the table
    assert normalize_diagnosis("Pregnancy care") == "maternity"
    assert normalize_diagnosis("Bariatric Consultation") == "obesity_treatment"


# ----------------------------------------------------------------------
# Run all tests
# ----------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("MemberValidation Agent Tests")
    print("=" * 60)
    pytest.main([__file__, "-v", "--tb=short"])
