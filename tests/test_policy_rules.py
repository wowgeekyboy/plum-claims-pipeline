"""
Tests for PolicyRulesEngine.

Covered test cases:
  TC004 — Consultation ₹1500, 10% co-pay → ₹1350
  TC006 — Dental ₹12000, root canal approved (₹8000), whitening excluded
  TC007 — MRI ₹15000 without pre-auth → REJECT
  TC008 — Per-claim ₹7500 > ₹5000 → REJECT
  TC010 — Network Apollo, 20% disc first, then 10% co-pay → ₹3240
  TC012 — Bariatric consultation → EXCLUDED_CONDITION → REJECT
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
from agents.policy_rules.agent import PolicyRulesEngine  # noqa: E402


@pytest.fixture
def policy():
    return get_policy()


@pytest.fixture
def engine(policy):
    return PolicyRulesEngine(policy)


def make_claim(documents, member_id="EMP001", category=ClaimCategory.CONSULTATION,
               claimed_amount=1500.0, hospital_name=None):
    return ClaimInput(
        member_id=member_id,
        policy_id="PLUM_GHI_2024",
        claim_category=category,
        treatment_date=date(2024, 11, 1),
        claimed_amount=claimed_amount,
        hospital_name=hospital_name,
        documents=documents,
    )


# ----------------------------------------------------------------------
# TC004 — Consultation with co-pay
# ----------------------------------------------------------------------

def test_tc004_consultation_with_copay(engine):
    """₹1500 claimed, 10% co-pay → ₹1350 approved."""
    claim = make_claim([
        DocumentInput(
            file_id="F007", actual_type=DocumentType.PRESCRIPTION,
            content={"diagnosis": "Viral Fever"},
        ),
        DocumentInput(
            file_id="F008", actual_type=DocumentType.HOSPITAL_BILL,
            content={
                "hospital_name": "City Clinic",
                "line_items": [
                    {"description": "Consultation", "amount": 1000},
                    {"description": "CBC", "amount": 300},
                    {"description": "Dengue", "amount": 200},
                ],
                "total": 1500,
            },
        ),
    ], claimed_amount=1500, hospital_name="City Clinic, Bengaluru")
    state = make_initial_state(claim)
    state["extracted_documents"] = [
        {"file_id": "F007", "document_type": "PRESCRIPTION", "diagnosis": "Viral Fever",
         "is_readable": True, "extraction_confidence": 1.0, "medicines": [], "tests_ordered": [], "line_items": []},
        {"file_id": "F008", "document_type": "HOSPITAL_BILL", "hospital_name": "City Clinic, Bengaluru",
         "is_readable": True, "extraction_confidence": 1.0, "total_amount": 1500,
         "line_items": [
             {"description": "Consultation", "amount": 1000},
             {"description": "CBC", "amount": 300},
             {"description": "Dengue", "amount": 200},
         ]},
    ]
    result = engine.run(state)

    assert result.is_valid
    assert result.approved_amount == 1350, f"Expected ₹1350, got ₹{result.approved_amount}"
    assert result.copay_percent == 10
    assert result.copay_amount == 150
    assert "claimed: ₹1,500" in result.calculation_steps[0]
    assert any("co-pay 10%" in s for s in result.calculation_steps)
    assert not result.is_network_hospital
    print(f"  TC004 calculation: {' | '.join(result.calculation_steps)}")


# ----------------------------------------------------------------------
# TC006 — Dental partial
# ----------------------------------------------------------------------

def test_tc006_dental_partial(engine):
    """Root canal ₹8000 approved, teeth whitening ₹4000 rejected. Total approved ₹8000."""
    claim = make_claim(
        documents=[
            DocumentInput(
                file_id="F011", actual_type=DocumentType.HOSPITAL_BILL,
                content={
                    "hospital_name": "Smile Dental Clinic",
                    "line_items": [
                        {"description": "Root Canal Treatment", "amount": 8000},
                        {"description": "Teeth Whitening", "amount": 4000},
                    ],
                    "total": 12000,
                },
            ),
        ],
        category=ClaimCategory.DENTAL,
        member_id="EMP002",
        claimed_amount=12000,
        hospital_name="Smile Dental Clinic",
    )
    state = make_initial_state(claim)
    state["extracted_documents"] = [{
        "file_id": "F011", "document_type": "HOSPITAL_BILL",
        "hospital_name": "Smile Dental Clinic", "is_readable": True,
        "extraction_confidence": 1.0, "total_amount": 12000,
        "line_items": [
            {"description": "Root Canal Treatment", "amount": 8000},
            {"description": "Teeth Whitening", "amount": 4000},
        ],
    }]
    result = engine.run(state)

    assert result.is_valid
    assert result.approved_amount == 8000, f"Expected ₹8000, got ₹{result.approved_amount}"
    assert len(result.line_item_decisions) == 2
    # Root canal approved
    rc = [li for li in result.line_item_decisions if "Root Canal" in li.description][0]
    assert rc.is_approved
    assert rc.approved_amount == 8000
    # Whitening rejected
    tw = [li for li in result.line_item_decisions if "Whitening" in li.description][0]
    assert not tw.is_approved
    assert tw.exclusion_matched is not None
    print(f"  TC006: ₹{result.approved_amount} approved, ₹{result.claimed_amount - result.approved_amount} rejected")


# ----------------------------------------------------------------------
# TC007 — MRI without pre-auth
# ----------------------------------------------------------------------

def test_tc007_mri_no_preauth(engine):
    """MRI ₹15000 without pre-auth → REJECT."""
    claim = make_claim(
        documents=[
            DocumentInput(
                file_id="F012", actual_type=DocumentType.PRESCRIPTION,
                content={"diagnosis": "Lumbar Disc Herniation", "tests_ordered": ["MRI Lumbar Spine"]},
            ),
            DocumentInput(
                file_id="F013", actual_type=DocumentType.LAB_REPORT,
                content={"test_name": "MRI Lumbar Spine"},
            ),
            DocumentInput(
                file_id="F014", actual_type=DocumentType.HOSPITAL_BILL,
                content={
                    "line_items": [{"description": "MRI Lumbar Spine", "amount": 15000}],
                    "total": 15000,
                },
            ),
        ],
        category=ClaimCategory.DIAGNOSTIC,
        member_id="EMP007",
        claimed_amount=15000,
    )
    state = make_initial_state(claim)
    state["extracted_documents"] = [
        {"file_id": "F012", "document_type": "PRESCRIPTION", "diagnosis": "Lumbar Disc Herniation",
         "tests_ordered": ["MRI Lumbar Spine"], "is_readable": True, "extraction_confidence": 1.0,
         "line_items": [], "medicines": []},
        {"file_id": "F013", "document_type": "LAB_REPORT", "treatment": "MRI Lumbar Spine",
         "is_readable": True, "extraction_confidence": 1.0, "line_items": [], "medicines": []},
        {"file_id": "F014", "document_type": "HOSPITAL_BILL", "is_readable": True,
         "extraction_confidence": 1.0, "total_amount": 15000,
         "line_items": [{"description": "MRI Lumbar Spine", "amount": 15000}]},
    ]
    result = engine.run(state)

    assert not result.is_valid
    assert RejectionReason.PRE_AUTH_MISSING in result.rejection_reasons
    assert result.approved_amount == 0
    assert "pre-authorization" in result.user_message.lower()
    assert any("MRI" in t for t in result.high_value_tests)
    print(f"  TC007 message: {result.user_message}")


# ----------------------------------------------------------------------
# TC008 — Per-claim limit exceeded
# ----------------------------------------------------------------------

def test_tc008_per_claim_exceeded(engine):
    """₹7500 > ₹5000 per-claim limit → REJECT."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F015", actual_type=DocumentType.PRESCRIPTION,
                          content={"diagnosis": "Gastroenteritis"}),
            DocumentInput(file_id="F016", actual_type=DocumentType.HOSPITAL_BILL,
                          content={"total": 7500}),
        ],
        member_id="EMP003",
        claimed_amount=7500,
    )
    state = make_initial_state(claim)
    state["extracted_documents"] = [
        {"file_id": "F015", "document_type": "PRESCRIPTION", "diagnosis": "Gastroenteritis",
         "is_readable": True, "extraction_confidence": 1.0, "medicines": [], "tests_ordered": [], "line_items": []},
        {"file_id": "F016", "document_type": "HOSPITAL_BILL", "is_readable": True,
         "extraction_confidence": 1.0, "total_amount": 7500, "line_items": []},
    ]
    result = engine.run(state)

    assert not result.is_valid
    assert result.per_claim_exceeded
    assert RejectionReason.PER_CLAIM_EXCEEDED in result.rejection_reasons
    assert "5,000" in result.user_message or "5000" in result.user_message
    assert "7,500" in result.user_message or "7500" in result.user_message
    print(f"  TC008 message: {result.user_message}")


