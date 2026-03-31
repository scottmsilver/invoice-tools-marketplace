---
name: matcher
description: "Match parent invoice line items to supporting documents. Compare what the GC claims vs what the actual documents say. Flag every mismatch — invoice numbers, amounts, dates, vendor names."
---

# Invoice Line Matcher

## Purpose

Compare two independent records and find every mismatch:
- **Parent line items** = what the GC claims on their summary (extracted from the GC's detail page)
- **Supporting documents** = what the actual vendor invoices/receipts say (extracted from each document)

These were extracted independently by the ingest skill. The parent values reflect what the GC wrote; the supporting doc values reflect what the vendor wrote. Your job is to pair them up and flag every difference, no matter how small. A $0.02 amount difference, a transposed digit in an invoice number, a date that's off by a day — all of these matter because they indicate either (a) a GC data entry error or (b) something more concerning.

**You are the diff engine between two sources of truth.**

## Input

The path to `extracted.json` produced by `invoice:ingest`. This file contains:
- `parent_invoice.line_items` — what the GC claims (transcribed from the GC's summary page)
- `supporting_documents` — what the actual invoices/receipts say (transcribed from each document)

## Output

Write `matching_results.json` to the working directory:

```json
{
  "matched_pairs": [
    {
      "parent_line_item_id": "li_001",
      "supporting_document_id": "supp_001",
      "match_type": "exact",
      "match_signals": ["invoice_number", "vendor_name", "amount"],
      "amount_difference": 0.00,
      "invoice_number_match": true,
      "vendor_name_match": true,
      "notes": ""
    }
  ],
  "unmatched_parent_items": [
    {
      "line_item_id": "li_019",
      "reason": "Internal GC labor charge — no external backup expected",
      "severity": "info"
    }
  ],
  "unmatched_supporting_docs": [],
  "discrepancies": [
    {
      "parent_line_item_id": "li_015",
      "supporting_document_id": "supp_012",
      "field": "invoice_number",
      "parent_value": "305970",
      "supporting_value": "303970",
      "severity": "warning",
      "notes": "Likely typo — amounts and vendor match"
    }
  ]
}
```

## Matching Strategy

Apply two strategies in order:

### 1. Greedy Match (fast)

For each parent line item, try to find a supporting document match using these criteria in priority order:

1. **Exact invoice number + exact amount**: Highest confidence
2. **Exact invoice number + amount within 2%**: High confidence (tax/rounding differences)
3. **Exact vendor + exact amount**: High confidence (when invoice numbers are missing)
4. **Fuzzy vendor + amount within 2%**: Medium confidence

### 2. Semantic Validation (verify matches make sense)

For each greedy match, verify semantic consistency:
- Does the supporting doc's description relate to the parent line item's description?
- Is the vendor name a reasonable match (accounting for GC prefixes, abbreviations, DBAs)?
- Is the date reasonable relative to the billing period?

Flag matches where amounts match but descriptions are unrelated (could be a coincidental amount match).

## Construction-Specific Patterns

**GC-prefixed vendors**: "LW - Home Depot" on the summary matches "Home Depot" on the receipt. Strip GC prefixes before comparing.

**Internal GC charges**: These have NO supporting document by design. Do not treat as errors:
- Labor charges from the GC (e.g., "GC Name - Jobsite Labor")
- Credits from the GC (e.g., "GC Name - Credit for [item]")

Instead, list them in `unmatched_parent_items` with severity "info" and reason "Internal GC charge".

**Credits**: Match each credit to its corresponding charge (same vendor, opposite sign). Note whether the credit fully or partially offsets the charge.

**Progress billings**: The supporting doc may show a cumulative contract amount while the parent summary shows only the incremental amount. Match on the incremental "amount due" / "due this pay request", not the contract total.

## Post-Match Field Comparison

After pairing a parent line item with its supporting document, compare EVERY field and record discrepancies:

| Field | Parent Source | Supporting Source | Flag If |
|-------|-------------|-------------------|---------|
| invoice_number | GC's Invoice # column | Document's invoice number header | Any difference at all (even one digit) |
| amount | GC's Amount column | Document's total/amount due | Any difference (even $0.01) |
| date | GC's Date column | Document's invoice date | Different date |
| vendor | GC's Supplier column | Document's letterhead | Name mismatch beyond GC prefix |

For each matched pair, set `invoice_number_match`, `amount_match`, `date_match`, and `vendor_name_match` booleans. Any `false` value generates a discrepancy entry.

This is the core value of the matcher: surfacing what the GC got wrong on their summary relative to the actual paperwork.

## Construction Synonym Pairs

Use these for semantic matching:
- "rough materials" = "lumber", "framing materials", "building supplies"
- "equipment rental" = "tool rental", "lift rental", "scaffold"
- "jobsite materials" = "misc materials", "supplies"
- "finish carpentry" = "trim", "millwork", "cabinetry labor"
- "painting materials" = "paint", "primer", "surface protection"

## Discrepancy Severity

- **error**: Amount mismatch > 2%, or supporting doc total doesn't match parent at all
- **warning**: Invoice number mismatch, small amount difference ($0.01-2%), date outside billing period
- **info**: Internal GC charge, receipt-only backup (no formal invoice), credit card statement instead of invoice
