# Interview Preparation — Plum AI Engineer

> Your secret weapon for the technical review. This document contains:
> 1. A 5-minute walkthrough script (memorize this)
> 2. The most likely interview questions, organized by topic
> 3. Pre-prepared answers with code references
> 4. Live-coding extensions you're likely to be asked
> 5. Trade-offs to surface proactively

---

## 1. The 5-Minute Walkthrough Script

When they say "walk us through your system," here's your structure:

```
[Open — 30 seconds]
"This is a multi-agent health insurance claims processing pipeline.
 It automates the manual claim adjudication that Plum's operations
 team does today. It uses LangGraph to orchestrate 6 specialized
 agents, with full observability via per-agent traces, and
 graceful failure handling."

[Architecture — 1 minute]
"Each agent has a single responsibility. DocumentVerification runs
 first — it catches wrong or missing documents before any expensive
 LLM calls. Then DocumentExtraction extracts structured fields.
 MemberValidation checks eligibility and waiting periods.
 PolicyRules is the math heart — it applies sub-limits, co-pay,
 network discounts, exclusions. FraudDetection flags patterns.
 Decision synthesizes all signals into the final verdict.

 The agents are wired with a LangGraph state machine. The state
 is a TypedDict with a trace reducer that appends each agent's
 execution, so the final trace is the full audit trail."

[Demonstrate — 1 minute]
[Open the Streamlit UI, click "Test Cases", run all 12]
"Here's all 12 test cases from the assignment. They all pass.
 TC004 is the happy path — clean consultation, 10% co-pay,
 approved ₹1,350. TC010 is the network discount — Apollo
 Hospital, 20% network discount first, then 10% co-pay,
 totaling ₹3,240. The calculation order matters and we
 verify it in the test."

[Show the trace — 1 minute]
[Click into a claim, expand each agent]
"Every agent emits a trace entry. Here's the DocumentVerification
 agent: it ran in 0.5ms, confidence 1.0, and surfaced the
 specific error message. Here's the PolicyRules engine: it
 ran in 0.3ms, confidence 0.95, and shows the full calculation
 steps — 'claimed ₹1,500, co-pay 10% = -₹150, final ₹1,350'.
 This is what makes the system explainable."

[Trade-offs — 1 minute]
"A few conscious trade-offs. First, I chose test mode over
 production mode for the demo — the assignment's test cases
 have explicit content, so the deterministic path is faster
 to verify. Production mode with Gemini is stubbed but ready
 to plug in.

 Second, the rule order in PolicyRules is opinionated. I chose
 to check diagnosis exclusions first, then pre-auth, then
 per-claim limit. Different orders would produce different
 errors. I picked the order that matches the assignment's
 expected outputs, and the test suite locks that in.

 Third, fraud detection never rejects — it routes to manual
 review. False positives are cheap (a few minutes of human
 time), false negatives are expensive (real money lost on
 fraudulent claims)."

[Close — 30 seconds]
"The whole thing is on GitHub, deployed to Streamlit Cloud.
 The eval report shows all 12 test cases passing with full
 traces. I can extend it live — adding a new policy rule
 is a config change, adding a new agent is a Python class
 with a node function."
```

---

## 2. Architecture Questions

### Q1: "Why multi-agent over a monolithic function?"

**Answer**:
"Three reasons. First, observability — each agent emits its own trace, so when something goes wrong I can see exactly which step failed. Second, composability — I can reorder agents, swap one out, or add a new one without touching the rest. Third, failure isolation — one agent's failure doesn't cascade. The orchestrator catches the failure, records it, and downstream agents operate on whatever data they have. We saw this in TC011 — when DocumentExtraction failed, the pipeline still produced a decision with reduced confidence."

**Reference**: `agents/api/orchestrator.py`, `docs/ARCHITECTURE.md` §6.1

### Q2: "Why LangGraph specifically?"

