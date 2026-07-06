"""
DecisionAgent — implementation.

The final synthesis. Takes all upstream signals and produces the final
DecisionType + approved amount + confidence + user message.

Decision priority (highest first):
  1. Document verification failure → REJECTED (with verification's message)
  2. Fraud signal requiring manual review → MANUAL_REVIEW
  3. Policy rejections → REJECTED
  4. Member validation failures → REJECTED
  5. Policy evaluation approved < claimed → PARTIAL
  6. Otherwise → APPROVED

Graceful failure (TC011):
  - If any agent failed, the final confidence is reduced by 0.1 per failure
  - The pipeline still produces a decision (best-effort)
  - ops_notes list which agents failed
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agents.core.enums import (
    AgentName,
    AgentStatus,
    DecisionType,
    RejectionReason,
)
from agents.core.state import AgentState
from agents.core.trace import AgentTrace
from agents.decision.schemas import Decision, DecisionInput


class DecisionAgent:
    """Synthesizes all upstream results into a final Decision."""

    def __init__(self) -> None:
        pass

    def run(self, state: AgentState) -> Decision:
        claim_input = state["claim_input"]
        doc_ver = state.get("document_verification", {}) or {}
        member_val = state.get("member_validation", {}) or {}
        policy_eval = state.get("policy_evaluation", {}) or {}
        fraud_eval = state.get("fraud_evaluation", {}) or {}
        errors = state.get("errors", []) or []

        # Identify failed agents from errors
        failed_agents = self._identify_failed_agents(errors)

        # ---- Decision priority chain ----
        decision, approved_amount, rejection_reasons, requires_review, user_message, ops_notes = (
            self._synthesize(
                doc_ver=doc_ver,
                member_val=member_val,
                policy_eval=policy_eval,
                fraud_eval=fraud_eval,
                claimed_amount=claim_input.claimed_amount,
                failed_agents=failed_agents,
            )
        )

        # ---- Confidence calculation ----
        upstream_confidences = [
            doc_ver.get("confidence", 0.9),
            member_val.get("confidence", 0.9),
            policy_eval.get("confidence", 0.9),
            fraud_eval.get("confidence", 0.9),
        ]
        # Filter out None
        upstream_confidences = [c for c in upstream_confidences if c is not None]
        base_conf = sum(upstream_confidences) / len(upstream_confidences) if upstream_confidences else 0.9
        penalty = 0.1 * len(failed_agents)
        confidence = max(0.5, base_conf - penalty)

        # ---- If graceful failure, append note ----
        # When any agent fails, we ALWAYS flag for manual review (TC011).
        # The reason: we can't trust a decision made on partial data, regardless
        # of whether it's APPROVED or REJECTED.
        if failed_agents:
            ops_notes.append(
                f"Note: {len(failed_agents)} agent(s) failed ({', '.join(failed_agents)}). "
                f"Decision is best-effort. Manual review recommended."
            )
            requires_review = True

        # ---- Next steps for the member ----
        next_steps = self._build_next_steps(decision, rejection_reasons, user_message)

        return Decision(
            decision=decision,
            approved_amount=approved_amount,
            rejection_reasons=rejection_reasons,
            confidence_score=round(confidence, 2),
            user_message=user_message,
            ops_notes=ops_notes,
            requires_manual_review=requires_review,
            next_steps=next_steps,
        )

    # ------------------------------------------------------------------
    # Decision synthesis
    # ------------------------------------------------------------------

    def _synthesize(
        self,
        doc_ver: dict,
        member_val: dict,
        policy_eval: dict,
        fraud_eval: dict,
        claimed_amount: float,
        failed_agents: list[str],
    ) -> tuple:
        """Apply the decision priority chain.

        Returns: (decision, approved_amount, rejection_reasons, requires_review, user_message, ops_notes)
        """
        ops_notes: list[str] = []

        # ---- 1. Document verification failure (highest priority) ----
        if doc_ver and not doc_ver.get("is_valid", True):
            return (
                DecisionType.REJECTED,
                0.0,
                [RejectionReason.NOT_COVERED],  # verification error
                False,
                doc_ver.get("user_message", "Document verification failed."),
                ops_notes,
            )

        # ---- 2. Fraud signal requiring manual review ----
        if fraud_eval and fraud_eval.get("requires_manual_review", False):
            signals = fraud_eval.get("signals_triggered", [])
            return (
                DecisionType.MANUAL_REVIEW,
                0.0,
                [],
                True,
                "Your claim is being reviewed by our team and will be processed within 48 hours. You will receive an update by email.",
                [f"Fraud signals: {signals}", f"Score: {fraud_eval.get('fraud_score', 0):.2f}"],
            )

        # ---- 3. Policy rejections ----
        if policy_eval and not policy_eval.get("is_valid", True):
            reasons_str = policy_eval.get("rejection_reasons", [])
            reasons = [RejectionReason(r) for r in reasons_str if r in [e.value for e in RejectionReason]]
            return (
                DecisionType.REJECTED,
                0.0,
                reasons,
                False,
                policy_eval.get("user_message", "This claim has been rejected per policy rules."),
                ops_notes,
            )

        # ---- 4. Member validation failures ----
        if member_val and not member_val.get("is_valid", True):
            reasons_str = member_val.get("rejection_reasons", [])
            reasons = [RejectionReason(r) for r in reasons_str if r in [e.value for e in RejectionReason]]
            return (
                DecisionType.REJECTED,
                0.0,
                reasons,
                False,
                member_val.get("user_message", "This claim has been rejected due to member validation issues."),
                ops_notes,
            )

        # ---- 5 & 6. APPROVED or PARTIAL based on policy evaluation ----
        approved = policy_eval.get("approved_amount", 0.0) if policy_eval else 0.0
        if approved <= 0:
            return (
                DecisionType.REJECTED,
                0.0,
                [RejectionReason.NOT_COVERED],
                False,
                "This claim could not be approved. Please contact support for details.",
                ops_notes,
            )

        if approved < claimed_amount:
            # PARTIAL only if there are line-item rejections.
            # If the full bill is approved but reduced by co-pay/discount, it's APPROVED.
            line_items = policy_eval.get("line_item_decisions", [])
            any_rejected = any(not li.get("is_approved", True) for li in line_items)
            if any_rejected:
                return (
                    DecisionType.PARTIAL,
                    approved,
                    [],
                    False,
                    self._build_partial_message(approved, policy_eval, claimed_amount),
                    ops_notes + self._build_ops_notes_for_partial(policy_eval),
                )
            # Approved < claimed but no line items rejected — could be sub-limit cap, etc.
            # Treat as APPROVED with the reduced amount.

        # APPROVED
        return (
            DecisionType.APPROVED,
            approved,
            [],
            False,
            self._build_approved_message(approved, claimed_amount, policy_eval),
            ops_notes,
        )

    # ------------------------------------------------------------------
    # User-facing messages
    # ------------------------------------------------------------------

    def _build_approved_message(
        self, approved: float, claimed: float, policy_eval: dict
    ) -> str:
        copay = policy_eval.get("copay_amount", 0)
        network_disc = policy_eval.get("network_discount_amount", 0)
        parts = [f"Your claim for ₹{claimed:,.0f} has been approved."]
        if network_disc > 0:
            parts.append(
                f"A network discount of ₹{network_disc:,.0f} was applied."
            )
        if copay > 0:
            parts.append(
                f"A co-pay of ₹{copay:,.0f} has been applied per your policy."
            )
        parts.append(f"₹{approved:,.0f} will be reimbursed within 5-7 business days.")
        return " ".join(parts)

    def _build_partial_message(
        self, approved: float, policy_eval: dict, claimed: float
    ) -> str:
        line_items = policy_eval.get("line_item_decisions", [])
        if line_items:
            approved_items = [li for li in line_items if li.get("is_approved")]
            rejected_items = [li for li in line_items if not li.get("is_approved")]
            parts = ["Your claim has been partially approved."]
            if approved_items:
                approved_str = ", ".join(
                    f"{li['description']} ₹{li['amount']:,.0f}"
                    for li in approved_items
                )
                parts.append(f"Approved: {approved_str}.")
            if rejected_items:
                rejected_str = ", ".join(
                    f"{li['description']} ₹{li['amount']:,.0f} ({li.get('reason', 'excluded')})"
                    for li in rejected_items
                )
                parts.append(f"Rejected: {rejected_str}.")
            parts.append(f"Total approved: ₹{approved:,.0f}.")
            return " ".join(parts)
        return f"Your claim has been partially approved for ₹{approved:,.0f} (claimed ₹{claimed:,.0f})."

    def _build_ops_notes_for_partial(self, policy_eval: dict) -> list[str]:
        notes = []
        rejected_items = [
            li for li in policy_eval.get("line_item_decisions", [])
            if not li.get("is_approved")
        ]
        for li in rejected_items:
            notes.append(
                f"Rejected line item: {li['description']} — {li.get('exclusion_matched', 'excluded')}"
            )
        return notes

    def _build_next_steps(
        self,
        decision: DecisionType,
        rejection_reasons: list[RejectionReason],
        user_message: str,
    ) -> list[str]:
        if decision == DecisionType.APPROVED:
            return ["No action required. Reimbursement will be processed within 5-7 business days."]
        if decision == DecisionType.PARTIAL:
            return [
                "The approved portion will be reimbursed.",
                "For the rejected portion, please contact support if you believe this is in error.",
            ]
        if decision == DecisionType.MANUAL_REVIEW:
            return ["No action required. Our team will review and respond within 48 hours."]
        # REJECTED
        steps = []
        if RejectionReason.WAITING_PERIOD in rejection_reasons:
            steps.append("Wait until your waiting period is complete, then resubmit.")
        if RejectionReason.PRE_AUTH_MISSING in rejection_reasons:
            steps.append("Request pre-authorization and resubmit with the pre-auth reference number.")
        if RejectionReason.PER_CLAIM_EXCEEDED in rejection_reasons:
            steps.append("Claims above the per-claim limit are not eligible. Please contact HR for high-value claims.")
        if RejectionReason.EXCLUDED_CONDITION in rejection_reasons:
            steps.append("Excluded conditions are not covered under your policy. Please check your policy document.")
        if not steps:
            steps.append("Please contact support for more information about this rejection.")
        return steps

    # ------------------------------------------------------------------
    # Failed agent detection
    # ------------------------------------------------------------------

    def _identify_failed_agents(self, errors: list[str]) -> list[str]:
        """Identify which agents failed from the error list."""
        failed = []
        for e in errors:
            for agent_name in ["DocumentVerification", "DocumentExtraction", "MemberValidation",
                              "PolicyRules", "FraudDetection"]:
                if agent_name in e:
                    if agent_name not in failed:
                        failed.append(agent_name)
        return failed


# ----------------------------------------------------------------------
# LangGraph node wrapper
# ----------------------------------------------------------------------

def make_decision_node():
    agent = DecisionAgent()

    def decision_node(state: AgentState) -> dict:
        started = datetime.now(timezone.utc)
        try:
            result = agent.run(state)
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.DECISION,
                status=AgentStatus.SUCCESS,
                started_at=started,
                completed_at=completed,
                duration_ms=(completed - started).total_seconds() * 1000,
                confidence_contribution=result.confidence_score,
                input_summary={
                    "claimed_amount": state["claim_input"].claimed_amount,
                    "errors": state.get("errors", []),
                },
                output_summary={
                    "decision": result.decision.value,
                    "approved_amount": result.approved_amount,
                    "confidence": result.confidence_score,
                    "requires_review": result.requires_manual_review,
                },
                notes=result.ops_notes + [result.user_message],
            )
            return {
                "decision": result.model_dump(mode="json"),
                "trace": [trace],
                "pipeline_status": "completed",
                "pipeline_completed_at": completed,
            }
        except Exception as e:
            completed = datetime.now(timezone.utc)
            trace = AgentTrace(
                agent_name=AgentName.DECISION,
                status=AgentStatus.FAILED,
                started_at=started,
                completed_at=completed,
                duration_ms=0.0,
                confidence_contribution=0.0,
                error=str(e),
            )
            # Even the decision agent can fail — return a safe default
            safe = Decision(
                decision=DecisionType.MANUAL_REVIEW,
                approved_amount=0.0,
                confidence_score=0.0,
                user_message="Your claim could not be processed automatically. Our team will review it within 48 hours.",
                requires_manual_review=True,
                ops_notes=[f"Decision agent failed: {e}"],
            )
            return {
                "decision": safe.model_dump(mode="json"),
                "trace": [trace],
                "errors": state.get("errors", []) + [f"Decision failed: {e}"],
                "pipeline_status": "failed_early",
                "pipeline_completed_at": completed,
            }

    return decision_node
