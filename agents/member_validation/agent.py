"""
MemberValidationAgent — implementation.

Validates that the member is:
  1. On the policy (member roster lookup)
  2. Past the initial 30-day waiting period
  3. Past the condition-specific waiting period (diabetes: 90, maternity: 270, etc.)

Test cases:
  TC005 — Vikram Joshi joined 2024-09-01, claimed diabetes 2024-10-15 (44 days < 90)
  TC012 — Obesity is excluded AND has 365-day waiting; either can REJECT

DESIGN NOTES
============
The agent is pure logic — no LLM. Diagnosis matching uses a hand-curated
shorthand table (T2DM = Type 2 Diabetes, HTN = Hypertension). In production
this would be backed by an LLM call for fuzzy match.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from agents.core.domain import Policy
from agents.core.enums import (
    AgentName,
    AgentStatus,
    RejectionReason,
)
from agents.core.state import AgentState
from agents.core.trace import AgentTrace
from agents.member_validation.schemas import (
    MemberValidationResult,
    WaitingPeriodStatus,
)


# ----------------------------------------------------------------------
# Diagnosis shorthand table
# ----------------------------------------------------------------------
# Maps shorthand/abbreviation to canonical condition name
# (which must match a key in policy.waiting_periods.specific_conditions).
DIAGNOSIS_SYNONYMS: dict[str, str] = {
    # Diabetes
    "t2dm": "diabetes",
    "t1dm": "diabetes",
    "dm": "diabetes",
    "diabetes mellitus": "diabetes",
    "type 2 diabetes": "diabetes",
    "type 1 diabetes": "diabetes",
    "type ii diabetes": "diabetes",
    "dm-2": "diabetes",
    "dm-1": "diabetes",
    "niddm": "diabetes",
    "iddm": "diabetes",
    # Hypertension
    "htn": "hypertension",
    "high bp": "hypertension",
    "high blood pressure": "hypertension",
    "essential hypertension": "hypertension",
    # Thyroid
    "hypothyroid": "thyroid_disorders",
    "hyperthyroid": "thyroid_disorders",
    "thyroid": "thyroid_disorders",
    # Maternity
    "pregnancy": "maternity",
    "delivery": "maternity",
    "caesarean": "maternity",
    "c-section": "maternity",
    "lscs": "maternity",
    "antenatal": "maternity",
    "prenatal": "maternity",
    # Joint replacement
    "tka": "joint_replacement",
    "thr": "joint_replacement",
    "knee replacement": "joint_replacement",
    "hip replacement": "joint_replacement",
    # Mental health
    "depression": "mental_health",
    "anxiety": "mental_health",
    "bipolar": "mental_health",
    "schizophrenia": "mental_health",
    "ptsd": "mental_health",
    # Cataract
    "cataract surgery": "cataract",
    # Hernia
    "hernia repair": "hernia",
    "inguinal hernia": "hernia",
    # Obesity
    "obesity": "obesity_treatment",
    "morbid obesity": "obesity_treatment",
    "bariatric": "obesity_treatment",
    "weight loss": "obesity_treatment",
}


def normalize_diagnosis(diagnosis: str | None) -> str:
    """Normalize a diagnosis string to a canonical condition name.

    Returns the canonical name (e.g. "diabetes") or empty string.
    """
    if not diagnosis:
        return ""
    d = diagnosis.lower().strip()
    # Direct lookup
    if d in DIAGNOSIS_SYNONYMS:
        return DIAGNOSIS_SYNONYMS[d]
    # Substring match — look for any synonym inside the diagnosis
    for synonym, canonical in DIAGNOSIS_SYNONYMS.items():
        if synonym in d:
            return canonical
    return ""


class MemberValidationAgent:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    def run(self, state: AgentState) -> MemberValidationResult:
        claim_input = state["claim_input"]
        member_id = claim_input.member_id
        treatment_date = claim_input.treatment_date

        # 1. Member lookup
        member = self.policy.get_member(member_id)
        if member is None:
            return MemberValidationResult(
                is_valid=False,
                member_found=False,
                member_id=member_id,
                rejection_reasons=[RejectionReason.MEMBER_INELIGIBLE],
                user_message=f"Member ID '{member_id}' is not on this policy. Please check the ID and try again.",
                confidence=1.0,
            )

        # 2. Initial waiting period
        initial_days = self.policy.waiting_periods.initial_waiting_period_days
        initial_passed, initial_eligible_from = self._check_waiting(
            member.join_date, treatment_date, initial_days
        )

        # 3. Condition-specific waiting period
        # Get the diagnosis from extracted documents
        diagnosis = self._get_diagnosis(state)
        canonical_condition = normalize_diagnosis(diagnosis)

        condition_passed = True
        condition_eligible_from: date | None = None
        days_until_eligible = 0
        condition_check: WaitingPeriodStatus | None = None

        if canonical_condition and canonical_condition in self.policy.waiting_periods.specific_conditions:
            req_days = self.policy.waiting_periods.specific_conditions[canonical_condition]
            condition_passed, condition_eligible_from = self._check_waiting(
                member.join_date, treatment_date, req_days
            )
            elapsed = (treatment_date - member.join_date).days
            if not condition_passed:
                days_until_eligible = max(0, (condition_eligible_from - treatment_date).days)
            condition_check = WaitingPeriodStatus(
                condition=canonical_condition,
                required_days=req_days,
                elapsed_days=elapsed,
                is_eligible=condition_passed,
                eligible_from_date=condition_eligible_from,
                message=(
                    f"Member has been enrolled for {elapsed} days. "
                    f"Required: {req_days} days for {canonical_condition}."
                ),
            )

        # 4. Determine result
        rejection_reasons: list[RejectionReason] = []
        user_message = ""
        is_valid = True

        if not initial_passed:
            is_valid = False
            rejection_reasons.append(RejectionReason.WAITING_PERIOD)
            user_message = (
                f"The initial waiting period of {initial_days} days has not been completed. "
                f"Your policy was effective from {member.join_date.isoformat()}. "
                f"You will be eligible for claims from {initial_eligible_from.isoformat()}."
            )
        elif not condition_passed and condition_check is not None:
            is_valid = False
            rejection_reasons.append(RejectionReason.WAITING_PERIOD)
            user_message = (
                f"This claim is for {diagnosis}, which has a {condition_check.required_days}-day "
                f"waiting period. Your policy was effective from {member.join_date.isoformat()} "
                f"and your treatment was on {treatment_date.isoformat()} — only "
                f"{condition_check.elapsed_days} days have passed. "
                f"You will be eligible for {canonical_condition}-related claims from "
                f"{condition_eligible_from.isoformat()}."
            )

        # 5. Build checks list (initial + condition)
        checks: list[WaitingPeriodStatus] = []
        checks.append(
            WaitingPeriodStatus(
                condition="initial",
                required_days=initial_days,
                elapsed_days=(treatment_date - member.join_date).days if member.join_date else 0,
                is_eligible=initial_passed,
                eligible_from_date=initial_eligible_from,
            )
        )
        if condition_check is not None:
            checks.append(condition_check)

        return MemberValidationResult(
            is_valid=is_valid,
            member_found=True,
            member_id=member_id,
            member_name=member.name,
            relationship=str(member.relationship.value) if hasattr(member.relationship, "value") else str(member.relationship),
            join_date=member.join_date,
            waiting_period_checks=checks,
            initial_waiting_passed=initial_passed,
            condition_waiting_passed=condition_passed,
            days_until_eligible=days_until_eligible,
            rejection_reasons=rejection_reasons,
            user_message=user_message,
            confidence=1.0,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_waiting(
        self, join_date: date | None, treatment_date: date, required_days: int
    ) -> tuple[bool, date | None]:
        """Check if enough days have passed since join_date.

        Returns (passed, eligible_from_date). If join_date is None, we assume
        passed (no info).
        """
        if join_date is None:
            return True, None
        elapsed = (treatment_date - join_date).days
        if elapsed >= required_days:
            return True, None
        eligible_from = join_date + timedelta(days=required_days)
        return False, eligible_from

    def _get_diagnosis(self, state: AgentState) -> str | None:
        """Pull the diagnosis from extracted documents."""
        extracted = state.get("extracted_documents", []) or []
        # Find the first document with a diagnosis
        for doc_dict in extracted:
            if isinstance(doc_dict, dict):
                d = doc_dict.get("diagnosis")
                if d:
                    return d
        return None


# ----------------------------------------------------------------------
# LangGraph node wrapper
# ----------------------------------------------------------------------

def make_member_validation_node(policy: Policy):
    agent = MemberValidationAgent(policy)

    def member_validation_node(state: AgentState) -> dict:
        from agents.core.enums import ComponentFailure
        sim = state.get("simulate_component_failure")
        if sim == ComponentFailure.MEMBER_VALIDATION:
            started = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.MEMBER_VALIDATION,
                status=AgentStatus.FAILED,
                started_at=started,
                completed_at=started,
                duration_ms=0.0,
                confidence_contribution=0.0,
                error="Simulated component failure (TC011)",
            )
            failed = MemberValidationResult(
                is_valid=True,  # best-effort — assume valid so pipeline continues
                member_found=True,
                member_id=state["claim_input"].member_id,
                confidence=0.5,
                user_message="",
                ops_notes=["Member validation skipped due to failure"],
            )
            return {
                "member_validation": failed.model_dump(mode="json"),
                "trace": [trace],
                "errors": state.get("errors", []) + ["MemberValidation: simulated failure"],
            }

        started = datetime.now(timezone.utc)
        try:
            result = agent.run(state)
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.MEMBER_VALIDATION,
                status=AgentStatus.SUCCESS,
                started_at=started,
                completed_at=completed,
                duration_ms=(completed - started).total_seconds() * 1000,
                confidence_contribution=result.confidence,
                input_summary={
                    "member_id": state["claim_input"].member_id,
                    "treatment_date": str(state["claim_input"].treatment_date),
                },
                output_summary={
                    "is_valid": result.is_valid,
                    "rejection_reasons": [r.value for r in result.rejection_reasons],
                    "days_until_eligible": result.days_until_eligible,
                },
                notes=[f"member found: {result.member_found}",
                       f"initial: {result.initial_waiting_passed}",
                       f"condition: {result.condition_waiting_passed}"] + result.user_message.split(". "),
            )
            return {
                "member_validation": result.model_dump(mode="json"),
                "trace": [trace],
            }
        except Exception as e:
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.MEMBER_VALIDATION,
                status=AgentStatus.FAILED,
                started_at=started,
                completed_at=completed,
                duration_ms=0.0,
                confidence_contribution=0.0,
                error=str(e),
            )
            return {
                "member_validation": {},
                "trace": [trace],
                "errors": state.get("errors", []) + [f"MemberValidation failed: {e}"],
            }

    return member_validation_node
