"""
Tests for DecisionAgent.

Tests the synthesis logic by feeding pre-populated state with various
upstream results and checking the final Decision.
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
    DecisionType,
    DocumentType,
    RejectionReason,
)
from agents.core.state import make_initial_state  # noqa: E402
from agents.decision.agent import DecisionAgent  # noqa: E402


@pytest.fixture
def agent():
    return DecisionAgent()


def make_state(documents=None, member_id="EMP001", claimed=1500.0, errors=None, category=ClaimCategory.CONSULTATION):
    claim = ClaimInput(
        member_id=member_id,
        policy_id="PLUM_GHI_2024",
        claim_category=category,
        treatment_date=date(2024, 11, 1),
        claimed_amount=claimed,
        documents=documents or [],
    )
    state = make_initial_state(claim)
    state["errors"] = errors or []
    return state


# ----------------------------------------------------------------------
# TC004 — APPROVED
# ----------------------------------------------------------------------

def test_tc004_approved(agent):
    """Consultation ₹1500 with 10% co-pay → APPROVED ₹1350."""
    state = make_state()
    state["document_verification"] = {"is_valid": True, "confidence": 1.0, "user_message": ""}
    state["member_validation"] = {"is_valid": True, "confidence": 1.0, "rejection_reasons": [], "user_message": ""}
    state["policy_evaluation"] = {
        "is_valid": True, "approved_amount": 1350, "claimed_amount": 1500,
        "rejection_reasons": [], "user_message": "",
        "copay_amount": 150, "network_discount_amount": 0, "line_item_decisions": [],
        "confidence": 0.95,
    }
    state["fraud_evaluation"] = {"requires_manual_review": False, "fraud_score": 0, "confidence": 0.95}

    result = agent.run(state)

    assert result.decision == DecisionType.APPROVED
    assert result.approved_amount == 1350
    assert result.confidence_score >= 0.85
    assert "1,350" in result.user_message
    print(f"  TC004: {result.user_message}")


# ----------------------------------------------------------------------
# TC006 — PARTIAL
# ----------------------------------------------------------------------

def test_tc006_partial(agent):
    """Dental: root canal approved, whitening rejected → PARTIAL ₹8000."""
    state = make_state(claimed=12000, category=ClaimCategory.DENTAL)
    state["document_verification"] = {"is_valid": True, "confidence": 1.0, "user_message": ""}
    state["member_validation"] = {"is_valid": True, "confidence": 1.0, "rejection_reasons": [], "user_message": ""}
    state["policy_evaluation"] = {
        "is_valid": True, "approved_amount": 8000, "claimed_amount": 12000,
        "rejection_reasons": [], "user_message": "",
        "copay_amount": 0, "network_discount_amount": 0,
        "line_item_decisions": [
            {"description": "Root Canal Treatment", "amount": 8000, "is_approved": True, "approved_amount": 8000, "reason": None, "exclusion_matched": None},
            {"description": "Teeth Whitening", "amount": 4000, "is_approved": False, "approved_amount": 0, "reason": "Cosmetic dental procedure", "exclusion_matched": "dental_exclusions: Teeth Whitening"},
        ],
        "confidence": 0.9,
    }
    state["fraud_evaluation"] = {"requires_manual_review": False, "fraud_score": 0, "confidence": 0.95}

    result = agent.run(state)

    assert result.decision == DecisionType.PARTIAL
    assert result.approved_amount == 8000
    assert "Root Canal" in result.user_message
    assert "Whitening" in result.user_message
    print(f"  TC006: {result.user_message}")


# ----------------------------------------------------------------------
# TC005/TC007/TC008/TC012 — REJECTED
# ----------------------------------------------------------------------

def test_tc005_rejected_waiting_period(agent):
    """Member validation fails (waiting period) → REJECTED."""
    state = make_state()
    state["document_verification"] = {"is_valid": True, "confidence": 1.0, "user_message": ""}
    state["member_validation"] = {
        "is_valid": False, "confidence": 1.0,
        "rejection_reasons": ["WAITING_PERIOD"],
        "user_message": "Type 2 Diabetes has a 90-day waiting period. Eligible from 2024-11-30.",
    }
    state["policy_evaluation"] = {"is_valid": True, "approved_amount": 3000, "claimed_amount": 3000, "confidence": 0.95}
    state["fraud_evaluation"] = {"requires_manual_review": False, "fraud_score": 0, "confidence": 0.95}

    result = agent.run(state)
    assert result.decision == DecisionType.REJECTED
    assert RejectionReason.WAITING_PERIOD in result.rejection_reasons
    assert "2024-11-30" in result.user_message


def test_tc007_rejected_preauth(agent):
    """MRI without pre-auth → REJECTED."""
    state = make_state(claimed=15000)
    state["document_verification"] = {"is_valid": True, "confidence": 1.0, "user_message": ""}
    state["member_validation"] = {"is_valid": True, "confidence": 1.0, "rejection_reasons": [], "user_message": ""}
    state["policy_evaluation"] = {
        "is_valid": False, "approved_amount": 0, "claimed_amount": 15000,
        "rejection_reasons": ["PRE_AUTH_MISSING"],
        "user_message": "Pre-authorization required for MRI above ₹10,000.",
    }
    state["fraud_evaluation"] = {"requires_manual_review": False, "fraud_score": 0, "confidence": 0.95}

    result = agent.run(state)
    assert result.decision == DecisionType.REJECTED
    assert RejectionReason.PRE_AUTH_MISSING in result.rejection_reasons
    assert "pre-authorization" in result.user_message.lower()


def test_tc008_rejected_per_claim(agent):
    """₹7500 > ₹5000 per-claim limit → REJECTED."""
    state = make_state(claimed=7500)
    state["document_verification"] = {"is_valid": True, "confidence": 1.0, "user_message": ""}
    state["member_validation"] = {"is_valid": True, "confidence": 1.0, "rejection_reasons": [], "user_message": ""}
    state["policy_evaluation"] = {
        "is_valid": False, "approved_amount": 0, "claimed_amount": 7500,
        "rejection_reasons": ["PER_CLAIM_EXCEEDED"],
        "user_message": "This claim of ₹7,500 exceeds the per-claim limit of ₹5,000.",
    }
    state["fraud_evaluation"] = {"requires_manual_review": False, "fraud_score": 0, "confidence": 0.95}

    result = agent.run(state)
    assert result.decision == DecisionType.REJECTED
    assert RejectionReason.PER_CLAIM_EXCEEDED in result.rejection_reasons


# ----------------------------------------------------------------------
# TC009 — MANUAL_REVIEW (fraud)
# ----------------------------------------------------------------------

def test_tc009_manual_review(agent):
    """Same-day claims fraud signal → MANUAL_REVIEW."""
    state = make_state()
    state["document_verification"] = {"is_valid": True, "confidence": 1.0, "user_message": ""}
    state["member_validation"] = {"is_valid": True, "confidence": 1.0, "rejection_reasons": [], "user_message": ""}
    state["policy_evaluation"] = {"is_valid": True, "approved_amount": 4800, "claimed_amount": 4800, "confidence": 0.95}
    state["fraud_evaluation"] = {
        "requires_manual_review": True, "fraud_score": 0.6, "confidence": 0.95,
        "signals_triggered": ["SAME_DAY_LIMIT_EXCEEDED"],
    }

    result = agent.run(state)
    assert result.decision == DecisionType.MANUAL_REVIEW
    assert result.requires_manual_review
    assert "48 hours" in result.user_message or "review" in result.user_message.lower()


# ----------------------------------------------------------------------
# TC001 — Document verification failure
# ----------------------------------------------------------------------

def test_tc001_doc_ver_failure(agent):
    """Wrong doc type → DocumentVerification fails → REJECTED with verification's message."""
    state = make_state()
    state["document_verification"] = {
        "is_valid": False, "confidence": 1.0,
        "user_message": "You uploaded two prescription documents for this consultation claim. Please upload the hospital bill.",
    }
    # Other agents didn't even run
    state["member_validation"] = {}
    state["policy_evaluation"] = {}
    state["fraud_evaluation"] = {}

    result = agent.run(state)
    assert result.decision == DecisionType.REJECTED
    assert "hospital bill" in result.user_message.lower()


