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
    """The Submit Claim page.

    Architecture:
    - Sample loader at the top (dropdown + button grid fallback)
    - Form for claim details (always shown, pre-filled when sample is loaded)
    - Documents section (auto-derived from sample or empty defaults)
    - Submit button runs the orchestrator
    """
    import traceback
    import logging
    logger = logging.getLogger("streamlit.page_submit")

    try:
        _page_submit_impl()
    except Exception as e:
        # Show the error to the user so we can see what's going wrong
        st.error(f"❌ Page error: {type(e).__name__}: {e}")
        with st.expander("🔍 Full traceback", expanded=True):
            st.code(traceback.format_exc())
        # Log to file too
        with open("/tmp/page_submit_error.log", "w") as f:
            f.write(f"Error: {e}\n\n{traceback.format_exc()}\n\nSession state: {dict(st.session_state)}\n")
        logger.exception("page_submit failed")


def _page_submit_impl() -> None:
    st.title("📋 Submit a Claim")
    st.markdown("Submit a new health insurance claim for processing. _v2.2 — robust error handling_")

    # ---- Sample loader ----
    st.markdown("### 🎯 Quick Start: Load a Sample Claim")
    samples_dir = ROOT / "samples"
    sample_files = sorted(samples_dir.glob("tc*.json")) if samples_dir.exists() else []

    if not sample_files:
        st.warning("No samples found in `samples/` directory. Fill in the form manually below.")
        active_sample = {}
    else:
        # Build a simple map of stem -> sample data
        if "_samples_cache" not in st.session_state:
            cache = {}
            for p in sample_files:
                try:
                    cache[p.stem] = json.loads(p.read_text())
                except Exception as ex:
                    st.warning(f"Could not load {p.name}: {ex}")
            st.session_state["_samples_cache"] = cache
        samples_cache = st.session_state["_samples_cache"]

        st.markdown("**Option 1:** Pick from dropdown")
        sample_names = ["— Select a sample —"] + list(samples_cache.keys())
        # Find current index from session state
        active = st.session_state.get("_active_stem")
        if active and active in samples_cache:
            current_idx = sample_names.index(active)
        else:
            current_idx = 0
        selected_stem = st.selectbox(
            "Choose a test case",
            options=sample_names,
            index=current_idx,
            key="_sample_dropdown_v3",
            label_visibility="collapsed",
        )
        # Sync the dropdown to the active state
        if selected_stem and not selected_stem.startswith("—"):
            if st.session_state.get("_active_stem") != selected_stem:
                st.session_state["_active_stem"] = selected_stem
                st.session_state["_active_sample"] = samples_cache[selected_stem]
        elif st.session_state.get("_active_stem"):
            # User chose placeholder but we have a sample loaded — keep it
            pass
        else:
            st.session_state.pop("_active_stem", None)
            st.session_state.pop("_active_sample", None)

        st.markdown("**Option 2:** Click a button")
        cols_per_row = 4
        for row_start in range(0, len(sample_files), cols_per_row):
            row_files = sample_files[row_start:row_start + cols_per_row]
            cols = st.columns(len(row_files))
            for col, sample_path in zip(cols, row_files):
                with col:
                    stem = sample_path.stem
                    # Show short label
                    short_label = stem.replace("tc", "TC").replace("_", " ")
                    if st.button(short_label, key=f"_load_{stem}", use_container_width=True):
                        st.session_state["_active_stem"] = stem
                        st.session_state["_active_sample"] = samples_cache.get(stem)
                        st.session_state.pop("last_decision", None)
                        st.session_state.pop("last_trace", None)

        # Show what's loaded
        active_stem = st.session_state.get("_active_stem")
        if active_stem:
            sample = st.session_state.get("_active_sample") or {}
            st.success(f"✓ Loaded: **{sample.get('name', active_stem)}** (`{active_stem}`)")
            with st.expander("📄 View sample data", expanded=False):
                try:
                    st.json(sample)
                except Exception:
                    st.write(sample)  # fallback if json fails
            if st.button("🗑️ Clear loaded sample", key="_clear_v3"):
                st.session_state.pop("_active_stem", None)
                st.session_state.pop("_active_sample", None)
                st.session_state.pop("last_decision", None)
                st.session_state.pop("last_trace", None)

    st.markdown("---")

    # ---- Build the form ----
    active_sample = st.session_state.get("_active_sample") or {}
    if not isinstance(active_sample, dict):
        active_sample = {}
    default_claim = active_sample.get("claim", {})
    default_docs = active_sample.get("documents", [])

    st.markdown("### ✏️ Claim Details")
    col1, col2 = st.columns(2)
    with col1:
        member_id = st.text_input(
            "Member ID",
            value=str(default_claim.get("member_id", "EMP001")),
            key="_member_id",
        )
        cat_options = [c.value for c in ClaimCategory]
        cat_default = str(default_claim.get("claim_category", "CONSULTATION"))
        cat_idx = cat_options.index(cat_default) if cat_default in cat_options else 0
        claim_category = st.selectbox(
            "Claim Category",
            options=cat_options,
            index=cat_idx,
            key="_claim_category",
        )
        try:
            amount_default = int(default_claim.get("claimed_amount", 1500))
        except (ValueError, TypeError):
            amount_default = 1500
        claimed_amount = st.number_input(
            "Claimed Amount (₹)",
            min_value=0,
            max_value=100000,
            value=amount_default,
            step=100,
            key="_claimed_amount",
        )
    with col2:
        try:
            d_default = date.fromisoformat(str(default_claim.get("treatment_date", "2024-11-01")))
        except (ValueError, TypeError):
            d_default = date(2024, 11, 1)
        treatment_date = st.date_input(
            "Treatment Date",
            value=d_default,
            min_value=date(2020, 1, 1),
            max_value=date(2030, 12, 31),
            key="_treatment_date",
        )
        hospital_name = st.text_input(
            "Hospital Name (optional)",
            value=str(default_claim.get("hospital_name", "") or ""),
            key="_hospital_name",
        )

    st.markdown("### 📄 Documents")
    if not default_docs:
        default_docs = [
            {"file_id": "F001", "file_name": "doc_1.jpg", "actual_type": "PRESCRIPTION", "quality": "GOOD", "content": None},
            {"file_id": "F002", "file_name": "doc_2.jpg", "actual_type": "HOSPITAL_BILL", "quality": "GOOD", "content": None},
        ]
        st.info("Using empty document defaults. Load a sample above to see the form pre-filled.")

    documents = []
    type_options = [t.value for t in DocumentType if t.value != "UNKNOWN"]
    quality_options = [q.value for q in DocumentQuality]

    for i, dd in enumerate(default_docs):
        if not isinstance(dd, dict):
            continue
        with st.expander(f"Doc #{i+1} — {dd.get('file_id', f'F{i+1:03d}')}", expanded=(i < 2)):
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                dt = str(dd.get("actual_type", "PRESCRIPTION"))
                tidx = type_options.index(dt) if dt in type_options else 0
                doc_type = st.selectbox(
                    "Type",
                    options=type_options,
                    index=tidx,
                    key=f"_doc_type_{i}",
                )
                file_name = st.text_input(
                    "File name",
                    value=str(dd.get("file_name", f"doc_{i+1}.jpg")),
                    key=f"_doc_filename_{i}",
                )
            with col_d2:
                ql = str(dd.get("quality", "GOOD"))
                qidx = quality_options.index(ql) if ql in quality_options else 0
                quality = st.selectbox(
                    "Quality",
                    options=quality_options,
                    index=qidx,
                    key=f"_doc_quality_{i}",
                )

            has_content = dd.get("content") is not None
            include_content = st.checkbox(
                "Include pre-extracted content (test mode)",
                value=has_content,
                key=f"_include_content_{i}",
            )
            content = None
            if include_content and dd.get("content"):
                try:
                    st.json(dd["content"])
                except Exception:
                    st.write(dd["content"])
                content = dd["content"]
            elif include_content:
                if doc_type == "PRESCRIPTION":
                    content = {
                        "patient_name": "Rajesh Kumar",
                        "doctor_name": "Dr. Arun Sharma",
                        "doctor_registration": "KA/45678/2015",
                        "date": str(treatment_date),
                        "diagnosis": "Viral Fever",
                        "medicines": ["Paracetamol 650mg"],
                    }
                elif doc_type in ("HOSPITAL_BILL", "PHARMACY_BILL"):
                    content = {
                        "hospital_name": hospital_name or "City Clinic",
                        "patient_name": "Rajesh Kumar",
                        "date": str(treatment_date),
                        "total": claimed_amount,
                        "line_items": [{"description": "Consultation", "amount": claimed_amount}],
                    }
                elif doc_type == "LAB_REPORT":
                    content = {"patient_name": "Rajesh Kumar", "test_name": "CBC", "date": str(treatment_date)}

            documents.append({
                "file_id": dd.get("file_id", f"F{i+1:03d}"),
                "file_name": file_name,
                "actual_type": doc_type,
                "quality": quality,
                "content": content,
            })

    st.markdown("---")
    submitted = st.button("🚀 Submit Claim", type="primary", use_container_width=True)

    if submitted:
        with st.spinner("Running claim through 6-agent pipeline..."):
            try:
                doc_inputs = []
                for d in documents:
                    try:
                        doc_inputs.append(
                            DocumentInput(
                                file_id=str(d.get("file_id", "F001")),
                                file_name=str(d.get("file_name", "")),
                                actual_type=DocumentType(d["actual_type"]) if d.get("actual_type") else None,
                                quality=DocumentQuality(d["quality"]),
                                content=d.get("content"),
                            )
                        )
                    except Exception as ex:
                        st.warning(f"Skipping bad doc: {ex}")

                # Build claims history
                history = []
                for h in default_claim.get("claims_history", []):
                    try:
                        from agents.core.domain import ClaimHistoryItem
                        history.append(ClaimHistoryItem(
                            claim_id=h.get("claim_id", "?"),
                            date=date.fromisoformat(h["date"]),
                            amount=float(h.get("amount", 0)),
                            provider=h.get("provider"),
                        ))
                    except Exception:
                        pass

                # Build sim failure
                sim_failure = None
                sim_str = active_sample.get("simulate_component_failure")
                if sim_str:
                    try:
                        from agents.core.enums import ComponentFailure
                        sim_failure = ComponentFailure(sim_str)
                    except (ValueError, KeyError):
                        sim_failure = None

                claim = ClaimInput(
                    member_id=str(member_id),
                    policy_id="PLUM_GHI_2024",
                    claim_category=ClaimCategory(claim_category),
                    treatment_date=treatment_date,
                    claimed_amount=float(claimed_amount),
                    hospital_name=str(hospital_name) if hospital_name else None,
                    documents=doc_inputs,
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
                st.session_state["last_decision"] = decision
                st.session_state["last_trace"] = trace
            except Exception as e:
                st.error(f"Error processing claim: {type(e).__name__}: {e}")
                import traceback
                with st.expander("🔍 Full traceback", expanded=True):
                    st.code(traceback.format_exc())

    # Show result
    if st.session_state.get("last_decision"):
        st.markdown("---")
        st.markdown("## 📊 Decision")
        try:
            render_decision_card(st.session_state["last_decision"])
        except Exception as e:
            st.error(f"Error rendering decision: {e}")
        if st.session_state.get("last_trace"):
            try:
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
            except Exception as e:
                st.error(f"Error rendering trace: {e}")
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