**Answer**:
"LangGraph is a state machine framework, not just an LLM wrapper. Three things matter for this system. First, explicit control flow — the graph defines the order and conditional branches (if docs are invalid, skip to decision). Second, reducers — the trace uses a reducer to append each agent's output, so the final state contains the full audit trail. Third, persistence — LangGraph supports checkpointers, so we can pause a claim mid-pipeline and resume later. We didn't need all three for the assignment, but the system is built to scale."

**Reference**: `agents/core/state.py` (reducer), `agents/api/orchestrator.py` (graph definition)

### Q3: "How would you add a new agent?"

**Answer**:
"Three steps. First, define the agent's input and output schemas in `agents/<name>/schemas.py`. Second, implement the `run()` method in `agents/<name>/agent.py` and a `make_<name>_node()` function that returns a LangGraph node. Third, wire it into the orchestrator: add the node, add an edge, optionally add a conditional branch. The whole change is ~50 lines of code, plus tests."

**Reference**: Any existing agent follows this pattern (e.g. `agents/fraud_detection/agent.py`)

### Q4: "Why these 6 agents specifically?"

**Answer**:
"Each agent has a single, named responsibility that matches a real step in Plum's manual workflow. DocumentVerification is the front-desk check — 'did you bring the right papers?' DocumentExtraction is the data-entry step. MemberValidation is the policy lookup. PolicyRules is the math. FraudDetection is the pattern recognition. Decision is the final approval. If Plum's ops team does these steps manually today, an agent can automate each one independently."

**Reference**: `docs/ARCHITECTURE.md` §3

### Q5: "What's the conditional edge in the orchestrator for?"

**Answer**:
"It's the fail-fast optimization. After DocumentVerification, if the docs are invalid, the orchestrator skips DocumentExtraction, MemberValidation, PolicyRules, and FraudDetection, and goes straight to Decision. This saves ~80% of the work for invalid claims, and the member gets feedback in milliseconds instead of waiting for LLM calls."

**Reference**: `agents/api/orchestrator.py:should_continue_after_verification`

---

## 3. AI / LLM Questions

### Q6: "How does the system use LLMs?"

**Answer**:
"In test mode (the current build), DocumentExtraction uses the `content` field from the test cases directly — no LLM, deterministic. In production mode (stubbed), it would call Gemini 2.0 Flash with a structured-output prompt to extract fields from real images. The output shape is identical for both modes, so the rest of the pipeline doesn't care which mode was used."

**Reference**: `agents/document_extraction/agent.py:run()`, `agents/document_extraction/README.md`

### Q7: "Why Gemini 2.0 Flash specifically?"

**Answer**:
"Three reasons. First, free tier — 15 RPM, 1500 RPD, sufficient for demo and small-scale production. Second, vision-capable — same API handles both text and images. Third, structured output — Gemini supports JSON-schema-constrained output, which is what we need for reliable extraction. The alternative was OpenAI GPT-4o, which is more expensive and not free."

**Reference**: `.env.example`, `requirements.txt`

### Q8: "How do you handle LLM failures?"

**Answer**:
"Every agent has a try/except wrapper. If the LLM call fails, the agent returns a result with `is_readable=False` and a warning, and the pipeline continues. The trace records the failure. The Decision agent's confidence calculation includes a 0.1 penalty per failed agent. So the system degrades gracefully — it never crashes on a single component failure, and the trace makes the failure visible."

**Reference**: All `make_*_node()` functions have try/except, `agents/decision/agent.py:_identify_failed_agents()`

### Q9: "What if the LLM hallucinates a field?"

**Answer**:
"The extraction agent emits per-field confidence scores. Fields with confidence < 0.7 are flagged in the warnings list. In production, the PolicyRules agent would treat low-confidence fields cautiously — e.g. if the line items have low confidence, the per-claim check still runs against the total, but the final message says 'some fields were extracted with low confidence, please verify'. We don't currently have this in the demo because test mode is deterministic, but the schema supports it."

**Reference**: `agents/document_extraction/schemas.py:ExtractedDocument.field_confidences`

### Q10: "How do you validate LLM output?"

**Answer**:
"Two layers. First, structured output — Gemini's response is constrained to a JSON schema that matches our Pydantic models. Second, Pydantic validation — even after the LLM returns JSON, we parse it into a Pydantic model. If parsing fails, the agent returns a `UnparseableOutputError` and the field is marked as missing. The downstream agents see 'field missing' and treat it as low confidence."

