"""
Plum Claims Pipeline — Streamlit UI.

Run with: streamlit run frontend/app.py

Features:
  - Submit a claim via form
  - See the decision (color-coded by type)
  - View the full agent trace
  - See specific user messages for rejections

Pages:
  1. Submit Claim — form for new claim
  2. Decision Review — see the result + trace
  3. Test Cases — run all 12 test cases from the assignment
  4. About — system info
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import requests
import streamlit as st

# Make the project importable
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Try to import the orchestrator directly (for local mode)
try:
    from agents.api.orchestrator import run_claim
    from agents.core.domain import ClaimHistoryItem, ClaimInput, DocumentInput
    from agents.core.enums import ClaimCategory, DocumentQuality, DocumentType
    LOCAL_MODE = True
except Exception as e:
    LOCAL_MODE = False
    st.error(f"Could not import orchestrator: {e}")


# ----------------------------------------------------------------------
# Page config
# ----------------------------------------------------------------------

st.set_page_config(
    page_title="Plum Claims Pipeline",
    page_icon="🩺",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

DECISION_COLORS = {
    "APPROVED": "🟢",
    "PARTIAL": "🟡",
    "REJECTED": "🔴",
    "MANUAL_REVIEW": "🟠",
}


def render_decision_card(decision: dict) -> None:
    """Render a color-coded decision card."""
    dec = decision.get("decision", "UNKNOWN")
    color = DECISION_COLORS.get(dec, "⚪")
    approved = decision.get("approved_amount", 0)
    confidence = decision.get("confidence_score", 0)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Decision", f"{color} {dec}")
    with col2:
        st.metric("Approved Amount", f"₹{approved:,.0f}")
    with col3:
        st.metric("Confidence", f"{confidence:.0%}")

    if decision.get("requires_manual_review"):
        st.warning("⚠️ This claim has been flagged for manual review.")

    if decision.get("user_message"):
        st.info(f"📝 **Message to member:**\n\n{decision['user_message']}")

    if decision.get("next_steps"):
        st.markdown("**Next steps:**")
        for step in decision["next_steps"]:
            st.markdown(f"- {step}")


def render_trace(agent_traces: list[dict]) -> None:
    """Render the agent trace as a timeline."""
    st.markdown("### 🔍 Agent Trace")
    for i, t in enumerate(agent_traces, 1):
        status_icon = "✅" if t["status"] == "SUCCESS" else "❌" if t["status"] == "FAILED" else "⏭️"
        with st.expander(
            f"{i}. {status_icon} **{t['agent']}** — {t['status']} ({t['duration_ms']:.0f}ms, conf {t['confidence']:.0%})"
        ):
            if t.get("error"):
                st.error(f"Error: {t['error']}")
            if t.get("notes"):
                for note in t["notes"]:
                    st.markdown(f"- {note}")


# ----------------------------------------------------------------------
# Page 1: Submit Claim
# ----------------------------------------------------------------------

def page_submit() -> None:
    st.title("📋 Submit a Claim")
    st.markdown("Submit a new health insurance claim for processing.")

    with st.form("claim_form"):
        col1, col2 = st.columns(2)
        with col1:
            member_id = st.text_input("Member ID", value="EMP001", help="e.g. EMP001, EMP005")
            claim_category = st.selectbox(
                "Claim Category",
                options=[c.value for c in ClaimCategory],
                index=0,
            )
            claimed_amount = st.number_input(
                "Claimed Amount (₹)",
                min_value=0,
                max_value=100000,
                value=1500,
                step=100,
            )
        with col2:
            treatment_date = st.date_input(
                "Treatment Date",
                value=date(2024, 11, 1),
                min_value=date(2020, 1, 1),
                max_value=date(2030, 12, 31),
            )
            hospital_name = st.text_input("Hospital Name (optional)", value="")

        st.markdown("### Documents")
        st.markdown("Add the documents uploaded for this claim.")

        num_docs = st.number_input("Number of documents", min_value=1, max_value=5, value=2)

        documents = []
        for i in range(num_docs):
            with st.expander(f"Document #{i+1}", expanded=(i < 2)):
                col_d1, col_d2 = st.columns(2)
                with col_d1:
                    doc_type = st.selectbox(
                        "Type",
                        options=[t.value for t in DocumentType if t.value != "UNKNOWN"],
                        key=f"doctype_{i}",
                    )
                    file_name = st.text_input("File name", value=f"doc_{i+1}.jpg", key=f"filename_{i}")
                with col_d2:
                    quality = st.selectbox(
                        "Quality",
                        options=[q.value for q in DocumentQuality],
                        key=f"quality_{i}",
                    )
                include_content = st.checkbox("Include pre-extracted content (test mode)", key=f"content_{i}")
                content = None
                if include_content:
                    if doc_type == "PRESCRIPTION":
                        content = {
                            "patient_name": st.text_input("Patient name", value="Rajesh Kumar", key=f"patient_{i}"),
                            "doctor_name": st.text_input("Doctor name", value="Dr. Arun Sharma", key=f"doctor_{i}"),
                            "doctor_registration": st.text_input("Doctor reg", value="KA/45678/2015", key=f"reg_{i}"),
                            "date": str(treatment_date),
                            "diagnosis": st.text_input("Diagnosis", value="Viral Fever", key=f"diag_{i}"),
                        }
                    elif doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL"):
                        content = {
                            "hospital_name": hospital_name or "City Clinic",
                            "patient_name": st.text_input("Patient name", value="Rajesh Kumar", key=f"patient_{i}"),
                            "date": str(treatment_date),
                            "total": claimed_amount,
                            "line_items": [
                                {"description": "Consultation Fee", "amount": claimed_amount},
                            ],
                        }
                documents.append({
                    "file_id": f"F{i+1:03d}",
                    "file_name": file_name,
                    "actual_type": doc_type,
                    "quality": quality,
                    "content": content,
                })

        submitted = st.form_submit_button("🚀 Submit Claim", use_container_width=True)

    if submitted:
        with st.spinner("Processing claim through 6-agent pipeline..."):
            try:
                # Build ClaimInput
                doc_inputs = [
                    DocumentInput(
                        file_id=d["file_id"],
                        file_name=d["file_name"],
                        actual_type=DocumentType(d["actual_type"]) if d["actual_type"] else None,
                        quality=DocumentQuality(d["quality"]),
                        content=d["content"],
                    )
                    for d in documents
                ]
                claim = ClaimInput(
                    member_id=member_id,
                    policy_id="PLUM_GHI_2024",
                    claim_category=ClaimCategory(claim_category),
                    treatment_date=treatment_date,
                    claimed_amount=claimed_amount,
                    hospital_name=hospital_name or None,
                    documents=doc_inputs,
                )
                trace = run_claim(claim)
                # Get final state with decision
                from agents.api.orchestrator import get_orchestrator
                from agents.core.state import make_initial_state
                state = make_initial_state(claim)
                state["trace"] = []
                final_state = get_orchestrator().invoke(state)
                decision = final_state.get("decision", {})

                st.session_state["last_decision"] = decision
                st.session_state["last_trace"] = trace

            except Exception as e:
                st.error(f"Error processing claim: {e}")
                return

    # Show result if we have one
    if "last_decision" in st.session_state and st.session_state["last_decision"]:
        st.markdown("---")
        st.markdown("## 📊 Decision")
        render_decision_card(st.session_state["last_decision"])
        if "last_trace" in st.session_state and st.session_state["last_trace"]:
            render_trace([
                {
                    "agent": t.agent_name.value,
                    "status": t.status.value,
                    "duration_ms": t.duration_ms,
                    "confidence": t.confidence_contribution,
                    "notes": t.notes,
                    "error": t.error,
                }
                for t in st.session_state["last_trace"].agent_traces
            ])


# ----------------------------------------------------------------------
# Page 2: Test Cases
# ----------------------------------------------------------------------

def page_test_cases() -> None:
    st.title("🧪 Test Cases")
    st.markdown("Run all 12 test cases from the Plum assignment through the pipeline.")

    if not LOCAL_MODE:
        st.error("Orchestrator not available. Cannot run test cases.")
        return

    if st.button("▶️ Run all 12 test cases", use_container_width=True):
        with open(ROOT / "data" / "test_cases.json") as f:
            cases = json.load(f)["test_cases"]

        results = []
        progress = st.progress(0, text="Running test cases...")
        for i, tc in enumerate(cases):
            progress.progress((i + 1) / len(cases), text=f"Running {tc['case_id']}...")
            inp = tc["input"]
            docs = [
                DocumentInput(
                    file_id=d["file_id"],
                    file_name=d.get("file_name"),
                    actual_type=DocumentType(d["actual_type"]) if d.get("actual_type") else None,
                    quality=DocumentQuality(d.get("quality", "GOOD")),
                    content=d.get("content"),
                    patient_name_on_doc=d.get("patient_name_on_doc"),
                )
                for d in inp.get("documents", [])
            ]
            history = [
                ClaimHistoryItem(
                    claim_id=h["claim_id"],
                    date=date.fromisoformat(h["date"]),
                    amount=h["amount"],
                    provider=h.get("provider"),
                )
                for h in inp.get("claims_history", [])
            ]
            sim = inp.get("simulate_component_failure")
            from agents.core.enums import ComponentFailure
            if sim is True:
                sim_failure = ComponentFailure.DOCUMENT_EXTRACTION
            elif sim:
                sim_failure = ComponentFailure(sim)
            else:
                sim_failure = None

            try:
                claim = ClaimInput(
                    member_id=inp["member_id"],
                    policy_id=inp["policy_id"],
                    claim_category=ClaimCategory(inp["claim_category"]),
                    treatment_date=date.fromisoformat(inp["treatment_date"]),
                    claimed_amount=inp["claimed_amount"],
                    hospital_name=inp.get("hospital_name"),
                    documents=docs,
                    ytd_claims_amount=inp.get("ytd_claims_amount", 0),
                    claims_history=history,
                    simulate_component_failure=sim_failure,
                )
                trace = run_claim(claim)
                from agents.api.orchestrator import get_orchestrator
                from agents.core.state import make_initial_state
                state = make_initial_state(claim)
                state["trace"] = []
                final_state = get_orchestrator().invoke(state)
                decision = final_state.get("decision", {})
                results.append({
                    "case_id": tc["case_id"],
                    "name": tc["case_name"],
                    "expected": tc["expected"].get("decision"),
                    "actual": decision.get("decision"),
                    "approved": decision.get("approved_amount", 0),
                    "confidence": decision.get("confidence_score", 0),
                })
            except Exception as e:
                results.append({
                    "case_id": tc["case_id"],
                    "name": tc["case_name"],
                    "expected": tc["expected"].get("decision"),
                    "actual": f"ERROR: {e}",
                    "approved": 0,
                    "confidence": 0,
                })

        progress.empty()
        st.session_state["test_results"] = results

    # Display results
    if "test_results" in st.session_state:
        st.markdown("### Results")
        for r in st.session_state["test_results"]:
            match = "✅" if r["actual"] == r["expected"] else "❌"
            color = DECISION_COLORS.get(str(r["actual"]), "⚪")
            with st.expander(f"{match} {r['case_id']}: {r['name']}"):
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Expected", r["expected"] or "N/A")
                with col2:
                    st.metric("Actual", f"{color} {r['actual']}")
                with col3:
                    st.metric("Confidence", f"{r['confidence']:.0%}")
                if r["approved"] > 0:
                    st.markdown(f"**Approved amount:** ₹{r['approved']:,.0f}")


# ----------------------------------------------------------------------
# Page 3: About
# ----------------------------------------------------------------------

def page_about() -> None:
    st.title("ℹ️ About")
    st.markdown("""
    ## Plum Claims Processing Pipeline

    **Multi-agent health insurance claims processing system** built for the Plum AI Engineer assignment.

    ### Architecture
    - **6 specialized agents** orchestrated by LangGraph
    - **FastAPI** backend
    - **Streamlit** UI
    - **Google Gemini 2.0 Flash** (free tier) for production document extraction
    - **Pydantic v2** for type-safe contracts
    - **52+ tests** across all agents and the full pipeline

    ### Agents
    1. **DocumentVerification** — catches wrong/missing/unreadable docs early
    2. **DocumentExtraction** — extracts structured fields from documents
    3. **MemberValidation** — checks eligibility + waiting periods
    4. **PolicyRules** — applies all financial rules (sub-limits, co-pay, exclusions, pre-auth)
    5. **FraudDetection** — pattern detection (same-day, monthly limits)
    6. **Decision** — final synthesis

    ### Tech Stack
    - Python 3.11+
    - LangGraph 0.2+
    - FastAPI 0.115+
    - Streamlit 1.39+
    - Pydantic 2.7+
    - Google Gemini 2.0 Flash (free tier)

    ### Source
    [github.com/wowgeekyboy/plum-claims-pipeline](https://github.com/wowgeekyboy/plum-claims-pipeline)
    """)


# ----------------------------------------------------------------------
# Sidebar navigation
# ----------------------------------------------------------------------

PAGES = {
    "📋 Submit Claim": page_submit,
    "🧪 Test Cases": page_test_cases,
    "ℹ️ About": page_about,
}

st.sidebar.title("🩺 Plum Claims")
st.sidebar.markdown("---")
selection = st.sidebar.radio("Navigate", list(PAGES.keys()))
st.sidebar.markdown("---")
st.sidebar.markdown("Built for Plum AI Engineer assignment")

PAGES[selection]()
