---
name: matcher
description: "Use when matching parent invoice line items to supporting documents in a construction draw request. Trigger when the user has a detail summary with line items and a set of supporting invoices that need to be cross-referenced, verified, and matched."
---

# Invoice Line Matcher

## Purpose

Match each line item on a GC's detail summary page to its corresponding supporting invoice in the draw package. Handle cases where descriptions don't match exactly, amounts differ slightly, or vendor names vary.

## Implementation

Use `lib.ValidationService`. It runs all verifications in parallel and supports two matching algorithms:

- **Greedy** (default): fast, local optimization — exact match on invoice totals, then fuzzy (2% tolerance), then line-item matching
- **Bipartite** (optimal): builds a cost matrix and solves via Hungarian algorithm for globally optimal assignment. Enable with `USE_BIPARTITE_MATCHING=true` env var.

Both algorithms include AI-powered semantic validation to flag matches where amounts match but descriptions don't make sense.

```python
from lib.llm_service import LLMService
from lib.validation_service import ValidationService

llm = LLMService(provider="gemini", api_key=os.environ["GEMINI_API_KEY"])
validation = ValidationService(llm)

# parent_invoice and supporting_invoices are lib.Invoice objects
verifications = validation.validate_invoice(
    parent_invoice, supporting_invoices,
    parent_pdf_bytes=pdf_bytes, parent_source_id=upload_id
)

for v in verifications:
    print(f"{v.type.value}: {v.status.value} — {v.evidence}")
```

## Verification Types Run

The `validate_invoice` method runs these checks in parallel:
1. **work_requested** — PO/contract/authorization found?
2. **totals_match** — line items sum to invoice total?
3. **correct_recipient** — billed to the right entity?
4. **has_supporting_details** — supporting docs cover the parent total?
5. **reasonable_cost** — any wildly expensive or absurd charges?
6. **work_completed** — completion date, signature, or approval stamp?
7. **receipt_exists** — each parent line item matched to a supporting invoice?
8. **unmatched_supporting_invoice** — any supporting docs not referenced by parent?

## Construction-Specific Patterns

- GC prefix on vendor names (e.g., "GC - Home Depot") means purchased by GC staff — match to "Home Depot"
- Internal GC charges (labor, credits) have no supporting invoice by design — don't treat as errors
- Credits always have a matching charge somewhere (same vendor, opposite sign)
- Progress billings show cumulative amounts — the "amount due" column is the incremental draw

## Construction Synonym Pairs

These help with semantic matching:
- "rough materials" = "lumber", "framing materials", "building supplies"
- "electrical supplies" = "wire", "conduit", "breakers", "panels"
- "equipment rental" = "tool rental", "lift rental", "scaffold"
- "jobsite materials" = "misc materials", "supplies"
- "finish carpentry" = "trim", "millwork", "cabinetry labor"
- "painting materials" = "paint", "primer", "surface protection"