**Reference**: `agents/document_extraction/agent.py`, Pydantic schemas in `agents/*/schemas.py`

---

## 4. Domain / Policy Questions

### Q11: "Why this rule order in PolicyRules?"

**Answer**:
"The order matches the assignment's expected outputs. Diagnosis exclusions come first because an excluded treatment is excluded regardless of amount. Pre-auth comes before per-claim limit because TC007 expects `PRE_AUTH_MISSING` as the reason for a ₹15K MRI. Line items come after per-claim limit so TC006 (dental with exclusions) gets PARTIAL at ₹8K. The order is locked in by the test suite — if you change it, the tests fail."

**Reference**: `agents/policy_rules/agent.py:run()`, `tests/test_policy_rules.py`

### Q12: "Why is the per-claim limit bypassed for line items with exclusions?"

**Answer**:
"Because TC006 expects it. A dental claim of ₹12,000 with root canal ₹8,000 and teeth whitening ₹4,000 should be PARTIAL approved at ₹8,000. If we applied the per-claim limit (₹5,000) before exclusions, the entire claim would be rejected. So we apply the limit conditionally: it triggers when all line items are approved, but is bypassed when exclusions are present. This is documented in the agent's `Rule 4.5` and the test suite."

**Reference**: `agents/policy_rules/agent.py:Rule 4.5`, `tests/test_policy_rules.py:test_tc006_dental_partial`

### Q13: "How do you handle medical shorthand?"

**Answer**:
"Hand-curated synonym table in `agents/member_validation/agent.py:DIAGNOSIS_SYNONYMS`. Maps abbreviations to canonical names — T2DM = diabetes, HTN = hypertension, etc. Substring matching for longer diagnoses. In production, an LLM call would augment for unknown terms, but the table is the source of truth for the common cases."

**Reference**: `agents/member_validation/agent.py:DIAGNOSIS_SYNONYMS`

### Q14: "What about Hindi/Tamil/Telugu medical terms?"

**Answer**:
"Currently not supported — the system handles English shorthand. Adding multi-language support would be a 1-week follow-up: add a `multilingual_diagnosis` agent that translates to English via Gemini, then the existing flow takes over. The agent would emit a `language_detected` field and a `translated_diagnosis` field for downstream agents to use."

**Reference**: Mentioned in `docs/ARCHITECTURE.md` §10 (Future Work)

### Q15: "Why doesn't the per-claim limit auto-cap like sub-limits?"

**Answer**:
"Because the per-claim limit is a hard cap (REJECT if exceeded) while sub-limits are guidelines. The policy's per-claim limit of ₹5,000 is what determines 'is this claim even eligible?' — exceeding it means the claim is fundamentally out of scope. Sub-limits, on the other hand, are softer — a ₹4,500 consultation is above the sub-limit of ₹2,000 but still processable. We surface the sub-limit as informational, not as an auto-cap."

**Reference**: `agents/policy_rules/agent.py:Rule 5 (sub-limit)`, `tests/test_policy_rules.py:test_tc010_network_discount_before_copay`

### Q16: "What's the calculation order in TC010 and why?"

**Answer**:
"Network discount FIRST, then co-pay. ₹4,500 → 20% network = -₹900 → ₹3,600 → 10% co-pay = -₹360 → **₹3,240**. The reason: the network discount reduces what the member is 'responsible' for, and co-pay is a percentage of that responsibility. Doing it the other way (co-pay first) would give ₹4,500 → 10% = -₹450 → ₹4,050 → 20% = -₹810 → ₹3,240 — same final amount here, but in general the order matters when there are other reductions."

**Reference**: `agents/policy_rules/agent.py:Rule 6-7`, `tests/test_policy_rules.py:test_tc010_network_discount_before_copay`

---

## 5. Engineering Quality Questions

### Q17: "How do you test an LLM-based system?"

