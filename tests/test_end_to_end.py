"""
End-to-end pipeline tests — run all 12 test cases from test_cases.json
through the full multi-agent pipeline and verify the expected decisions.
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.core.domain import ClaimHistoryItem, ClaimInput, DocumentInput  # noqa: E402
from agents.core.enums import (  # noqa: E402
    ClaimCategory,
    ComponentFailure,
    DecisionType,
    DocumentQuality,
    DocumentType,
)
from agents.api.orchestrator import run_claim  # noqa: E402


def load_test_cases():
    """Load all 12 test cases from data/test_cases.json."""
    with open(ROOT / "data" / "test_cases.json") as f:
        return json.load(f)["test_cases"]


def build_claim(tc_input: dict) -> ClaimInput:
    """Convert a test case input dict into a ClaimInput."""
    docs = []
    for d in tc_input.get("documents", []):
        actual_type = DocumentType(d["actual_type"]) if d.get("actual_type") else None
        quality = DocumentQuality(d.get("quality", "GOOD"))
        docs.append(
            DocumentInput(
                file_id=d["file_id"],
                file_name=d.get("file_name"),
                actual_type=actual_type,
                quality=quality,
                content=d.get("content"),
                patient_name_on_doc=d.get("patient_name_on_doc"),
            )
        )
    history = [
        ClaimHistoryItem(
            claim_id=h["claim_id"],
            date=date.fromisoformat(h["date"]),
            amount=h["amount"],
            provider=h.get("provider"),
        )
        for h in tc_input.get("claims_history", [])
    ]
    # Test data uses "true" (boolean) for simulate_component_failure
    sim = tc_input.get("simulate_component_failure")
    if sim is True:
        # Boolean true — pick a default component to fail (TC011 case)
        sim_failure = ComponentFailure.DOCUMENT_EXTRACTION
    elif sim:
        sim_failure = ComponentFailure(sim)
    else:
        sim_failure = None
    return ClaimInput(
        member_id=tc_input["member_id"],
        policy_id=tc_input["policy_id"],
        claim_category=ClaimCategory(tc_input["claim_category"]),
        treatment_date=date.fromisoformat(tc_input["treatment_date"]),
        claimed_amount=tc_input["claimed_amount"],
        hospital_name=tc_input.get("hospital_name"),
        documents=docs,
        ytd_claims_amount=tc_input.get("ytd_claims_amount", 0),
        claims_history=history,
        simulate_component_failure=sim_failure,
    )


# ----------------------------------------------------------------------
# Individual test cases — explicit assertions
# ----------------------------------------------------------------------

def _run_case(case_id: str):
    cases = {tc["case_id"]: tc for tc in load_test_cases()}
    tc = cases[case_id]
    claim = build_claim(tc["input"])
    return run_claim(claim), tc


def test_tc001_wrong_document_type():
    """Wrong doc type → REJECTED with specific message."""
    trace, tc = _run_case("TC001")
    assert trace.final_decision == "REJECTED"
    decision_msg = next(
        (n.notes for n in trace.agent_traces
         if n.agent_name.value == "Decision"),
        [],
    )
    # Check the verification message names the right things
    doc_ver = next(t for t in trace.agent_traces if t.agent_name.value == "DocumentVerification")
    assert "prescription" in str(doc_ver.notes).lower()
    assert "hospital bill" in str(doc_ver.notes).lower()


def test_tc002_unreadable_document():
    """Unreadable document → REJECTED with re-upload message."""
    trace, tc = _run_case("TC002")
    assert trace.final_decision == "REJECTED"
    doc_ver = next(t for t in trace.agent_traces if t.agent_name.value == "DocumentVerification")
    assert "re-upload" in str(doc_ver.notes).lower() or "blurry" in str(doc_ver.notes).lower()


def test_tc003_patient_mismatch():
    """Patient mismatch → REJECTED with names surfaced."""
    trace, tc = _run_case("TC003")
    assert trace.final_decision == "REJECTED"
    doc_ver = next(t for t in trace.agent_traces if t.agent_name.value == "DocumentVerification")
    notes_str = str(doc_ver.notes)
    assert "Rajesh Kumar" in notes_str
    assert "Arjun Mehta" in notes_str


def test_tc004_clean_consultation():
    """Clean consultation → APPROVED ₹1,350."""
    trace, tc = _run_case("TC004")
    assert trace.final_decision == "APPROVED"
    # Approved amount is 1350 (1500 - 10% co-pay)
    from agents.api.orchestrator import get_orchestrator
    from agents.core.state import make_initial_state
    state = make_initial_state(build_claim(tc["input"]))
    state["trace"] = []
    final = get_orchestrator().invoke(state)
    assert final["decision"]["approved_amount"] == 1350


def test_tc005_diabetes_waiting_period():
    """Diabetes within 90-day waiting period → REJECTED."""
    trace, tc = _run_case("TC005")
    assert trace.final_decision == "REJECTED"
    mem_val = next(t for t in trace.agent_traces if t.agent_name.value == "MemberValidation")
    notes = str(mem_val.notes)
    assert "WAITING" in notes.upper() or "2024-11-30" in notes


def test_tc006_dental_partial():
    """Dental partial: root canal approved, whitening rejected → PARTIAL ₹8,000."""
    trace, tc = _run_case("TC006")
    assert trace.final_decision == "PARTIAL"
    from agents.api.orchestrator import get_orchestrator
    from agents.core.state import make_initial_state
    state = make_initial_state(build_claim(tc["input"]))
    state["trace"] = []
    final = get_orchestrator().invoke(state)
    assert final["decision"]["approved_amount"] == 8000


def test_tc007_mri_no_preauth():
    """MRI ₹15,000 without pre-auth → REJECTED."""
    trace, tc = _run_case("TC007")
    assert trace.final_decision == "REJECTED"
    policy = next(t for t in trace.agent_traces if t.agent_name.value == "PolicyRules")
    notes = str(policy.notes)
    assert "pre-authorization" in notes.lower() or "PRE_AUTH" in notes


def test_tc008_per_claim_exceeded():
    """₹7,500 > ₹5,000 per-claim limit → REJECTED."""
    trace, tc = _run_case("TC008")
    assert trace.final_decision == "REJECTED"
    policy = next(t for t in trace.agent_traces if t.agent_name.value == "PolicyRules")
    notes = str(policy.notes)
    assert "5,000" in notes or "per-claim" in notes.lower()


def test_tc009_fraud_manual_review():
    """4 same-day claims → MANUAL_REVIEW."""
    trace, tc = _run_case("TC009")
    assert trace.final_decision == "MANUAL_REVIEW"
    fraud = next(t for t in trace.agent_traces if t.agent_name.value == "FraudDetection")
    notes = str(fraud.notes)
    assert "SAME_DAY" in notes or "same-day" in notes.lower()


def test_tc010_network_hospital_discount():
    """Apollo network: ₹4,500 → 20% disc + 10% co-pay = ₹3,240."""
    trace, tc = _run_case("TC010")
    assert trace.final_decision == "APPROVED"
    from agents.api.orchestrator import get_orchestrator
    from agents.core.state import make_initial_state
    state = make_initial_state(build_claim(tc["input"]))
    state["trace"] = []
    final = get_orchestrator().invoke(state)
    assert final["decision"]["approved_amount"] == 3240
    # Verify network discount was applied (not just co-pay)
    policy = final["policy_evaluation"]
    assert policy["is_network_hospital"] is True
    assert policy["network_discount_amount"] == 900
    assert policy["copay_amount"] == 360


def test_tc011_component_failure_graceful():
    """One component fails → pipeline continues with reduced confidence."""
    trace, tc = _run_case("TC011")
    # Expected decision: APPROVED, but with reduced confidence
    assert trace.final_decision == "APPROVED"
    # Confidence should be reduced (typical full pipeline is 0.95+)
    assert trace.overall_confidence < 0.95, f"Expected reduced confidence, got {trace.overall_confidence}"
    # The failed agent should appear in the trace
    failed = [t for t in trace.agent_traces if t.status.value == "FAILED"]
    assert len(failed) >= 1
    # Decision agent should flag for manual review
    from agents.api.orchestrator import get_orchestrator
    from agents.core.state import make_initial_state
    state = make_initial_state(build_claim(tc["input"]))
    state["trace"] = []
    final = get_orchestrator().invoke(state)
    assert final["decision"]["requires_manual_review"] is True


def test_tc012_bariatric_excluded():
    """Bariatric consultation → EXCLUDED_CONDITION → REJECTED."""
    trace, tc = _run_case("TC012")
    assert trace.final_decision == "REJECTED"
    policy = next(t for t in trace.agent_traces if t.agent_name.value == "PolicyRules")
    notes = str(policy.notes)
    assert "excluded" in notes.lower() or "bariatric" in notes.lower()


# ----------------------------------------------------------------------
# Run all tests
# ----------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("End-to-End Pipeline Tests (All 12 Test Cases)")
    print("=" * 60)
    pytest.main([__file__, "-v", "--tb=short"])
