# Plum Claims Processing Pipeline

> Multi-agent health insurance claims processing system built for the Plum AI Engineer assignment.

A production-grade pipeline that automates health insurance claim adjudication for Indian employee health benefits, with full observability, graceful failure handling, and explainable decisions.

---

## What This System Does

When an employee submits a health insurance claim, this system:

1. **Verifies** that the right documents are uploaded (and stops early with a specific message if not)
2. **Extracts** structured data from messy Indian medical documents (handwritten prescriptions, phone photos of bills, etc.)
3. **Validates** the member against the policy (eligibility, waiting periods, coverage)
4. **Applies** all policy rules (sub-limits, co-pay, network discount, exclusions, pre-auth)
5. **Detects** fraud signals (unusual same-day claims, high-value claims)
6. **Decides** APPROVED / PARTIAL / REJECTED / MANUAL_REVIEW with a confidence score and full trace

Every decision is **explainable** — the trace shows exactly what was checked, what passed, what failed, and why.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Frontend (Streamlit) — Submit claim, view decision + trace   │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  Orchestrator (LangGraph State Machine)                      │
└──┬────────┬────────┬────────┬────────┬────────┬─────────────┘
   ▼        ▼        ▼        ▼        ▼        ▼
┌──────┐┌──────┐┌────────┐┌──────┐┌──────┐┌──────────┐
│ Doc  ││ Doc  ││Member  ││Policy││Fraud ││ Decision │
│Valid ││Extr- ││Valida- ││Rule  ││Detect││  Agent   │
│Agent ││actor ││tion    ││Eng.  ││Agent ││          │
└──────┘└──────┘└────────┘└──────┘└──────┘└──────────┘
```

Six specialized agents, each with a single responsibility, Pydantic I/O contracts, and a confidence score that contributes to the final decision.

See `docs/ARCHITECTURE.md` for the full system design.

---

## Project Structure

```
plum-claims-pipeline/
├── agents/                  # Multi-agent pipeline
│   ├── api/                 # Orchestrator (LangGraph)
│   ├── document_verification/
│   ├── document_extraction/
│   ├── member_validation/
│   ├── policy_rules/
│   ├── fraud_detection/
│   └── decision/
├── api/                     # FastAPI backend
├── frontend/                # Streamlit UI
├── tests/                   # Pytest suite (12 test cases)
├── eval/                    # Eval report generator
├── docs/                    # ARCHITECTURE.md, CONTRACTS.md, EVAL_REPORT.md
├── data/                    # policy_terms.json, test_cases.json
├── mock_docs/               # Generated mock documents
├── scripts/                 # Utility scripts (mock doc generation, etc.)
├── .env.example             # Template for environment variables
├── requirements.txt
└── README.md
```

---

## Quick Start

### 1. Prerequisites
- Python 3.11+
- A free Google Gemini API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

### 2. Setup
```bash
# Clone
git clone https://github.com/wowgeekyboy/plum-claims-pipeline.git
cd plum-claims-pipeline

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -r requirements.txt

# Configure API key
cp .env.example .env
# Edit .env and paste your GOOGLE_API_KEY
```

### 3. Run the API
```bash
uvicorn api.main:app --reload --port 8000
```

### 4. Run the UI
```bash
streamlit run frontend/app.py
```

### 5. Run the tests
```bash
pytest tests/ -v
```

---

## Deliverables

| # | Deliverable | Location |
|---|-------------|----------|
| 1 | Working System + UI | `frontend/` + deployed URL |
| 2 | Architecture Document | `docs/ARCHITECTURE.md` |
| 3 | Component Contracts | `docs/COMPONENT_CONTRACTS.md` |
| 4 | Eval Report (all 12 test cases) | `docs/EVAL_REPORT.md` |
| 5 | Demo Video (8-12 min) | (see submission) |

---

## Tech Stack

- **Multi-agent orchestration**: LangGraph (state machine)
- **LLM**: Google Gemini 2.0 Flash (vision-capable, free tier)
- **Backend**: FastAPI
- **Frontend**: Streamlit
- **Validation**: Pydantic v2
- **Testing**: Pytest
- **Deployment**: Streamlit Community Cloud

---

## Test Cases

The system is evaluated against 12 test cases from `data/test_cases.json`:

| ID | Scenario | Expected Decision |
|----|----------|-------------------|
| TC001 | Wrong document type uploaded | Stop early with specific error |
| TC002 | Unreadable document | Ask for re-upload, don't reject |
| TC003 | Documents belong to different patients | Stop early, surface names |
| TC004 | Clean consultation (co-pay) | APPROVED ₹1,350 |
| TC005 | Diabetes within 90-day waiting period | REJECTED |
| TC006 | Dental partial (cosmetic exclusion) | PARTIAL ₹8,000 |
| TC007 | MRI without pre-auth (>₹10K) | REJECTED |
| TC008 | Per-claim limit exceeded (>₹5K) | REJECTED |
| TC009 | Multiple same-day claims (fraud) | MANUAL_REVIEW |
| TC010 | Network hospital discount order | APPROVED ₹3,240 |
| TC011 | Component failure (graceful) | APPROVED with reduced confidence |
| TC012 | Excluded treatment (bariatric) | REJECTED |

---

## License

Built for the Plum AI Engineer assignment. © 2026.