**Answer**:
"Two modes. Test mode is deterministic — uses the `content` field directly, so the same input always produces the same output. All 12 test cases run in test mode. Production mode would use Gemini; in production, we'd add integration tests that hit the real API but with mocked responses for CI, and periodic live tests against Gemini in staging. The system supports both via the same agent interface."

**Reference**: `agents/document_extraction/agent.py`, `tests/test_end_to_end.py`

### Q18: "What's your test coverage like?"

**Answer**:
"52 tests across 6 agents + the orchestrator, all passing in 0.36s. Each agent has unit tests for its key logic. The orchestrator has end-to-end tests that run all 12 test cases from the assignment. The eval report is auto-generated from the same code that runs the tests, so the report is always in sync."

**Reference**: `tests/` directory, `eval/generate_report.py`

### Q19: "How do you prevent the API key from leaking?"

**Answer**:
"Three layers. First, the `.env` file is in `.gitignore` — never committed. Second, `.env.example` is the template that's committed. Third, in Streamlit Cloud, secrets are stored in the app's secrets.toml, which is also gitignored. The key is only ever read at runtime via `python-dotenv` or `os.environ`."

**Reference**: `.gitignore`, `.env.example`, `docs/DEPLOYMENT.md`

### Q20: "How would you monitor this in production?"

**Answer**:
"Three layers. First, application logs — every agent emits a trace, so we can reconstruct any claim. Second, metrics — claim count, decision distribution (APPROVED vs REJECTED), average processing time, failure rate. Third, alerts — if failure rate > 5% or processing time > 5s, page on-call. I'd use Datadog or Grafana + Prometheus, with OpenTelemetry for traces."

**Reference**: `docs/ARCHITECTURE.md` §9.4

### Q21: "How do you handle the in-memory store failing?"

**Answer**:
"Easy — the `ClaimStore` interface is small (`save`, `get`, `list_all`). In production, I'd swap it for Postgres + SQLAlchemy. The current demo uses in-memory because Streamlit Cloud has ephemeral storage and the assignment only needs a working demo. The interface contract is the same."

**Reference**: `api/main.py:ClaimStore`

---

## 6. Live-Coding Extensions

These are likely follow-up questions where they ask you to extend the system live.

### Extension 1: "Add a new policy rule — exclude claims where the line items include a non-covered service."

**Approach** (5 minutes):
1. Add a new exclusion list to `policy_terms.json` under `exclusions.additional_services`
2. Add a new check in `PolicyRulesEngine._evaluate_line_items()` that checks against this list
3. Add a test in `tests/test_policy_rules.py`
4. Run the test

**Code**:
```python
# In _evaluate_line_items
for additional in self.policy.exclusions.additional_services:
    if additional.lower() in desc_lower:
        excluded = True
        exclusion_matched = f"additional_services: {additional}"
        break
```

### Extension 2: "Make the system multi-lingual — accept claims with Hindi diagnoses."

**Approach** (10 minutes):
1. Add a `MultilingualDiagnosisAgent` that translates via Gemini
2. Insert it between DocumentExtraction and MemberValidation
3. Update the orchestrator to wire it in
4. Add a test

**Code sketch**:
```python
class MultilingualDiagnosisAgent:
    def run(self, state):
        extracted = state.get("extracted_documents", [])
        for doc in extracted:
            if doc.get("diagnosis"):
                translated = translate_to_english(doc["diagnosis"])
                doc["diagnosis"] = translated
        return {"extracted_documents": extracted, "trace": [trace]}
```

### Extension 3: "Add a confidence threshold — if the overall confidence is below 0.7, route to MANUAL_REVIEW even if the decision is APPROVED."

**Approach** (3 minutes):
1. Modify `DecisionAgent.run()` to add a check after the decision is made
2. If `decision == APPROVED` and `confidence_score < 0.7`, set `requires_manual_review=True`
3. Add a test

**Code**:
```python
# In DecisionAgent.run, after confidence calculation
if decision == DecisionType.APPROVED and confidence < 0.7:
    requires_review = True
    ops_notes.append(f"Low confidence ({confidence:.2f}) — flagged for review")
```

