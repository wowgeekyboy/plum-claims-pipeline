# Component Contracts — Plum Claims Pipeline

> **Deliverable #3** — precise, machine-checkable contracts for every significant component.
> Each component's contract is precise enough that another engineer could reimplement it
> without reading its code.

This document is auto-generated from the per-agent README.md files in
`agents/*/README.md`. To regenerate: `python scripts/generate_contracts.py`

---

## Index of Components

| # | Component | Purpose | Reads | Writes |
|---|-----------|---------|-------|--------|
| 1 | DocumentVerification | Catch wrong/missing docs early | Claim input | `document_verification` |
| 2 | DocumentExtraction | Extract structured fields from docs | Verified documents | `extracted_documents` |
| 3 | MemberValidation | Member + waiting periods | Member, diagnosis | `member_validation` |
| 4 | PolicyRules | Sub-limits, co-pay, exclusions, pre-auth | Extracted bill + category | `policy_evaluation` |
| 5 | FraudDetection | Same-day/monthly limits, high-value | Claim history | `fraud_evaluation` |
| 6 | Decision | Synthesize all signals → final | All upstream | `decision` |
| 7 | Orchestrator | LangGraph state machine wiring | Claim | All of the above |

---

## 1. Document Verification

# DocumentVerification Agent — Contract

## Purpose
First gate in the pipeline. Catches document problems **before** any expensive LLM calls, with **specific** error messages the member can act on.

