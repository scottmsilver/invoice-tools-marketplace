---
name: ingest
description: "Read a construction invoice PDF once, detect document boundaries, and extract raw structured data from every document. Write extracted.json. Never corrects values — downstream skills handle comparison and judgment."
---

# Invoice Ingest

## Purpose

Raw, faithful extraction. Read every page of the PDF exactly once. For each document, transcribe exactly what it says — amounts, invoice numbers, dates, vendor names — without correcting, reconciling, or choosing between conflicting values. The ingest layer is a transcription layer, not an analysis layer.

**You are a camera, not an editor.**

- If the draw summary says invoice #305970 and the supporting doc says #303970, record BOTH values exactly as they appear in their respective documents. Do not pick one.
- If the draw summary says $38.35 and the receipt says $38.33, record BOTH values. Do not "fix" either.
- If a date looks wrong (6/1/2025 for a February 2026 draw), record it as-is. Flag it in warnings, but do not change it.

Corrections, comparisons, and judgments happen downstream in the matcher and analyzer.

## Deep Extraction Requirement

Every page must be read. Every document must be individually extracted. Every amount must be verified against the source. If the PDF has 40 pages with 30 separate invoices, read all 40 and extract all 30.

## Input

A path to one or more PDFs, a directory, or a zip file. If a zip, extract it first. If a directory, scan recursively for PDFs, spreadsheets, and context files.

## Output

Write a JSON file to the working directory: `extracted.json`

### Output Schema

The schema has two independent sections that are extracted from different parts of the PDF. They must NOT influence each other during extraction.

```json
{
  "source_pdf": "/path/to/invoice.pdf",
  "page_count": 40,
  "ingest_timestamp": "2026-03-31T10:00:00Z",

  "parent_invoice": {
    "pages": [1, 2],
    "vendor": "ABC Construction",
    "invoice_number": "Project-MAR26",
    "date": "2026-03-28",
    "bill_to": "Jane Homeowner",
    "line_items": [
      {
        "id": "li_001",
        "supplier": "Apex Drywall",
        "description": "Framing and Durock install at fireplace",
        "amount": 2530.00,
        "invoice_number": "043",
        "date": "2026-02-23",
        "cost_code": "2900",
        "is_credit": false
      }
    ],
    "financial_summary": {
      "subtotal": 204272.08,
      "markup_label": "Builders Comp",
      "markup_rate": null,
      "markup_amount": 30640.81,
      "markup_credits": [
        { "description": "Credit for BC on Elevator Draw", "amount": -10275.00 }
      ],
      "retention": 0.00,
      "retention_note": "reached 75% threshold",
      "invoice_total": 224637.89,
      "prior_balance": 40000.00,
      "total_due": 264637.89
    }
  },

  "supporting_documents": [
    {
      "id": "supp_001",
      "pages": [3],
      "vendor": "Apex Drywall Specialists",
      "invoice_number": "043",
      "date": "2026-02-23",
      "total_amount": 2530.00,
      "amount_due": 2530.00,
      "document_type": "standard_invoice",
      "po_number": "SILV-5500-1",
      "line_items": [
        { "description": "Labor: 52h @ $40", "amount": 2080.00 },
        { "description": "Material", "amount": 450.00 }
      ],
      "math_verified": true,
      "notes": ""
    }
  ],

  "extraction_warnings": []
}
```

### Critical: Parent line items are transcribed from the parent

Each line item in `parent_invoice.line_items` records **exactly what appears on the GC's detail summary page** (typically page 2). The fields come from the summary's columns:

- `supplier` — from the Suppliers column
- `description` — from the Material/Description column
- `amount` — from the Amount column (negative for credits shown in parentheses)
- `invoice_number` — from the Invoice # column **as the GC wrote it**
- `date` — from the Date column **as the GC wrote it**
- `cost_code` — from the LW Act# / Cost Code column

Do NOT look at supporting documents to fill in or correct parent line item fields. The parent line item is a record of **what the GC claims**.

### Critical: Supporting documents are transcribed from the documents themselves

Each entry in `supporting_documents` records **exactly what appears on that vendor's invoice/receipt**. The fields come from the supporting document:

- `vendor` — from the document's letterhead
- `invoice_number` — from the document's invoice number field
- `date` — from the document's date field
- `total_amount` / `amount_due` — from the document's totals

Do NOT look at the parent summary to fill in or correct supporting document fields. The supporting document is a record of **what the vendor claims**.

### Why this matters

