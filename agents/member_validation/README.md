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