## Why it runs first
- Saves LLM cost (don't call Gemini on a wrong-type doc)
- Saves time (instant feedback)
- Builds user trust (the assignment grades on message specificity)

## Input
```python
DocumentVerificationInput:
  claim_id: str
  claim_category: str           # e.g. "CONSULTATION"
  documents: list[dict]         # raw document inputs from the claim
  member_name: str              # for cross-patient detection
```

## Output
```python
DocumentVerificationResult:
  is_valid: bool                # True = pass, False = stop
  stop_processing: bool         # True = orchestrator halts
  errors: list[DocumentError]   # structured error codes
  warnings: list[str]           # non-fatal notes
  user_message: str             # SPECIFIC message for the member
  per_document_results: list[DocumentCheckResult]
  documents_present: list[DocumentType]
  documents_missing: list[DocumentType]
  confidence: float             # 0-1
```

## Errors Raised
None — the agent always returns a result. If there's a problem, `is_valid=False` and `user_message` explains it. The agent never crashes the pipeline.

## Behavior Rules
1. **Required docs check**: Read `policy.document_requirements[claim_category]['required']`. If any are missing → `MISSING_REQUIRED_DOCUMENT` error, `stop_processing=True`.
2. **Wrong type detection (TC001)**: If a doc's `actual_type` is in `required` but appears more than once (e.g. two PRESCRIPTIONs for a CONSULTATION that requires PRESCRIPTION + HOSPITAL_BILL), produce `WRONG_DOCUMENT_TYPE` error with a message naming the uploaded type and the missing type.
3. **Unreadable detection (TC002)**: If any doc has `quality=UNREADABLE`, produce `UNREADABLE_DOCUMENT` error with a message naming the specific file and asking for re-upload.
4. **Patient mismatch (TC003)**: If `patient_name_on_doc` differs from `member_name` across documents, produce `PATIENT_NAME_MISMATCH` with the specific names found.
5. **Duplicate detection**: If the same `actual_type` appears twice, flag `DUPLICATE_DOCUMENT` warning.

## User Message Examples
- TC001: *"You uploaded 2 prescriptions for a consultation claim. A consultation requires a prescription AND a hospital bill. Please upload the hospital bill (itemized receipt from the clinic/hospital)."*
- TC002: *"Your pharmacy bill (file: blurry_bill.jpg) is not readable. Please re-upload a clear photo showing the medicine names, quantities, and total amount."*
- TC003: *"The documents you uploaded are for different patients. We found 'Rajesh Kumar' on the prescription and 'Arjun Mehta' on the hospital bill. Both documents must be for the same patient."*

## Confidence
- 1.0 when `actual_type` is explicitly provided for all docs
- 0.85 when types are detected by Gemini (production mode)
- Drops by 0.1 per warning

## Test Cases Covered
- TC001 (wrong doc type)
- TC002 (unreadable)
- TC003 (patient mismatch)
- TC006 (missing prescription for DENTAL — bill only is fine, no error)
- All other TCs as a pre-flight check

---

## 2. Document Extraction

# DocumentExtraction Agent — Contract

## Purpose
Extracts structured fields from each uploaded document. Produces the data the rest of the pipeline reasons on.

## Why this agent exists
The assignment says: *"Documents will not be clean. Expect handwritten prescriptions, rubber stamps over text, phone photos of bills, and inconsistent formats."* We need an LLM to handle that mess.

## Input
```python
DocumentExtractionInput:
  claim_id: str
  documents: list[dict]        # DocumentInput dicts
  skip_if_unverified: bool     # orchestrator passes this
```

## Output
```python
DocumentExtractionResult:
  extracted_documents: list[ExtractedDocument]
  documents_skipped: list[str]   # unreadable file IDs
  average_confidence: float
  total_extraction_time_ms: float
  llm_calls_made: int
```

## ExtractedDocument shape
```python
ExtractedDocument:
  file_id: str
  document_type: DocumentType
  patient_name: str | None
  doctor_name: str | None
  doctor_registration: str | None
  date: str | None             # YYYY-MM-DD
  diagnosis: str | None
  medicines: list[str]
  tests_ordered: list[str]
  treatment: str | None
  hospital_name: str | None
  line_items: list[LineItem]   # {description, amount, quantity}
  total_amount: float | None
  is_readable: bool
  extraction_confidence: float # 0-1
  warnings: list[str]
  field_confidences: dict[str, float]
```

## Errors Raised
- `LLMTimeoutError` — Gemini call timed out
- `LLMRateLimitError` — Gemini rate limit hit
- `UnparseableOutputError` — Gemini returned non-JSON

In all cases, the agent returns a result with `is_readable=False` for the affected doc and a warning. The pipeline does not crash (TC011).

## Behavior Rules
1. **Test mode** (when `document.content` is set): use the dict directly. No LLM call. `extraction_method="test_mode"`, `llm_calls_made=0`.
2. **Production mode** (when `document.image_base64` is set): call Gemini 2.0 Flash with a structured-output prompt. `extraction_method="gemini_vision"`, `llm_calls_made++`.
3. **Quality=UNREADABLE**: skip extraction, return doc with `is_readable=False`, add to `documents_skipped`.
4. **Field-level confidence**: low-confidence fields (< 0.7) get added to `warnings` and the document's overall `extraction_confidence` is the average.
5. **Always return** a result, even if all extractions fail.

## Confidence Calculation
- Test mode: 1.0 (deterministic)
- Production mode: average of per-field confidences
- Unreadable doc: 0.0 for that doc

## Test Cases Covered
- TC004: extracts consultation prescription + hospital bill with line items
- TC005: extracts diabetes diagnosis (for waiting period check)
- TC006: extracts dental line items (root canal vs whitening)
- TC007: extracts MRI test name (for pre-auth check)
- TC011: simulates LLM failure, returns partial result

## Cost
- Test mode: ₹0
- Production: ~1 Gemini call per document (free tier: 1500 RPD)

---

## 3. Member Validation

# MemberValidation Agent — Contract

## Purpose
Verifies the member is on the policy AND past all relevant waiting periods.

## Why this agent exists
- TC005 needs it: Vikram Joshi joined 2024-09-01, claimed diabetes 2024-10-15, but diabetes has a 90-day waiting period. He's only been a member 44 days. → REJECT.
- Real insurance: ~30% of claims hit a waiting period. This is a high-leverage check.

## Input
```python
MemberValidationInput:
  claim_id: str
  member_id: str               # e.g. "EMP005"
  treatment_date: date         # when the treatment happened
  diagnosis: str | None        # from extraction
  claim_category: str          # e.g. "CONSULTATION"
```

## Output
```python
MemberValidationResult:
  is_valid: bool
  member_found: bool
  member_id: str
  member_name: str | None
  relationship: str | None
  join_date: date | None
  waiting_period_checks: list[WaitingPeriodStatus]
  initial_waiting_passed: bool
  condition_waiting_passed: bool
  days_until_eligible: int
  rejection_reasons: list[RejectionReason]
  user_message: str
  confidence: float
```

## Errors Raised
None. Member not found → `is_valid=False`, `member_found=False`, `rejection_reasons=[MEMBER_INELIGIBLE]`.

## Behavior Rules
1. **Member lookup**: `policy.get_member(member_id)`. If None → `MEMBER_INELIGIBLE`.
2. **Initial 30-day waiting period**: `treatment_date - member.join_date`. If < 30 days → `initial_waiting_passed=False`.
3. **Condition-specific waiting periods**: For each condition in `policy.waiting_periods.specific_conditions`, check if `diagnosis` matches (with medical shorthand: T2DM = Type 2 Diabetes, HTN = Hypertension).
4. **Eligible-from date**: `member.join_date + timedelta(days=required_days)`. If `treatment_date < eligible_from_date` → not eligible. Surface this in the user message.

## Diagnosis → Condition matching
The agent must recognize medical shorthand:
| Short form | Full form | Waiting period |
|------------|-----------|----------------|
| T2DM, DM, Type 2 DM | Type 2 Diabetes | 90 days |
| HTN | Hypertension | 90 days |
| Hypothyroidism | Hypothyroidism | 90 days |
| Maternity, Pregnancy | Maternity | 270 days |
| Cataract | Cataract | 365 days |
| Hernia | Hernia | 365 days |

We use a simple keyword/shorthand lookup table. In production, this would be an LLM call (TC012's bariatric uses this).

## User Message Example
- TC005: *"This claim is for Type 2 Diabetes, which has a 90-day waiting period. Your policy was effective from 2024-09-01 and your treatment was on 2024-10-15 — only 44 days have passed. You will be eligible for diabetes-related claims from 2024-11-30."*

## Confidence
- 1.0 when diagnosis matches a known condition exactly
- 0.85 when matched via shorthand
- 0.7 when matched via fuzzy match (LLM)

## Test Cases Covered
- TC005 (diabetes waiting period — REJECT)
- TC012 (obesity excluded condition — also caught here OR in policy_rules)
- All other TCs as a check

---

## 4. Policy Rules

# PolicyRules Engine — Contract

## Purpose
The "math + rules" heart of the system. Applies every policy rule to produce the approved amount. This is the single biggest source of correctness bugs — order of operations matters.

## Why a separate agent
- All financial rules in one place = auditable
- Easy to test in isolation (the math is the math)
- If the policy changes (and it will), only this agent needs updating

## Input
```python
PolicyRulesInput:
  claim_id: str
  claim_category: str
  claimed_amount: float
  treatment_date: str          # YYYY-MM-DD
  hospital_name: str | None    # for network check
  line_items: list[dict]
  diagnosis: str | None
  tests_ordered: list[str]
  pre_auth_obtained: bool
  ytd_claims_amount: float
  annual_opd_used: float
```

## Output
```python
PolicyEvaluation:
  is_valid: bool
  claimed_amount: float
  approved_amount: float
  category_sub_limit: float
  sub_limit_applied: bool
  sub_limit_capped_amount: float | None
  is_network_hospital: bool
  network_discount_percent: float
  network_discount_amount: float
  amount_after_network_discount: float | None
  copay_percent: float
  copay_amount: float
  amount_after_copay: float | None
  per_claim_limit: float
  per_claim_exceeded: bool
  pre_auth_required: bool
  pre_auth_obtained: bool
  high_value_tests: list[str]
  line_item_decisions: list[LineItemDecision]
  rejection_reasons: list[RejectionReason]
  user_message: str
  notes: list[str]
  confidence: float
  calculation_steps: list[str]    # for transparency
```

## Errors Raised
None — returns a result with `is_valid=False` and `rejection_reasons`.

## Order of Operations (CRITICAL — TC010 tests this)

```
1. Per-claim limit check
   If claimed_amount > per_claim_limit: REJECT (do not proceed)
2. Sub-limit per category
   If claimed_amount > category_sub_limit: cap at sub_limit (note in steps)
3. Pre-authorization check
   If high-value test (MRI/CT/PET > threshold) AND !pre_auth_obtained: REJECT
4. Exclusions check (line-item level)
   For each line item, check against:
     - dental_exclusions, vision_exclusions (for those categories)
     - exclusions.conditions (general)
5. Network hospital check
   If hospital_name in policy.network_hospitals: apply network_discount_percent
6. Co-pay
   Apply copay_percent on (claimed - network_discount)
7. Round to nearest rupee
8. Compute approved_amount
```

## Test Cases Covered
- TC004: Consultation ₹1500 → co-pay 10% → ₹1350. (Note: no network discount in this test)
- TC006: DENTAL — root canal ₹8000 approved, teeth whitening ₹4000 rejected. Approved = ₹8000.
- TC007: DIAGNOSTIC MRI ₹15000 > ₹10K threshold, no pre-auth → REJECT
- TC008: Per-claim ₹7500 > ₹5000 limit → REJECT
- TC010: Network Apollo, 20% disc first → ₹3600, then 10% co-pay → **₹3240**
- TC012: Bariatric → EXCLUDED_CONDITION → REJECT

## Calculation Examples
| TC | Claim | Net Disc | Co-pay | Final |
|----|-------|----------|--------|-------|
| TC004 | 1500 | 0% (not network) | 10% | 1350 |
| TC006 | 12000 | 0% | 0% | 8000 (whitening rejected) |
| TC010 | 4500 | 20% = -900 | 10% on 3600 = -360 | 3240 |

## Confidence
- 1.0 when all rules apply cleanly
- 0.9 when sub-limit was applied
- 0.8 when an exclusion matched (LLM-assisted diagnosis check)

---

## 5. Fraud Detection

# FraudDetection Agent — Contract

## Purpose
Detect claim patterns that may indicate fraud. The agent **never rejects** — it routes to `MANUAL_REVIEW` for human investigation. This is by design: a false positive (rejecting a legit claim) is far worse than a false negative (a fraudulent claim going through).

## Why a separate agent
- Pluggable: fraud rules evolve as bad actors adapt
- Auditable: every signal is logged in the trace
- Conservative: only flags, never blocks

## Input
```python
FraudDetectionInput:
  claim_id: str
  member_id: str
  treatment_date: str
  claimed_amount: float
  same_day_claims_count: int
  monthly_claims_count: int
  document_warnings: list[str]
```

## Output
```python
FraudEvaluation:
  fraud_score: float              # 0-1
  signals_triggered: list[FraudSignal]
  requires_manual_review: bool
  notes: list[str]
  confidence: float
  user_message: str
  same_day_claims_count: int
  monthly_claims_count: int
  claimed_amount: float
  is_high_value: bool
```

## Errors Raised
None — the agent always returns a result. Even if all signals fail to compute, fraud_score defaults to 0.

## Behavior Rules
1. **Same-day limit**: If `same_day_claims_count > policy.fraud_thresholds.same_day_claims_limit` (default 2) → trigger `SAME_DAY_LIMIT_EXCEEDED`.
2. **Monthly limit**: If `monthly_claims_count > policy.fraud_thresholds.monthly_claims_limit` (default 6) → trigger `MONTHLY_LIMIT_EXCEEDED`.
3. **High-value claim**: If `claimed_amount > policy.fraud_thresholds.high_value_claim_threshold` (default 25000) → trigger `HIGH_VALUE_CLAIM`.
4. **Document alterations**: If `document_warnings` contains "alteration", "cancellation", "duplicate stamp" → trigger `DOCUMENT_ALTERATION`.
5. **Auto-review**: If `claimed_amount > policy.fraud_thresholds.auto_manual_review_above` (default 25000) → set `requires_manual_review=True`.
6. **Fraud score**: weighted sum of signals (0.4 same-day, 0.3 monthly, 0.2 high-value, 0.1 alterations), capped at 1.0.
7. **Manual review trigger**: If `fraud_score > policy.fraud_thresholds.fraud_score_manual_review_threshold` (default 0.8) → set `requires_manual_review=True`.

## Test Cases Covered
- TC009: Same-day claims = 4 (3 history + 1 current) > 2 → trigger + manual review.

## User Message
The `user_message` is for the **ops team**, not the member. Example for TC009:
> *"Member EMP008 has submitted 4 claims on 2024-10-30, exceeding the same-day limit of 2. Patterns: 3 different providers (City Clinic A, B, Wellness Center). Routed to manual review."*

## Why not just REJECT?
- Same-day claims can be legit (e.g. multiple consultations, lab + pharmacy same day)
- High-value claims are normal for serious conditions
- The human reviewer can verify the legitimate case
- Auto-reject creates angry customers + appeal overhead

---

## 6. Decision

# Decision Agent — Contract

## Purpose
The final synthesis. Takes all upstream signals and produces the final decision + confidence + user-facing message.

## Why it exists
Without a dedicated decision agent, the orchestrator would have to merge logic from 5 other agents — hard to reason about, hard to test, and easy to introduce ordering bugs. The Decision agent is the "judge" — it sees the full case and rules.

## Input
```python
DecisionInput:
  claim_id: str
  claimed_amount: float
  document_verification: dict | None
  member_validation: dict | None
  policy_evaluation: dict | None
  fraud_evaluation: dict | None
  failed_agents: list[str]
```

## Output
```python
Decision:
  decision: DecisionType        # APPROVED | PARTIAL | REJECTED | MANUAL_REVIEW
  approved_amount: float
  rejection_reasons: list[RejectionReason]
  confidence_score: float       # 0-1
  user_message: str
  ops_notes: list[str]
  requires_manual_review: bool
  next_steps: list[str]
```

## Errors Raised
None. Always returns a Decision.

## Decision Priority
```
1. If document_verification.is_valid == False: REJECTED (no decision)
   — but the user_message is the verification message, not a generic "rejected"
2. If fraud_evaluation.requires_manual_review == True: MANUAL_REVIEW
3. If policy_evaluation.rejection_reasons is non-empty: REJECTED
   (use policy's user_message)
4. If policy_evaluation.approved_amount < claimed_amount: PARTIAL
5. Otherwise: APPROVED
```

## Confidence Calculation
```
base = average of upstream agents' confidences
penalty = 0.1 per failed_agent (capped at 0.3)
confidence = max(0.5, base - penalty)   # never below 0.5
```

## Graceful Failure (TC011)
If `failed_agents` is non-empty:
- `decision` is still produced (best-effort)
- `confidence_score` is reduced (penalty applied)
- `ops_notes` lists which agents failed
- `user_message` includes a note that manual review is recommended

## User Message Examples

**APPROVED (TC004):**
> *"Your claim for ₹1,500 has been approved. A 10% co-pay of ₹150 has been applied per your policy. ₹1,350 will be reimbursed within 5-7 business days."*

**REJECTED — waiting period (TC005):**
> *"This claim has been rejected. Type 2 Diabetes has a 90-day waiting period. Your policy was effective from 2024-09-01; you will be eligible for diabetes-related claims from 2024-11-30."*

**REJECTED — pre-auth missing (TC007):**
> *"This claim has been rejected because pre-authorization was required for an MRI scan above ₹10,000 and was not obtained. To resubmit, please request pre-authorization from your insurer at least 48 hours before the procedure."*

**MANUAL_REVIEW (TC009):**
> *"Your claim is being reviewed by our team and will be processed within 48 hours. You will receive an update by email."*

**PARTIAL (TC006):**
> *"Your claim has been partially approved. Approved: Root Canal Treatment ₹8,000. Rejected: Teeth Whitening ₹4,000 (cosmetic procedure, not covered under your policy). Total approved: ₹8,000."*

## Test Cases Covered
All 12 — this agent is the final synthesis for every one.

---

## Cross-Component Contracts

### State Contract (`AgentState`)

All agents read from and write to a shared `AgentState` (TypedDict).
See `agents/core/state.py` for the canonical definition.

### Error Handling Contract

- **No agent crashes the pipeline.** Every agent catches its own exceptions and returns a result with `confidence` reduced and `error`/`warnings` populated.
- **Failed agents are visible in the trace.** The orchestrator's trace records which agents failed.
- **Graceful degradation**: if an upstream agent fails, downstream agents operate on whatever data they have (or skip with a `SKIPPED` status).

### Confidence Contract

- Every agent emits a `confidence` value in [0, 1].
- The Decision agent computes the final `confidence_score` as:
  `final = max(0.5, mean(upstream_confidences) - 0.1 * num_failed_agents)`
- Failed agents contribute their last-known confidence (or 0.5 default), and incur a 0.1 penalty each.

### Trace Contract

Every agent emits exactly one `AgentTrace` (success/fail/skip). The trace contains:
- `agent_name`, `status`, `started_at`, `completed_at`, `duration_ms`
- `confidence_contribution` (its confidence)
- `input_summary` / `output_summary` (redacted — no PHI payloads)
- `notes` (free-form) and `error` (if failed)
