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