The matcher's entire job is to compare these two independent records and find mismatches. If ingest "helps" by reconciling them, the matcher has nothing to find.

Example of what goes WRONG if ingest corrects:
- GC summary says Superdry invoice #1054
- Superdry's actual invoice says #1034
- If ingest "corrects" the parent to say 1034, the matcher sees a perfect match
- The homeowner never learns the GC has the wrong invoice number in their books

Example of what goes RIGHT with raw extraction:
- Parent line item: `"invoice_number": "1054"` (what the GC wrote)
- Supporting doc: `"invoice_number": "1034"` (what Superdry's invoice says)
- Matcher detects: invoice_number mismatch, parent=1054, supporting=1034
- Analyzer flags it as a question for the GC

## Workflow

### Step 1: Read the Parent Invoice (pages 1-2 typically)

Read the cover page and detail summary first. Extract:
- All line items from the detail summary, exactly as written
- The financial summary (subtotal, markup, credits, retention, totals)
- Record the stated amounts — do not compute them

### Step 2: Read Supporting Documents (remaining pages)

Read the rest of the PDF in batches. For each page, detect document boundaries and extract data.

**Boundary detection signals:**
- Different company letterhead/logo (strong)
- New "INVOICE" or "Invoice #" header (strong)
- New "Bill To" / "Ship To" block (strong)
- "Page 1 of N" resets to 1 (strong)

**Multi-page grouping:**
- Pay applications: invoice page + payment request detail (same vendor, same total)
- Labor detail sheets: 2-3 pages of daily entries
- Specialty contractors: invoice + terms page

### Step 3: Extract Each Supporting Document

For each document, extract exactly what it says:

| Field | Required | Notes |
|-------|----------|-------|
| vendor | Yes | Company name from letterhead, exactly as printed |
| invoice_number | If present | Vendor's invoice number, exactly as printed |
| date | Yes | Invoice date from the document |
| total_amount | Yes | Total on the invoice |
| amount_due | Yes | Balance due (may differ from total if prior payments) |
| document_type | Yes | See types below |
| po_number | If present | Purchase order number |
| line_items | Yes | Every line item with description and amount |

**Document types:**
- `standard_invoice` — formal typed invoice
- `receipt_photo` — photographed retail receipt
- `progress_billing` — shows cumulative contract + incremental draw
- `rental_invoice` — equipment rental with period charges
- `pay_application` — AIA-style or custom pay request
- `credit_memo` — all amounts negative
- `labor_detail` — internal timesheet/hours breakdown
- `credit_card_statement` — CC transaction printout (not a proper invoice)

**Progress billing rule:** For progress billings, `amount_due` is the INCREMENTAL amount (due this pay request), not the cumulative contract total. Verify: `amount_due = total_billed_to_date - prior_payments`.

### Step 4: Format Normalization (cosmetic only)

Apply ONLY format normalization. These changes make the data machine-readable without altering the actual values:

- **Dates**: Normalize to YYYY-MM-DD (e.g., "Feb 25th" → "2026-02-25")
- **Amounts**: Strip `$`, commas. Credits in parentheses `(2,149.00)` → `-2149.00`. Round to 2 decimal places.
- **Vendor names on supporting docs**: Use the full name from the letterhead. If the document says "BFS Group LLC" record that, don't shorten to "BFS".

**Do NOT normalize:**
- Invoice numbers (preserve exactly as printed — "0778484S1S" stays as-is, don't "fix" to "0778484515")
- Vendor names on the parent summary (if the GC wrote "LW - Benjamin Moore", record that)
- Amounts that look wrong (if the GC wrote $38.35, record $38.35 even if the receipt says $38.33)

### Step 5: Write extracted.json

Write the complete structured data. Include an `extraction_warnings` array for anything noteworthy during extraction:
- Pages that were hard to read
- Ambiguous amounts (extract best guess, note the ambiguity)
- Dates that look anomalous (record as-is, note it)
- Documents where boundary detection was uncertain

**Warnings are observations, not corrections.** "Parent shows date 6/1/2025 which seems anomalous for a Feb 2026 draw" is a good warning. Changing the date to 2/16/2026 is not — that's the analyzer's job.

## Error Handling

- If a page is unreadable, log it in `extraction_warnings` and continue
- If an amount is ambiguous, extract the most likely reading and note both possibilities in warnings
- If a document spans pages and you're unsure of the boundary, group them and note uncertainty
- Write partial results as you go — if extraction fails mid-PDF, whatever was extracted should still be usable
