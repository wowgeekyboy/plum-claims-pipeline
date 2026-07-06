"""
Smoke test: verify the core models and policy loader work end-to-end.

Run with: python -m agents.core.smoke_test
"""

import sys
from datetime import date
from pathlib import Path

# Add project root to path so we can import agents.*
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agents.core.domain import ClaimInput, DocumentInput, ClaimHistoryItem  # noqa: E402
from agents.core.enums import (  # noqa: E402
    ClaimCategory,
    DecisionType,
    DocumentQuality,
    DocumentType,
    RejectionReason,
)
from agents.core.policy_loader import get_policy, load_policy  # noqa: E402
from agents.core.state import make_initial_state  # noqa: E402
from agents.core.trace import AgentTrace, ClaimTrace  # noqa: E402


def test_policy_loads() -> None:
    """Policy should load and have expected values."""
    policy = get_policy()
    assert policy.policy_id == "PLUM_GHI_2024", f"Got {policy.policy_id}"
    assert policy.per_claim_limit == 5000, f"Got {policy.per_claim_limit}"
    assert policy.annual_opd_limit == 50000
    assert len(policy.members) >= 10, f"Got {len(policy.members)} members"
    assert ClaimCategory.CONSULTATION in policy.opd_categories
    assert policy.opd_categories[ClaimCategory.CONSULTATION].sub_limit == 2000
    assert policy.opd_categories[ClaimCategory.CONSULTATION].copay_percent == 10
    assert ClaimCategory.PHARMACY in policy.opd_categories
    assert "Apollo Hospitals" in policy.network_hospitals
    assert policy.waiting_periods.specific_conditions.get("diabetes") == 90
    assert "Bariatric surgery" in policy.exclusions.conditions
    print("  [OK] policy loads with all expected fields")


def test_member_lookup() -> None:
    """Should be able to look up members by ID."""
    policy = get_policy()
    emp001 = policy.get_member("EMP001")
    assert emp001 is not None
    assert emp001.name == "Rajesh Kumar"
    assert emp001.relationship.value == "SELF"
    assert "DEP001" in emp001.dependents
    missing = policy.get_member("DOES_NOT_EXIST")
    assert missing is None
    print("  [OK] member lookup works (found EMP001, rejects nonexistent)")


def test_claim_input_validates() -> None:
    """ClaimInput should accept a typical submission."""
    claim = ClaimInput(
        member_id="EMP001",
        policy_id="PLUM_GHI_2024",
        claim_category=ClaimCategory.CONSULTATION,
        treatment_date=date(2024, 11, 1),
        claimed_amount=1500.0,
        hospital_name="City Clinic",
        documents=[
            DocumentInput(
                file_id="F001",
                file_name="prescription.jpg",
                actual_type=DocumentType.PRESCRIPTION,
            ),
        ],
    )
    assert claim.claim_id is None  # auto-generated
    assert len(claim.documents) == 1
    print("  [OK] ClaimInput validates with one document")


def test_initial_state() -> None:
    """make_initial_state should produce a valid AgentState."""
    claim = ClaimInput(
        member_id="EMP001",
        policy_id="PLUM_GHI_2024",
        claim_category=ClaimCategory.CONSULTATION,
        treatment_date=date(2024, 11, 1),
        claimed_amount=1500.0,
    )
    state = make_initial_state(claim)
    assert state["claim_id"].startswith("CLM_")
    assert state["trace"] == []
    assert state["pipeline_status"] == "running"
    assert state["errors"] == []
    print(f"  [OK] initial state created (claim_id={state['claim_id']})")


def test_agent_trace() -> None:
    """AgentTrace should serialize and round-trip cleanly."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    t = AgentTrace(
        agent_name="DocumentVerification",
        status="SUCCESS",
        started_at=now,
        completed_at=now,
        duration_ms=42.5,
        confidence_contribution=0.95,
        notes=["all docs valid"],
    )
    # JSON-mode dump (what gets sent over the wire) should have string values
    d = t.model_dump(mode="json")
    assert d["agent_name"] == "DocumentVerification"
    assert d["duration_ms"] == 42.5
    print("  [OK] AgentTrace serializes correctly (json mode)")


def test_claim_trace_markdown() -> None:
    """ClaimTrace should render to a readable markdown string."""
    from datetime import datetime
    t = AgentTrace(
        agent_name="DocumentVerification",
        status="SUCCESS",
        started_at=datetime(2024, 11, 1, 10, 0, 0),
        completed_at=datetime(2024, 11, 1, 10, 0, 1),
        duration_ms=1000.0,
        confidence_contribution=0.9,
    )
    trace = ClaimTrace(
        claim_id="CLM_TEST",
        policy_id="PLUM_GHI_2024",
        member_id="EMP001",
        claim_category="CONSULTATION",
        submitted_at=datetime(2024, 11, 1, 10, 0, 0),
        completed_at=datetime(2024, 11, 1, 10, 0, 5),
        total_duration_ms=5000.0,
        agent_traces=[t],
        overall_confidence=0.9,
        final_decision="APPROVED",
    )
    md = trace.to_markdown()
    assert "CLM_TEST" in md
    assert "DocumentVerification" in md
    assert "APPROVED" in md
    assert "|" in md  # markdown table
    print("  [OK] ClaimTrace renders markdown (length=%d)" % len(md))


def main() -> None:
    print("=" * 60)
    print("Phase 1 Smoke Test — Core Models + Policy Loader")
    print("=" * 60)
    test_policy_loads()
    test_member_lookup()
    test_claim_input_validates()
    test_initial_state()
    test_agent_trace()
    test_claim_trace_markdown()
    print("=" * 60)
    print("All smoke tests passed ✓")
    print("=" * 60)


if __name__ == "__main__":
    main()
