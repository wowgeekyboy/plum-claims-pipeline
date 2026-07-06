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
