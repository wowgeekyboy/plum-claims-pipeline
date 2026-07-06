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

    # Sample loader — use a callback to update session state
    st.markdown("### 🎯 Quick Start: Load a Sample Claim")
    samples_dir = ROOT / "samples"
    sample_files = sorted(samples_dir.glob("tc*.json")) if samples_dir.exists() else []
    if sample_files:
        sample_labels = ["— Select a sample —"] + [
            f"{p.stem}: {json.loads(p.read_text()).get('name', p.stem)}"
            for p in sample_files
        ]

        def on_sample_change() -> None:
            """When a sample is selected, load it into session state and clear stale form values."""
            sel = st.session_state.get("sample_selector", "— Select a sample —")
            if sel and sel != "— Select a sample —":
                stem = sel.split(":")[0]
                sample_path = samples_dir / f"{stem}.json"
                if sample_path.exists():
                    st.session_state["loaded_sample"] = json.loads(sample_path.read_text())
                    # Bump form version so widgets re-render with new defaults
                    st.session_state["form_version"] = st.session_state.get("form_version", 0) + 1
                    # Clear last decision
                    st.session_state.pop("last_decision", None)
                    st.session_state.pop("last_trace", None)
            else:
                st.session_state.pop("loaded_sample", None)

        st.selectbox(
            "Choose a sample (loads all fields automatically)",
            options=sample_labels,
            key="sample_selector",
            on_change=on_sample_change,
        )

        if st.session_state.get("loaded_sample"):
            sample = st.session_state["loaded_sample"]
            st.success(f"✓ Loaded: {sample.get('name', 'sample')}")
            with st.expander("📄 Sample details", expanded=False):
                st.json(sample)
            if st.button("🔄 Clear loaded sample"):
                st.session_state.pop("loaded_sample", None)
                st.session_state["form_version"] = st.session_state.get("form_version", 0) + 1
                st.rerun()
            st.markdown("---")

    # Determine initial values from loaded sample
    # Use session_state to manage widget values directly (survives reruns)
    loaded = st.session_state.get("loaded_sample", {})
    default_claim = loaded.get("claim", {})
    default_docs = loaded.get("documents", [])

    # Initialize form defaults in session state on first render OR when sample changes
    form_version = st.session_state.get("form_version", 0)
    if f"member_id_v{form_version}" not in st.session_state:
        st.session_state[f"member_id_v{form_version}"] = default_claim.get("member_id", "EMP001")
        st.session_state[f"claimed_amount_v{form_version}"] = int(default_claim.get("claimed_amount", 1500))
        st.session_state[f"hospital_name_v{form_version}"] = default_claim.get("hospital_name", "") or ""
        try:
            default_date = date.fromisoformat(default_claim.get("treatment_date", "2024-11-01"))
        except (ValueError, TypeError):
            default_date = date(2024, 11, 1)
        st.session_state[f"treatment_date_v{form_version}"] = default_date
        st.session_state[f"claim_category_v{form_version}"] = default_claim.get("claim_category", "CONSULTATION")

    v = form_version  # shorthand

    col1, col2 = st.columns(2)
    with col1:
        member_id = st.text_input(
            "Member ID",
            key=f"member_id_v{v}",
            help="e.g. EMP001, EMP005",
        )
        cat_options = [c.value for c in ClaimCategory]
        claim_category = st.selectbox(
            "Claim Category",
            options=cat_options,
            key=f"claim_category_v{v}",
        )
        claimed_amount = st.number_input(
            "Claimed Amount (₹)",
            min_value=0,
            max_value=100000,
            step=100,
            key=f"claimed_amount_v{v}",
        )
    with col2:
        treatment_date = st.date_input(
            "Treatment Date",
            min_value=date(2020, 1, 1),
            max_value=date(2030, 12, 31),
            key=f"treatment_date_v{v}",
        )
        hospital_name = st.text_input(
            "Hospital Name (optional)",
            key=f"hospital_name_v{v}",
        )

    st.markdown("### Documents")
    st.markdown("Add the documents uploaded for this claim.")

    # If a sample is loaded, use its documents; otherwise default to 2 empty
    if default_docs:
        st.info(f"Sample loaded — {len(default_docs)} document(s) pre-filled below")
        num_docs = len(default_docs)
    else:
        num_docs = st.number_input("Number of documents", min_value=1, max_value=5, value=2, key=f"num_docs_v{v}")

    documents = []
    for i in range(num_docs):
        # Get defaults from sample if available
        default_doc = default_docs[i] if i < len(default_docs) else {}
        default_type = default_doc.get("actual_type", "PRESCRIPTION")
        default_quality = default_doc.get("quality", "GOOD")
        default_filename = default_doc.get("file_name", f"doc_{i+1}.jpg")
        default_content = default_doc.get("content")

        with st.expander(f"Document #{i+1}: {default_doc.get('file_id', f'F{i+1:03d}')}", expanded=(i < 2)):
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                type_options = [t.value for t in DocumentType if t.value != "UNKNOWN"]
                # Initialize doc type in session state
                doc_type_key = f"doctype_v{v}_{i}"
                if doc_type_key not in st.session_state:
                    st.session_state[doc_type_key] = default_type
                doc_type = st.selectbox(
                    "Type",
                    options=type_options,
                    key=doc_type_key,
                )
                filename_key = f"filename_v{v}_{i}"
                if filename_key not in st.session_state:
                    st.session_state[filename_key] = default_filename
                file_name = st.text_input("File name", key=filename_key)
            with col_d2:
                quality_options = [q.value for q in DocumentQuality]
                quality_key = f"quality_v{v}_{i}"
                if quality_key not in st.session_state:
                    st.session_state[quality_key] = default_quality
                quality = st.selectbox(
                    "Quality",
                    options=quality_options,
                    key=quality_key,
                )

            # Content handling
            content_checkbox_key = f"content_v{v}_{i}"
            if default_content is not None:
                if content_checkbox_key not in st.session_state:
                    st.session_state[content_checkbox_key] = True
                include_content = st.checkbox(
                    "Include pre-extracted content (test mode)",
                    key=content_checkbox_key,
                )
                if include_content:
                    st.json(default_content)
                content = default_content if include_content else None
            else:
                if content_checkbox_key not in st.session_state:
                    st.session_state[content_checkbox_key] = False
                include_content = st.checkbox(
                    "Include pre-extracted content (test mode)",
                    key=content_checkbox_key,
                )
                content = None
                if include_content:
                    if doc_type == "PRESCRIPTION":
                        content = {
                            "patient_name": st.text_input("Patient name", value="Rajesh Kumar", key=f"patient_v{v}_{i}"),
                            "doctor_name": st.text_input("Doctor name", value="Dr. Arun Sharma", key=f"doctor_v{v}_{i}"),
                            "doctor_registration": st.text_input("Doctor reg", value="KA/45678/2015", key=f"reg_v{v}_{i}"),
                            "date": str(treatment_date),
                            "diagnosis": st.text_input("Diagnosis", value="Viral Fever", key=f"diag_v{v}_{i}"),
                        }
                    elif doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL"):
                        content = {
                            "hospital_name": hospital_name or "City Clinic",
                            "patient_name": st.text_input("Patient name", value="Rajesh Kumar", key=f"patient_v{v}_{i}"),
                            "date": str(treatment_date),
                            "total": claimed_amount,
                            "line_items": [
                                {"description": "Consultation Fee", "amount": claimed_amount},
                            ],
                        }

            documents.append({
                "file_id": default_doc.get("file_id", f"F{i+1:03d}"),
                "file_name": file_name,
                "actual_type": doc_type,
                "quality": quality,
                "content": content,
            })

    st.markdown("---")
    submitted = st.button("🚀 Submit Claim", use_container_width=True, type="primary", key=f"submit_v{v}")

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