# ----------------------------------------------------------------------
# TC010 — Network hospital discount order matters
# ----------------------------------------------------------------------

def test_tc010_network_discount_before_copay(engine):
    """Apollo (network): ₹4500 → 20% disc (-900) → ₹3600 → 10% co-pay (-360) → ₹3240."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F019", actual_type=DocumentType.PRESCRIPTION,
                          content={"diagnosis": "Acute Bronchitis"}),
            DocumentInput(file_id="F020", actual_type=DocumentType.HOSPITAL_BILL,
                          content={
                              "hospital_name": "Apollo Hospitals",
                              "line_items": [
                                  {"description": "Consultation", "amount": 1500},
                                  {"description": "Medicines", "amount": 3000},
                              ],
                              "total": 4500,
                          }),
        ],
        member_id="EMP010",
        claimed_amount=4500,
        hospital_name="Apollo Hospitals",
    )
    state = make_initial_state(claim)
    state["extracted_documents"] = [
        {"file_id": "F019", "document_type": "PRESCRIPTION", "diagnosis": "Acute Bronchitis",
         "is_readable": True, "extraction_confidence": 1.0, "medicines": [], "tests_ordered": [], "line_items": []},
        {"file_id": "F020", "document_type": "HOSPITAL_BILL", "hospital_name": "Apollo Hospitals",
         "is_readable": True, "extraction_confidence": 1.0, "total_amount": 4500,
         "line_items": [
             {"description": "Consultation", "amount": 1500},
             {"description": "Medicines", "amount": 3000},
         ]},
    ]
    result = engine.run(state)

    assert result.is_valid
    assert result.is_network_hospital
    assert result.network_discount_percent == 20
    assert result.network_discount_amount == 900
    assert result.amount_after_network_discount == 3600
    assert result.copay_percent == 10
    assert result.copay_amount == 360
    assert result.approved_amount == 3240, f"Expected ₹3240, got ₹{result.approved_amount}"
    print(f"  TC010 calculation: {' | '.join(result.calculation_steps)}")

    # Verify order: network BEFORE copay in calculation_steps
    net_idx = next(i for i, s in enumerate(result.calculation_steps) if "network discount" in s)
    copay_idx = next(i for i, s in enumerate(result.calculation_steps) if "co-pay" in s)
    assert net_idx < copay_idx, "Network discount must come before co-pay"


# ----------------------------------------------------------------------
# TC012 — Excluded treatment
# ----------------------------------------------------------------------

def test_tc012_bariatric_excluded(engine):
    """Bariatric consultation → EXCLUDED_CONDITION → REJECT."""
    claim = make_claim(
        documents=[
            DocumentInput(file_id="F023", actual_type=DocumentType.PRESCRIPTION,
                          content={"diagnosis": "Morbid Obesity — BMI 37", "treatment": "Bariatric Consultation"}),
            DocumentInput(file_id="F024", actual_type=DocumentType.HOSPITAL_BILL,
                          content={
                              "line_items": [
                                  {"description": "Bariatric Consultation", "amount": 3000},
                                  {"description": "Diet Program", "amount": 5000},
                              ],
                              "total": 8000,
                          }),
        ],
        member_id="EMP009",
        claimed_amount=8000,
    )
    state = make_initial_state(claim)
    state["extracted_documents"] = [
        {"file_id": "F023", "document_type": "PRESCRIPTION", "diagnosis": "Morbid Obesity — BMI 37",
         "treatment": "Bariatric Consultation", "is_readable": True, "extraction_confidence": 1.0,
         "medicines": [], "tests_ordered": [], "line_items": []},
        {"file_id": "F024", "document_type": "HOSPITAL_BILL", "is_readable": True,
         "extraction_confidence": 1.0, "total_amount": 8000,
         "line_items": [
             {"description": "Bariatric Consultation", "amount": 3000},
             {"description": "Diet Program", "amount": 5000},
         ]},
    ]
    result = engine.run(state)

    assert not result.is_valid
    assert RejectionReason.EXCLUDED_CONDITION in result.rejection_reasons
    assert result.approved_amount == 0
    assert "excluded" in result.user_message.lower() or "bariatric" in result.user_message.lower()
    assert result.confidence >= 0.9
    print(f"  TC012 message: {result.user_message}")


# ----------------------------------------------------------------------
# Run all tests
# ----------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("PolicyRules Engine Tests")
    print("=" * 60)
    pytest.main([__file__, "-v", "--tb=short"])
