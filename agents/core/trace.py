"""
Trace and observability models.

The trace is the system's "black box flight recorder" — it captures every step
so an operator (or auditor) can reconstruct exactly why any claim got any decision.

This is the most important non-agent module in the system. If the trace is
broken, the system is not explainable, and Plum's ops team cannot trust it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from agents.core.enums import AgentName, AgentStatus


class AgentTrace(BaseModel):
    """A single step in the pipeline — what one agent did.

    Every agent emits one of these when it completes (success, fail, or skip).
    The trace is appended to AgentState.trace and serialized to the API response.
    """
    agent_name: AgentName
    status: AgentStatus
    started_at: datetime
    completed_at: datetime
    duration_ms: float = Field(0.0, description="Wall-clock time spent in this agent")
    confidence_contribution: float = Field(
        1.0,
        ge=0.0,
        le=1.0,
        description=(
            "How much this agent's confidence contributes to the final score. "
            "Failed or skipped agents contribute 0 (penalizing total confidence)."
        ),
    )
    input_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Redacted summary of what the agent received (no PII/PHI payloads)",
    )
    output_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Redacted summary of what the agent produced",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Free-form notes — e.g. 'matched waiting period rule'",
    )
    error: str | None = Field(
        None,
        description="Error message if the agent failed. None if successful.",
    )
    warnings: list[str] = Field(default_factory=list)


class ClaimTrace(BaseModel):
    """The full trace for a claim — list of agent traces + meta."""
    claim_id: str
    policy_id: str
    member_id: str
    claim_category: str
    submitted_at: datetime
    completed_at: datetime | None = None
    total_duration_ms: float = 0.0
    agent_traces: list[AgentTrace] = Field(default_factory=list)
    overall_confidence: float = 0.0
    final_decision: str | None = None

    def to_markdown(self) -> str:
        """Render the trace as a human-readable Markdown table.

        Used by the eval report generator and the Streamlit trace view.
        """
        lines: list[str] = []
        lines.append(f"# Claim Trace — `{self.claim_id}`")
        lines.append("")
        lines.append(f"- **Policy:** {self.policy_id}")
        lines.append(f"- **Member:** {self.member_id}")
        lines.append(f"- **Category:** {self.claim_category}")
        lines.append(f"- **Submitted:** {self.submitted_at.isoformat()}")
        if self.completed_at:
            lines.append(f"- **Completed:** {self.completed_at.isoformat()}")
        lines.append(f"- **Total duration:** {self.total_duration_ms:.1f} ms")
        lines.append(f"- **Final decision:** {self.final_decision or '(in progress)'}")
        lines.append(f"- **Confidence:** {self.overall_confidence:.2f}")
        lines.append("")
        lines.append("## Agent execution order")
        lines.append("")
        lines.append("| # | Agent | Status | Duration (ms) | Confidence | Notes |")
        lines.append("|---|-------|--------|---------------|------------|-------|")
        for i, t in enumerate(self.agent_traces, 1):
            notes_str = "; ".join(t.notes) if t.notes else ""
            if t.error:
                notes_str = f"ERROR: {t.error}"
            lines.append(
                f"| {i} | {t.agent_name.value} | {t.status.value} | {t.duration_ms:.1f} | "
                f"{t.confidence_contribution:.2f} | {notes_str} |"
            )
        return "\n".join(lines)
