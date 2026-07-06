"""
FraudDetectionAgent — implementation.

Detects claim patterns that may indicate fraud. Does NOT reject — it
routes to MANUAL_REVIEW for human investigation.

The agent is intentionally conservative: false positives (a legit claim
flagged for review) are FAR less costly than false negatives (a fraudulent
claim auto-approved).

Test cases:
  TC009 — Same-day claims = 4 > 2 limit → MANUAL_REVIEW
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.core.domain import Policy
from agents.core.enums import (
    AgentName,
    AgentStatus,
    FraudSignal,
)
from agents.core.state import AgentState
from agents.core.trace import AgentTrace
from agents.fraud_detection.schemas import (
    FraudDetectionInput,
    FraudEvaluation,
)


class FraudDetectionAgent:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    def run(self, state: AgentState) -> FraudEvaluation:
        claim_input = state["claim_input"]
        thresholds = self.policy.fraud_thresholds

        # ---- Count same-day claims (from history + current) ----
        same_day_count = self._count_same_day_claims(state)
        monthly_count = self._count_monthly_claims(state)

        signals: list[FraudSignal] = []
        notes: list[str] = []

        # ---- Check 1: Same-day limit ----
        if same_day_count > thresholds.same_day_claims_limit:
            signals.append(FraudSignal.SAME_DAY_LIMIT_EXCEEDED)
            notes.append(
                f"Same-day claims ({same_day_count}) exceed limit ({thresholds.same_day_claims_limit})"
            )

        # ---- Check 2: Monthly limit ----
        if monthly_count > thresholds.monthly_claims_limit:
            signals.append(FraudSignal.MONTHLY_LIMIT_EXCEEDED)
            notes.append(
                f"Monthly claims ({monthly_count}) exceed limit ({thresholds.monthly_claims_limit})"
            )

        # ---- Check 3: High-value claim ----
        is_high_value = claim_input.claimed_amount > thresholds.high_value_claim_threshold
        if is_high_value:
            signals.append(FraudSignal.HIGH_VALUE_CLAIM)
            notes.append(
                f"Claimed amount (₹{claim_input.claimed_amount:,.0f}) exceeds "
                f"high-value threshold (₹{thresholds.high_value_claim_threshold:,.0f})"
            )

        # ---- Check 4: Document alterations (warnings from extraction) ----
        doc_warnings = self._get_document_warnings(state)
        alteration_keywords = ["alteration", "cancellation", "duplicate stamp", "crossed out"]
        for w in doc_warnings:
            w_lower = w.lower()
            for kw in alteration_keywords:
                if kw in w_lower:
                    signals.append(FraudSignal.DOCUMENT_ALTERATION)
                    notes.append(f"Document alteration signal: {w}")
                    break

        # ---- Compute fraud score ----
        fraud_score = self._compute_fraud_score(signals)

        # ---- Determine if manual review is required ----
        # Hard signals (same-day, monthly, alterations) ALWAYS trigger review,
        # because the cost of a false positive (manual review) is much lower
        # than a false negative (fraudulent claim auto-approved).
        hard_signals = {
            FraudSignal.SAME_DAY_LIMIT_EXCEEDED,
            FraudSignal.MONTHLY_LIMIT_EXCEEDED,
            FraudSignal.DOCUMENT_ALTERATION,
            FraudSignal.SUSPECTED_FAKE_DOCUMENT,
        }
        triggered_hard = bool(hard_signals & set(signals))

        requires_review = (
            triggered_hard
            or fraud_score > thresholds.fraud_score_manual_review_threshold
            or (is_high_value and claim_input.claimed_amount > thresholds.auto_manual_review_above)
        )

        # ---- Build ops-facing message ----
        if signals:
            user_message = (
                f"Claim flagged for review. Signals: {', '.join(s.value for s in signals)}. "
                f"Fraud score: {fraud_score:.2f}. "
                f"Same-day claims: {same_day_count}, Monthly claims: {monthly_count}."
            )
        else:
            user_message = ""

        return FraudEvaluation(
            fraud_score=fraud_score,
            signals_triggered=signals,
            requires_manual_review=requires_review,
            notes=notes,
            confidence=0.95,
            user_message=user_message,
            same_day_claims_count=same_day_count,
            monthly_claims_count=monthly_count,
            claimed_amount=claim_input.claimed_amount,
            is_high_value=is_high_value,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _count_same_day_claims(self, state: AgentState) -> int:
        """Count claims for this member on the same treatment_date.

        Includes the current claim (count = 1 if no history).
        """
        claim_input = state["claim_input"]
        treatment_date = claim_input.treatment_date

        count = 1  # the current claim
        for h in claim_input.claims_history:
            if h.date == treatment_date:
                count += 1
        return count

    def _count_monthly_claims(self, state: AgentState) -> int:
        """Count claims for this member in the same calendar month."""
        claim_input = state["claim_input"]
        treatment_date = claim_input.treatment_date

        count = 1  # the current claim
        for h in claim_input.claims_history:
            if h.date.year == treatment_date.year and h.date.month == treatment_date.month:
                count += 1
        return count

    def _get_document_warnings(self, state: AgentState) -> list[str]:
        """Aggregate warnings from extracted documents."""
        extracted = state.get("extracted_documents", []) or []
        warnings = []
        for doc_dict in extracted:
            if isinstance(doc_dict, dict):
                for w in doc_dict.get("warnings", []):
                    warnings.append(w)
        return warnings

    def _compute_fraud_score(self, signals: list[FraudSignal]) -> float:
        """Weighted sum of signals, capped at 1.0."""
        weights = {
            FraudSignal.SAME_DAY_LIMIT_EXCEEDED: 0.4,
            FraudSignal.MONTHLY_LIMIT_EXCEEDED: 0.3,
            FraudSignal.HIGH_VALUE_CLAIM: 0.2,
            FraudSignal.DOCUMENT_ALTERATION: 0.5,
            FraudSignal.SUSPECTED_FAKE_DOCUMENT: 0.6,
        }
        score = sum(weights.get(s, 0) for s in signals)
        return min(score, 1.0)


# ----------------------------------------------------------------------
# LangGraph node wrapper
# ----------------------------------------------------------------------

def make_fraud_detection_node(policy: Policy):
    agent = FraudDetectionAgent(policy)

    def fraud_detection_node(state: AgentState) -> dict:
        from agents.core.enums import ComponentFailure
        sim = state.get("simulate_component_failure")
        if sim == ComponentFailure.FRAUD_DETECTION:
            started = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.FRAUD_DETECTION,
                status=AgentStatus.FAILED,
                started_at=started,
                completed_at=started,
                duration_ms=0.0,
                confidence_contribution=0.0,
                error="Simulated component failure (TC011)",
            )
            # Best-effort: assume no fraud if we can't compute
            return {
                "fraud_evaluation": FraudEvaluation(
                    fraud_score=0.0,
                    signals_triggered=[],
                    requires_manual_review=False,
                    confidence=0.5,
                ).model_dump(mode="json"),
                "trace": [trace],
                "errors": state.get("errors", []) + ["FraudDetection: simulated failure"],
            }

        started = datetime.now(timezone.utc)
        try:
            result = agent.run(state)
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.FRAUD_DETECTION,
                status=AgentStatus.SUCCESS,
                started_at=started,
                completed_at=completed,
                duration_ms=(completed - started).total_seconds() * 1000,
                confidence_contribution=result.confidence,
                input_summary={
                    "claimed_amount": result.claimed_amount,
                    "same_day_count": result.same_day_claims_count,
                    "monthly_count": result.monthly_claims_count,
                },
                output_summary={
                    "fraud_score": result.fraud_score,
                    "signals": [s.value for s in result.signals_triggered],
                    "requires_review": result.requires_manual_review,
                },
                notes=result.notes,
            )
            return {
                "fraud_evaluation": result.model_dump(mode="json"),
                "trace": [trace],
            }
        except Exception as e:
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.FRAUD_DETECTION,
                status=AgentStatus.FAILED,
                started_at=started,
                completed_at=completed,
                duration_ms=0.0,
                confidence_contribution=0.0,
                error=str(e),
            )
            return {
                "fraud_evaluation": {},
                "trace": [trace],
                "errors": state.get("errors", []) + [f"FraudDetection failed: {e}"],
            }

    return fraud_detection_node
