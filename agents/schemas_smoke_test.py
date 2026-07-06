"""
Per-agent schema smoke test — verifies all input/output models are well-formed
and can be instantiated with realistic data.

Run with: python -m agents.schemas_smoke_test
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.core.enums import (  # noqa: E402
    ClaimCategory,
    DocumentError,
    DocumentType,
    RejectionReason,
    FraudSignal,
    DecisionType,
)
from agents.decision.schemas import Decision, DecisionInput  # noqa: E402
from agents.document_extraction.schemas import (  # noqa: E402
    DocumentExtractionInput,
    DocumentExtractionResult,
    ExtractedDocument,
    LineItem,
)
from agents.document_verification.schemas import (  # noqa: E402
    DocumentCheckResult,
    DocumentVerificationInput,
    DocumentVerificationResult,
)
from agents.fraud_detection.schemas import (  # noqa: E402
    FraudDetectionInput,
    FraudEvaluation,
)
from agents.member_validation.schemas import (  # noqa: E402
    MemberValidationInput,
    MemberValidationResult,
    WaitingPeriodStatus,
)
from agents.policy_rules.schemas import (  # noqa: E402
    LineItemDecision,
    PolicyEvaluation,
    PolicyRulesInput,
)


def test_doc_verification_result() -> None:
    """The TC001 wrong-doc result."""
    r = DocumentVerificationResult(
        is_valid=False,
        stop_processing=True,
        errors=[DocumentError.WRONG_DOCUMENT_TYPE],
        user_message=(
            "You uploaded 2 prescriptions for a consultation claim. "
            "A consultation requires a prescription AND a hospital bill. "
            "Please upload the hospital bill."
        ),
        documents_present=[DocumentType.PRESCRIPTION],
        documents_missing=[DocumentType.HOSPITAL_BILL],
    )
    assert r.is_valid is False
    assert "hospital bill" in r.user_message.lower()
    print("  [OK] DocumentVerificationResult (TC001 wrong doc)")


def test_extracted_document() -> None:
    """Realistic extraction result for TC004."""
    doc = ExtractedDocument(
        file_id="F007",
        document_type=DocumentType.PRESCRIPTION,
        patient_name="Rajesh Kumar",
        doctor_name="Dr. Arun Sharma",
        doctor_registration="KA/45678/2015",
        date="2024-11-01",
        diagnosis="Viral Fever",
        medicines=["Paracetamol 650mg", "Vitamin C 500mg"],
        extraction_confidence=0.97,
    )
    assert doc.patient_name == "Rajesh Kumar"
    assert doc.extraction_confidence == 0.97
    print("  [OK] ExtractedDocument (TC004 prescription)")


def test_member_validation_tc005() -> None:
    """TC005: Vikram Joshi, joined 2024-09-01, treatment 2024-10-15, diabetes."""
    r = MemberValidationResult(
        is_valid=False,
        member_found=True,
        member_id="EMP005",
        member_name="Vikram Joshi",
        relationship="SELF",
        join_date=date(2024, 9, 1),
        waiting_period_checks=[
            WaitingPeriodStatus(
                condition="diabetes",
                required_days=90,
                elapsed_days=44,
                is_eligible=False,
                eligible_from_date=date(2024, 11, 30),
                message="44 days elapsed; 90 required. Eligible from 2024-11-30.",
            )
        ],
        initial_waiting_passed=True,
        condition_waiting_passed=False,
        days_until_eligible=46,
        rejection_reasons=[RejectionReason.WAITING_PERIOD],
        user_message=(
            "Type 2 Diabetes has a 90-day waiting period. Your policy was effective "
            "from 2024-09-01 and your treatment was on 2024-10-15 — only 44 days "
            "have passed. You will be eligible for diabetes-related claims from 2024-11-30."
        ),
    )
    assert r.is_valid is False
    assert RejectionReason.WAITING_PERIOD in r.rejection_reasons
    assert "2024-11-30" in r.user_message
    print("  [OK] MemberValidationResult (TC005 waiting period)")


def test_policy_evaluation_tc010() -> None:
    """TC010: Apollo Hospitals, ₹4500, network disc 20% then co-pay 10% = ₹3240."""
    r = PolicyEvaluation(
        is_valid=True,
        claimed_amount=4500,
        approved_amount=3240,
        category_sub_limit=2000,  # consultation sub_limit
        is_network_hospital=True,
        network_discount_percent=20,
        network_discount_amount=900,
        amount_after_network_discount=3600,
        copay_percent=10,
        copay_amount=360,
        amount_after_copay=3240,
        per_claim_limit=5000,
        per_claim_exceeded=False,
        calculation_steps=[
            "claimed: ₹4,500",
            "network discount 20%: -₹900",
            "after network discount: ₹3,600",
            "co-pay 10%: -₹360",
            "final approved: ₹3,240",
        ],
    )
    assert r.approved_amount == 3240
    assert r.network_discount_amount == 900
    assert r.copay_amount == 360
    assert "₹3,240" in r.calculation_steps[-1]
    print("  [OK] PolicyEvaluation (TC010 network disc + co-pay)")


def test_policy_evaluation_tc006() -> None:
    """TC006: DENTAL — root canal approved, whitening rejected. ₹8000 approved."""
    r = PolicyEvaluation(
        is_valid=True,
        claimed_amount=12000,
        approved_amount=8000,
        category_sub_limit=10000,
        per_claim_limit=5000,  # not exceeded for dental
        per_claim_exceeded=False,
        line_item_decisions=[
            LineItemDecision(
                description="Root Canal Treatment",
                amount=8000,
                is_approved=True,
                approved_amount=8000,
            ),
            LineItemDecision(
                description="Teeth Whitening",
                amount=4000,
                is_approved=False,
                approved_amount=0,
                reason="Cosmetic dental procedure — not covered",
                exclusion_matched="dental_exclusions: Teeth Whitening",
            ),
        ],
        calculation_steps=[
            "Root Canal Treatment ₹8,000 → APPROVED",
            "Teeth Whitening ₹4,000 → REJECTED (dental_exclusions)",
            "Final approved: ₹8,000",
        ],
    )
    assert r.approved_amount == 8000
    assert sum(li.approved_amount for li in r.line_item_decisions) == 8000
    assert not r.line_item_decisions[1].is_approved
    print("  [OK] PolicyEvaluation (TC006 line-item partial)")


def test_fraud_evaluation_tc009() -> None:
    """TC009: 4 claims same day > 2 limit → MANUAL_REVIEW."""
    r = FraudEvaluation(
        fraud_score=0.6,
        signals_triggered=[FraudSignal.SAME_DAY_LIMIT_EXCEEDED],
        requires_manual_review=True,
        notes=["Same-day limit (2) exceeded: 4 claims on 2024-10-30"],
        same_day_claims_count=4,
        monthly_claims_count=4,
        claimed_amount=4800,
        is_high_value=False,
    )
    assert r.requires_manual_review is True
    assert FraudSignal.SAME_DAY_LIMIT_EXCEEDED in r.signals_triggered
    print("  [OK] FraudEvaluation (TC009 same-day limit)")


def test_decision_approved() -> None:
    """TC004: clean approval."""
    d = Decision(
        decision=DecisionType.APPROVED,
        approved_amount=1350,
        confidence_score=0.95,
        user_message=(
            "Your claim for ₹1,500 has been approved. A 10% co-pay of ₹150 has "
            "been applied per your policy. ₹1,350 will be reimbursed."
        ),
    )
    assert d.decision == DecisionType.APPROVED
    assert d.approved_amount == 1350
    assert d.confidence_score == 0.95
    print("  [OK] Decision (TC004 approved)")


def test_decision_graceful() -> None:
    """TC011: graceful failure — approved with reduced confidence."""
    d = Decision(
        decision=DecisionType.APPROVED,
        approved_amount=3600,  # would be 4000 with full pipeline
        confidence_score=0.72,  # reduced from typical 0.9
        user_message=(
            "Your claim has been approved for ₹3,600. Note: one component of "
            "our pipeline could not complete; manual review is recommended."
        ),
        ops_notes=["DocumentExtraction agent failed — partial result used"],
        requires_manual_review=True,
        next_steps=["Ops team: review extraction before disbursement"],
    )
    assert d.confidence_score < 0.8
    assert d.requires_manual_review is True
    print("  [OK] Decision (TC011 graceful failure)")


def main() -> None:
    print("=" * 60)
    print("Phase 1 Smoke Test — Per-Agent Schemas")
    print("=" * 60)
    test_doc_verification_result()
    test_extracted_document()
    test_member_validation_tc005()
    test_policy_evaluation_tc010()
    test_policy_evaluation_tc006()
    test_fraud_evaluation_tc009()
    test_decision_approved()
    test_decision_graceful()
    print("=" * 60)
    print("All per-agent schema tests passed ✓")
    print("=" * 60)


if __name__ == "__main__":
    main()