# ----------------------------------------------------------------------
# TC011 — Graceful failure
# ----------------------------------------------------------------------

def test_tc011_graceful_failure(agent):
    """One agent failed → decision still made, but confidence reduced and manual review flagged."""
    state = make_state()
    state["document_verification"] = {"is_valid": True, "confidence": 1.0, "user_message": ""}
    state["member_validation"] = {"is_valid": True, "confidence": 1.0, "rejection_reasons": [], "user_message": ""}
    # Policy evaluation failed
    state["policy_evaluation"] = {}
    state["fraud_evaluation"] = {"requires_manual_review": False, "fraud_score": 0, "confidence": 0.95}
    state["errors"] = ["PolicyRules: simulated failure"]

    result = agent.run(state)

    # Best-effort: not rejected (no upstream rejection), but flagged for review
    assert result.requires_manual_review
    # Confidence is reduced (was 0.9 without failures, now lower due to penalty)
    assert result.confidence_score < 0.9, f"Expected <0.9, got {result.confidence_score}"
    assert any("PolicyRules" in n or "agent(s) failed" in n for n in result.ops_notes)


# ----------------------------------------------------------------------
# Run all tests
# ----------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Decision Agent Tests")
    print("=" * 60)
    pytest.main([__file__, "-v", "--tb=short"])
