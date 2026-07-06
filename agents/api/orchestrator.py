"""
LangGraph orchestrator — wires the 6 agents into a state machine.

The graph structure:
                       ┌─────────────────────┐
                       │   START             │
                       └──────────┬──────────┘
                                  ▼
                       ┌─────────────────────┐
                       │ DocumentVerification│
                       └──────────┬──────────┘
                                  ▼
                       ┌─────────────────────┐
                       │ DocumentExtraction  │
                       └──────────┬──────────┘
                                  ▼
                       ┌─────────────────────┐
                       │ MemberValidation    │
                       └──────────┬──────────┘
                                  ▼
                       ┌─────────────────────┐
                       │ PolicyRules         │
                       └──────────┬──────────┘
                                  ▼
                       ┌─────────────────────┐
                       │ FraudDetection      │
                       └──────────┬──────────┘
                                  ▼
                       ┌─────────────────────┐
                       │ Decision            │
                       └──────────┬──────────┘
                                  ▼
                       ┌─────────────────────┐
                       │     END             │
                       └─────────────────────┘

Why a linear graph? Each agent's output is the next agent's input.
The decision is the final synthesis.

DESIGN NOTE
===========
DocumentVerification runs FIRST. If it fails, downstream agents can
short-circuit (they'll see an empty state). This is the "fail fast" pattern.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.core.enums import AgentName, AgentStatus
from agents.core.policy_loader import get_policy
from agents.core.state import AgentState, make_initial_state
from agents.core.trace import AgentTrace, ClaimTrace
from agents.decision.agent import make_decision_node
from agents.document_extraction.agent import make_document_extraction_node
from agents.document_verification.agent import make_document_verification_node
from agents.fraud_detection.agent import make_fraud_detection_node
from agents.member_validation.agent import make_member_validation_node
from agents.policy_rules.agent import make_policy_rules_node


def should_continue_after_verification(state: AgentState) -> str:
    """Decide whether to continue the pipeline after DocumentVerification.

    If verification failed (stop_processing=True), we skip downstream agents
    and go straight to Decision (which will surface the verification error).
    """
    doc_ver = state.get("document_verification", {}) or {}
    if not doc_ver.get("is_valid", True):
        # Verification failed — skip extraction/validation/policy/fraud, go to decision
        return "decision"
    return "continue"


def build_orchestrator():
    """Build and return the compiled LangGraph orchestrator.

    Returns a compiled StateGraph that can be invoked with `.invoke(state)`.
    """
    policy = get_policy()

    # Create node functions
    doc_ver_node = make_document_verification_node(policy)
    doc_ext_node = make_document_extraction_node(policy)
    mem_val_node = make_member_validation_node(policy)
    pol_rules_node = make_policy_rules_node(policy)
    fraud_node = make_fraud_detection_node(policy)
    decision_node = make_decision_node()

    # Build the graph
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("document_verification", doc_ver_node)
    graph.add_node("document_extraction", doc_ext_node)
    graph.add_node("member_validation", mem_val_node)
    graph.add_node("policy_rules", pol_rules_node)
    graph.add_node("fraud_detection", fraud_node)
    graph.add_node("decision", decision_node)

    # Linear edges
    graph.add_edge(START, "document_verification")
    graph.add_edge("document_extraction", "member_validation")
    graph.add_edge("member_validation", "policy_rules")
    graph.add_edge("policy_rules", "fraud_detection")
    graph.add_edge("fraud_detection", "decision")
    graph.add_edge("decision", END)

    # Conditional edge after document_verification
    # If verification failed → skip to decision
    # If verification passed → continue to extraction
    graph.add_conditional_edges(
        "document_verification",
        should_continue_after_verification,
        {
            "continue": "document_extraction",
            "decision": "decision",
        },
    )

    return graph.compile()


def run_claim(claim_input) -> ClaimTrace:
    """Run a single claim through the full pipeline.

    Args:
        claim_input: A ClaimInput instance.

    Returns:
        A ClaimTrace with the full audit trail and final decision.
    """
    orchestrator = build_orchestrator()
    state = make_initial_state(claim_input)

    # Add a synthetic orchestrator trace entry at the start
    started = datetime.now(timezone.utc)
    state["trace"] = [
        AgentTrace(
            agent_name=AgentName.ORCHESTRATOR,
            status=AgentStatus.SUCCESS,
            started_at=started,
            completed_at=started,
            duration_ms=0.0,
            confidence_contribution=1.0,
            input_summary={"claim_id": state["claim_id"]},
            output_summary={"starting_pipeline": True},
            notes=[f"Claim {state['claim_id']} entered pipeline"],
        )
    ]

    # Run the graph
    final_state = orchestrator.invoke(state)

    # Build the ClaimTrace
    completed = datetime.now(timezone.utc)
    decision = final_state.get("decision", {}) or {}
    all_traces = final_state.get("trace", [])

    # Add the final orchestrator entry
    all_traces.append(
        AgentTrace(
            agent_name=AgentName.ORCHESTRATOR,
            status=AgentStatus.SUCCESS,
            started_at=started,
            completed_at=completed,
            duration_ms=(completed - started).total_seconds() * 1000,
            confidence_contribution=1.0,
            input_summary={"claim_id": state["claim_id"]},
            output_summary={
                "final_decision": decision.get("decision", "UNKNOWN"),
                "approved_amount": decision.get("approved_amount", 0),
            },
            notes=[f"Pipeline completed in {(completed - started).total_seconds() * 1000:.0f}ms"],
        )
    )

    return ClaimTrace(
        claim_id=state["claim_id"],
        policy_id=claim_input.policy_id,
        member_id=claim_input.member_id,
        claim_category=str(claim_input.claim_category.value),
        submitted_at=claim_input.submitted_at,
        completed_at=completed,
        total_duration_ms=(completed - started).total_seconds() * 1000,
        agent_traces=all_traces,
        overall_confidence=decision.get("confidence_score", 0.0),
        final_decision=decision.get("decision"),
    )


# Singleton orchestrator (compiled once, reused for every claim)
_orchestrator = None


def get_orchestrator():
    """Get or build the singleton orchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = build_orchestrator()
    return _orchestrator
