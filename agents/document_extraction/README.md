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