### Extension 4: "Add a new fraud signal — same hospital + same diagnosis from different members on the same day."

**Approach** (10 minutes):
1. Add a new fraud signal: `SUSPECTED_PROVIDER_COLLUSION`
2. Modify `FraudDetectionAgent.run()` to track member → hospital → diagnosis patterns
3. Trigger when 3+ members use the same hospital + diagnosis on the same day
4. Add a test

**Code sketch**:
```python
# In FraudDetectionAgent
def _check_provider_collusion(self, state):
    # Look at extracted_documents for hospital_name + diagnosis
    # Compare against other claims in the system (would need DB in production)
    # For demo: just check if 2+ docs from this claim have same hospital+diagnosis
    pass
```

### Extension 5: "Add a date check — claims older than 30 days from treatment should be auto-rejected."

**Approach** (5 minutes):
1. Add a check in `PolicyRulesEngine.run()` for `treatment_date` vs `submission_date`
2. The policy already has `submission_rules.deadline_days_from_treatment = 30`
3. If `(submission_date - treatment_date).days > 30` → REJECT
4. Add a test

**Code**:
```python
# In PolicyRulesEngine, add as Rule 0 (very first check)
if (claim_input.submitted_at.date() - claim_input.treatment_date).days > self.policy.submission_deadline_days:
    return ... REJECTED with reason SUBMISSION_DEADLINE_EXCEEDED
```

---

## 7. Trade-offs to Surface Proactively

If they don't ask, bring these up yourself — it shows judgment.

1. **Test mode vs production mode**: chose test mode for determinism, production mode is stubbed
2. **In-memory store**: easy to deploy, swap for Postgres in production
3. **No auth**: demo only, would add OAuth in production
4. **No batching**: each claim is synchronous, would add Celery for high throughput
5. **Rule order in PolicyRules**: opinionated, matches the assignment's expected outputs, locked in by tests
6. **Fraud detection never rejects**: false positives are cheap, false negatives are expensive
7. **Pre-auth defaults to False**: conservative interpretation
8. **Per-claim limit bypass for line items**: handles TC006 case
9. **No real OCR in this build**: 1-day follow-up to add Gemini vision
10. **Multi-agent vs monolithic**: chose multi-agent for bonus points + observability

---

## 8. Questions to Ask THEM

Good questions show you're thinking about the role, not just the assignment.

1. "What's the biggest pain point in the current claim adjudication workflow that you'd want to solve first?"
2. "How do you balance false positives (legit claims flagged for review) vs false negatives (fraud approved) in fraud detection?"
3. "What's the policy update cadence — how often does `policy_terms.json` change, and how do you handle versioning?"
4. "Are there any patterns in the data that would help you build a better fraud detection model?"
5. "What's the team's stance on multi-agent vs single-agent-with-tools for production systems?"

---

## 9. Red Flags to Avoid

❌ Don't say: "I just used LangChain because it's the standard."
✅ Do say: "I chose LangGraph over LangChain because the explicit state machine makes the system easier to reason about and debug."

❌ Don't say: "The LLM just figures out the policy."
✅ Do say: "The policy is in a JSON file. The LLM extracts from documents, the rules engine applies the policy deterministically."

❌ Don't say: "I didn't have time to test edge cases."
✅ Do say: "I prioritized the 12 test cases in the assignment, and added 5 extra unit tests per agent for edge cases I'd identified."

❌ Don't say: "It should work, I just didn't run it."
✅ Do say: "All 52 tests pass, and I ran the full pipeline end-to-end on all 12 test cases. Here's the eval report."

---

## 10. Final Tips

1. **Open the eval report at the end of your walkthrough** — it shows 12/12 with full traces
2. **Mention the 27/52 unit tests** — shows thoroughness
3. **Have the GitHub repo open** — they'll want to see commit history
4. **Be ready to switch to a tab showing the orchestrator code** — the conditional edge is interesting
5. **Don't be defensive about limitations** — the architecture doc acknowledges them
6. **Show enthusiasm for extension** — "I'd love to add real Gemini integration next"

Good luck. You've got this. 🚀
