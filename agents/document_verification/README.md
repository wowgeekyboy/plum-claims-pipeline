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
