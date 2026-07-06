"""
LangGraph state — the shared state passed between all agents in the pipeline.

In LangGraph, every node in the graph receives a state, mutates it (or returns
a partial update), and the framework merges it. The state is the "spine" of
the entire pipeline.

Why a TypedDict and not a Pydantic model? LangGraph's StateGraph is built
around TypedDicts because:
  1. The reducer pattern (`add_messages`, `operator.add`, etc.) requires dict-like mutation
  2. Pydantic's immutability fights with LangGraph's partial-update model
  3. LangGraph validates the state at runtime via reducers

We keep Pydantic models for the agent's I/O (strict contracts) but use
TypedDict for the in-flight state (flexible, mutatable).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import uuid4

from typing_extensions import TypedDict

from agents.core.domain import ClaimInput
from agents.core.enums import ComponentFailure
from agents.core.trace import AgentTrace


def _merge_traces(existing: list[AgentTrace], new: list[AgentTrace]) -> list[AgentTrace]:
    """Reducer for the trace: append new traces, don't overwrite.

    Every agent returns its own trace (a list with 1 element). The reducer
    concatenates them, so by the end of the pipeline, state['trace'] contains
    the full audit trail.
    """
    return (existing or []) + (new or [])


class AgentState(TypedDict, total=False):
    """The complete state of a claim as it flows through the pipeline.

    Every field is optional (`total=False`) so agents can populate them
    progressively. The orchestrator reads `decision` at the end to return
    the final result.
    """
    # ---- Identity ----
    claim_id: str
    claim_input: ClaimInput

    # ---- Failure simulation (TC011) ----
    simulate_component_failure: ComponentFailure | None

    # ---- Intermediate agent outputs ----
    document_verification: dict[str, Any]       # serialized DocumentVerificationResult
    extracted_documents: list[dict[str, Any]]   # serialized list of ExtractedDocument
    member_validation: dict[str, Any]           # serialized MemberValidationResult
    policy_evaluation: dict[str, Any]           # serialized PolicyEvaluation
    fraud_evaluation: dict[str, Any]            # serialized FraudEvaluation
    decision: dict[str, Any]                    # serialized Decision

    # ---- Trace (uses reducer to append) ----
    trace: Annotated[list[AgentTrace], _merge_traces]

    # ---- System-level ----
    errors: list[str]                  # any uncaught errors (do not crash the pipeline)
    pipeline_started_at: datetime
    pipeline_completed_at: datetime | None
    pipeline_status: str               # "running" | "completed" | "failed_early"


def make_initial_state(claim_input: ClaimInput) -> AgentState:
    """Build the initial state for a new claim.

    Generates a claim_id if not provided, sets pipeline_started_at, and
    initializes trace as empty.
    """
    claim_id = claim_input.claim_id or f"CLM_{uuid4().hex[:8].upper()}"
    return {
        "claim_id": claim_id,
        "claim_input": claim_input,
        "simulate_component_failure": claim_input.simulate_component_failure,
        "document_verification": {},
        "extracted_documents": [],
        "member_validation": {},
        "policy_evaluation": {},
        "fraud_evaluation": {},
        "decision": {},
        "trace": [],
        "errors": [],
        "pipeline_started_at": datetime.now(timezone.utc),
        "pipeline_completed_at": None,
        "pipeline_status": "running",
    }