def page_mock_documents() -> None:
    st.title("🖼️ Mock Documents")
    st.markdown("""
    Visual mock medical documents generated for demo purposes.
    These are JPG files that look like real Indian medical documents —
    prescriptions, hospital bills, lab reports.

    Use these to:
    - Demonstrate the system with realistic-looking inputs
    - Show the demo video
    - Understand what the LLM (Gemini) would see in production
    """)

    mock_dir = ROOT / "mock_docs"
    if not mock_dir.exists():
        st.error("Mock documents not found. Run `python scripts/generate_mock_documents.py` first.")
        return

    # Categorize documents
    categories = {
        "Prescriptions": [f for f in mock_dir.glob("*prescription*.jpg")],
        "Hospital & Pharmacy Bills": [f for f in mock_dir.glob("*bill*.jpg") if "blurry" not in f.name],
        "Lab Reports": [f for f in mock_dir.glob("*report*.jpg")],
    }

    for category, files in categories.items():
        if not files:
            continue
        st.markdown(f"### {category}")
        cols = st.columns(min(len(files), 3))
        for i, f in enumerate(sorted(files)):
            with cols[i % 3]:
                st.image(str(f), caption=f.name, use_container_width=True)

    st.markdown("---")
    st.markdown("""
    ### How to regenerate
    Run: `python scripts/generate_mock_documents.py`

    The documents are deterministic — same input → same output.
    """)


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
    "🖼️ Mock Documents": page_mock_documents,
    "ℹ️ About": page_about,
}

st.sidebar.title("🩺 Plum Claims")
st.sidebar.markdown("---")
selection = st.sidebar.radio("Navigate", list(PAGES.keys()))
st.sidebar.markdown("---")
st.sidebar.markdown("Built for Plum AI Engineer assignment")

PAGES[selection]()
