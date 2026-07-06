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
