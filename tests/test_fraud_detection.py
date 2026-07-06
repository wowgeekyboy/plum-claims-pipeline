"""
Tests for FraudDetectionAgent.

Covered test cases:
  TC009 — 4 same-day claims > 2 limit → MANUAL_REVIEW
  Plus: high-value claim, monthly limit, no fraud baseline
"""

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.core.domain import ClaimHistoryItem, ClaimInput, DocumentInput  # noqa: E402
from agents.core.enums import (  # noqa: E402
    ClaimCategory,
    DocumentType,
    FraudSignal,
)
from agents.core.policy_loader import get_policy  # noqa: E402
from agents.core.state import make_initial_state  # noqa: E402
from agents.fraud_detection.agent import FraudDetectionAgent  # noqa: E402


@pytest.fixture
def policy():
    return get_policy()


@pytest.fixture
def agent(policy):
    return FraudDetectionAgent(policy)


def make_claim(documents, member_id="EMP001", category=ClaimCategory.CONSULTATION,
               claimed_amount=1500.0, treatment_date=date(2024, 10, 30),
               history=None):
    return ClaimInput(
        member_id=member_id,
        policy_id="PLUM_GHI_2024",
        claim_category=category,
        treatment_date=treatment_date,
        claimed_amount=claimed_amount,
        documents=documents,
        claims_history=history or [],
    )


# ----------------------------------------------------------------------
# TC009 — Same-day limit
# ----------------------------------------------------------------------

def test_tc009_same_day_limit(agent):
    """EMP008 has 3 prior claims on 2024-10-30 + this one = 4 > 2 → MANUAL_REVIEW."""
    history = [
        ClaimHistoryItem(claim_id="CLM_0081", date=date(2024, 10, 30), amount=1200, provider="City Clinic A"),
        ClaimHistoryItem(claim_id="CLM_0082", date=date(2024, 10, 30), amount=1800, provider="City Clinic B"),
        ClaimHistoryItem(claim_id="CLM_0083", date=date(2024, 10, 30), amount=2100, provider="Wellness Center"),
    ]
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F017", actual_type=DocumentType.PRESCRIPTION,
                          content={"diagnosis": "Migraine", "doctor_name": "Dr. S. Khan"}),
            DocumentInput(file_id="F018", actual_type=DocumentType.HOSPITAL_BILL,
                          content={"total": 4800}),
        ],
        member_id="EMP008",
        category=ClaimCategory.CONSULTATION,
        claimed_amount=4800,
        treatment_date=date(2024, 10, 30),
        history=history,
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert result.same_day_claims_count == 4
    assert FraudSignal.SAME_DAY_LIMIT_EXCEEDED in result.signals_triggered
    assert result.requires_manual_review
    assert result.fraud_score > 0
    print(f"  TC009 signals: {[s.value for s in result.signals_triggered]}, score: {result.fraud_score}")


# ----------------------------------------------------------------------
# No fraud baseline
# ----------------------------------------------------------------------

def test_no_fraud_baseline(agent):
    """A single consultation with no history → no signals."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION,
                          content={"diagnosis": "Viral Fever"}),
        ],
        member_id="EMP001",
        claimed_amount=1500,
        treatment_date=date(2024, 11, 1),
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert result.signals_triggered == []
    assert not result.requires_manual_review
    assert result.fraud_score == 0.0
    assert result.same_day_claims_count == 1


# ----------------------------------------------------------------------
# High-value claim
# ----------------------------------------------------------------------

def test_high_value_claim_flagged(agent):
    """Claim > ₹25,000 → HIGH_VALUE_CLAIM signal, manual review."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION,
                          content={"diagnosis": "Major Surgery"}),
        ],
        member_id="EMP001",
        claimed_amount=30000,
        treatment_date=date(2024, 11, 1),
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert FraudSignal.HIGH_VALUE_CLAIM in result.signals_triggered
    assert result.is_high_value
    assert result.requires_manual_review  # > auto_manual_review_above


# ----------------------------------------------------------------------
# Monthly limit
# ----------------------------------------------------------------------

def test_monthly_limit(agent):
    """7 claims in the same month > 6 limit → MONTHLY_LIMIT_EXCEEDED."""
    history = [
        ClaimHistoryItem(claim_id=f"CLM_M{i}", date=date(2024, 10, d), amount=1000)
        for i, d in enumerate([1, 5, 10, 15, 20, 25], start=1)
    ]
    claim = make_claim(
        documents=[DocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION)],
        member_id="EMP001",
        claimed_amount=1500,
        treatment_date=date(2024, 10, 30),
        history=history,
    )
    state = make_initial_state(claim)
    result = agent.run(state)

    assert result.monthly_claims_count == 7
    assert FraudSignal.MONTHLY_LIMIT_EXCEEDED in result.signals_triggered


# ----------------------------------------------------------------------
# Document alterations
# ----------------------------------------------------------------------

def test_document_alteration_signal(agent):
    """If extraction flagged an alteration warning, trigger DOCUMENT_ALTERATION."""
    claim = make_claim(
        documents=[DocumentInput(file_id="F001", actual_type=DocumentType.PRESCRIPTION)],
        member_id="EMP001",
        claimed_amount=1500,
        treatment_date=date(2024, 11, 1),
    )
    state = make_initial_state(claim)
    # Simulate extraction warning about alteration
    state["extracted_documents"] = [{
        "file_id": "F001", "document_type": "PRESCRIPTION",
        "warnings": ["Cancellation marks detected on amount"],
        "is_readable": True, "extraction_confidence": 0.7,
    }]
    result = agent.run(state)

    assert FraudSignal.DOCUMENT_ALTERATION in result.signals_triggered


# ----------------------------------------------------------------------
# Run all tests
# ----------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("FraudDetection Agent Tests")
    print("=" * 60)
    pytest.main([__file__, "-v", "--tb=short"])
