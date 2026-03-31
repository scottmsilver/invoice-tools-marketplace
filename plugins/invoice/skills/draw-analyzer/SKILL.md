---
name: draw-analyzer
description: "Analyze and audit monthly construction draw requests from a general contractor. Use this skill whenever the user uploads a contractor draw request PDF, monthly construction invoice, builder's cost draw, or any pay application from their GC. Also trigger when the user mentions 'draw request', 'draw analysis', 'builder invoice', 'construction invoice audit', 'verify the draw', 'check the draw', 'monthly draw', or anything about reviewing what their contractor billed. This skill extracts every line item, verifies all math, flags anomalies (missing invoice numbers, out-of-period dates, unmatched credits, builder's comp errors), and produces a structured audit report. Even if the user just says 'look at this invoice' and it's a multi-page construction draw PDF, use this skill."
---

# Monthly Draw Analyzer

## Purpose

Homeowners managing large residential construction projects receive monthly draw requests from their general contractor. These are multi-page PDFs containing:
- A **cover page** with the total amount due
- A **detail summary page** listing every sub-invoice, credit, and fee with columns for Supplier, Material/Description, Amount, Invoice #, Date, and Cost Code
- A **financial summary** at the bottom: Subtotal, Builder's Comp (markup), BC credits, Retention, Draw Total, any carry-forward balance, and Total Due
- **Supporting invoices** (the actual sub-contractor and vendor invoices backing each line item)

The supporting invoices come in wildly different formats: formal typed invoices, handwritten receipts, photographed receipts, pay applications, progress billings, rental invoices, T&M breakdowns, etc.

Your job is to extract, verify, and audit all of this so the homeowner can make an informed decision about whether to pay the draw, dispute specific items, or request corrections.

**Deep scrub is non-negotiable.** Do not skim the draw or sample a few invoices. Every single line item must be extracted, every supporting invoice must be read, and every amount must be cross-checked. A half-done audit is worse than no audit — it creates false confidence. If a draw has 30 supporting invoices, read all 30. If the math is off by $0.22, flag it.

## Expected Inputs

The preferred input is a **zip file** containing everything for the draw. When the user provides a zip:

1. **Extract it** to a temporary directory using Python's `zipfile` module
2. **Auto-detect the contents** by scanning for file types:
   - `.pdf` files — the draw request and/or standalone invoices
   - `.xlsx` / `.xls` files — the actuals spreadsheet
   - `.md` files — project context
   - `.jpg` / `.png` / `.gif` files — photographed receipts
3. **Identify the draw request PDF** — usually the largest PDF, or the one with "draw" / "request" / the GC name in the filename. If ambiguous, ask.
4. **Identify the actuals spreadsheet** — look for "actuals" / "budget" / "cost" in the filename, or the `.xlsx` file
5. **Load project context** — if a `.md` file is present (e.g., `project-context.md`), read it first

The skill also works with **individual files** pointed to directly (backward compatible):
- A draw request PDF on its own (Steps 1-6 only)
- A draw request PDF + actuals spreadsheet (full analysis including Steps 7-9)
- A single vendor invoice (simplified analysis — math check, flag anomalies, no budget cross-ref)

**The actuals spreadsheet is optional.** Many users won't have one, especially for non-construction invoices (property management, vendors billed directly). If no spreadsheet is found in the zip or working directory, skip Steps 7-9 and produce the report with Steps 1-6 only. Do not ask for it — just note in the report that budget cross-referencing was skipped because no actuals were provided.

### Zip file conventions

The zip can be flat (all files at the root) or have a single top-level directory. Either works. Common layouts:

```
feb-2026-draw.zip
├── Silver Remodel February 2026 Draw Request.pdf
├── Silver Remodel Actuals.xlsx
└── project-context.md
```

or just:

```
invoice-review.zip
├── draw.pdf
└── actuals.xlsx
```

### Actuals spreadsheet

The actuals spreadsheet is project-specific. Common sheet names and their purposes:
- **Cost Codes**: Master budget tracker with columns for Cost Code ID, Description, OG Budget, Revised Budget, monthly draw columns, Invoiced Work To Date, Remaining Cost to Complete, Estimated Total, Variance columns
- **Scope**: Remaining work items by cost code with notes on what's left
- **CO Log**: Change order log
- **Notes/Costs**: Supplementary cost breakdowns for large items

### Project context

Look for a **project-context file** (e.g., `project-context.md`) in the zip or working directory. This file contains project-specific details like GC name, contacts, cost code mappings, Builder's Comp rate, and special billing rules. If found, load it before processing the draw.

If only the draw PDF is uploaded, perform Steps 1-6. If the actuals spreadsheet is also available, additionally perform Steps 7-9 for the budget cross-reference analysis.

## Workflow

### Step 0: Extract Zip (if applicable)

If the user provided a zip file:

```python
import zipfile, tempfile, os

tmpdir = tempfile.mkdtemp(prefix="draw_audit_")
with zipfile.ZipFile(zip_path, 'r') as z:
    z.extractall(tmpdir)

# Find files by extension
pdfs = []
spreadsheets = []
context_files = []
for root, dirs, files in os.walk(tmpdir):
    for f in files:
        full = os.path.join(root, f)
        if f.lower().endswith('.pdf'):
            pdfs.append(full)
        elif f.lower().endswith(('.xlsx', '.xls')):
            spreadsheets.append(full)
        elif f.lower().endswith('.md'):
            context_files.append(full)
```

Pick the draw PDF (largest PDF or best filename match), the actuals spreadsheet, and any context file. Then proceed to Step 1.

### Step 1: Read the PDF

Use the pdf-reading skill or `pymupdf` to read the draw request PDF. The key pages are:
- The **detail summary page** (usually page 2) — this has the structured table of all line items
- The **cover page** (page 1) — has the total due amount to cross-check
- **Supporting invoice pages** (page 3+) — the actual vendor invoices

### Step 2: Extract the Detail Summary Table

From the detail summary page, extract every line item into structured data:

| Field | Description |
|-------|-------------|
| supplier | Vendor/subcontractor name |
| description | Material or service description |
| amount | Dollar amount (negative for credits) |
| invoice_number | Vendor invoice number (may be blank for internal GC charges) |
| date | Invoice date |
| cost_code | Cost code / activity number |
| is_credit | Boolean — true if this is a credit line (shown in parentheses or red) |

Also extract the financial summary block:
- **Subtotal** (sum of all line items including credits)
- **Builder's Comp** (the GC's markup, typically 10-20% of non-credit subtotal)
- **BC Credits** (credits against builder's comp, e.g., for items that shouldn't carry markup)
- **Retention Held** (withheld amount, if any)
- **Draw Total** (Subtotal + Builder's Comp + BC Credits - Retention)
- **Balance Due from Previous Draw** (carry-forward unpaid amount)
- **Total Due** (Draw Total + Previous Balance)

### Step 3: Verify Math

Perform these verification checks:

1. **Line item sum**: Do all extracted amounts sum to the stated Subtotal?
2. **Builder's Comp rate**: Calculate BC as a percentage of the non-credit subtotal. Is it the expected rate? Flag if it deviates from the project's contracted rate.
3. **BC Credit logic**: Do BC credits correspond to specific line items that shouldn't carry markup? Verify the math.
4. **Draw total arithmetic**: Does Subtotal + BC + BC Credits = Draw Total?
5. **Total due**: Does Draw Total + Previous Balance = Total Due?
6. **Cover page cross-check**: Does the detail page Total Due match the cover page amount?

### Step 4: Flag Anomalies

Check for and flag:

- **Missing invoice numbers**: Line items billed to the homeowner without a traceable vendor invoice number (common for internal GC charges — note these separately as they deserve extra scrutiny)
- **Out-of-period dates**: Invoices dated significantly before or after the billing month (e.g., a June 2025 invoice appearing in a February 2026 draw)
- **Unmatched credits**: Credits that don't clearly correspond to a charge (or credits where the offset isn't equal)
- **Large round numbers without detail**: Lump-sum charges without itemized backup
- **Duplicate-looking entries**: Multiple charges from the same vendor for similar descriptions
- **50% deposits on large items**: Flag these for awareness — they represent future obligations
- **Internal labor charges**: Labor billed by the GC without external invoice backup — these rely on the GC's own timesheets
- **Invoices billed to jobsite personnel rather than the homeowner**: These sometimes lack proper PO routing

### Step 5: Cross-Reference Supporting Invoices

For each line item on the summary page, check whether a supporting invoice exists in the PDF:
- Does the invoice amount match what's on the summary?
- Does the vendor name match?
- Does the invoice number match?
- Are there any discrepancies between what the sub-invoice says and what the GC's summary reports?

Note: Some items (internal GC labor, small retail receipts) may have minimal supporting documentation — flag these but don't treat as errors.

### Step 6: Generate the Audit Report

If only the draw PDF was provided, produce the report with Steps 1-5 data. If the actuals spreadsheet was also provided, include Steps 7-9 data as well.

### Step 7: Cross-Reference Against Actuals (requires actuals spreadsheet)

Read the Cost Codes sheet from the actuals spreadsheet. For each cost code that appears in this draw:

1. **Match draw amounts to actuals**: Aggregate the draw's line items by cost code. Compare each code's draw total against the corresponding monthly draw column in the actuals. Flag any mismatches — these could indicate the GC's summary doesn't match their own books.

2. **Budget status check**: For each cost code, calculate:
   - Prior invoiced amount (total invoiced minus this draw)
   - Total invoiced including this draw
   - Whether the code is over its **revised budget** (this is the most actionable flag)
   - Whether the code is significantly over the **original estimate** (>$10K over, useful for tracking scope creep)

3. **Remaining budget**: Show how much is left in each code's budget after this draw. Codes with $0 remaining that are still showing charges deserve extra scrutiny.

### Step 8: Check Scope & Change Orders (requires actuals spreadsheet)

1. Read the **Scope** sheet. For cost codes in this draw, pull the scope notes to show what work was originally anticipated.
2. Read the **CO Log**. Check if any charges in this draw correspond to change orders. Flag charges that look like changed scope but have no corresponding CO entry.
3. Check the **PO/vendor allocation columns** in the Cost Codes sheet. Verify that vendors billing against a cost code are the expected vendors for that PO.

### Step 9: Budget Trend Analysis (requires actuals spreadsheet)

Identify the most concerning budget trends across the full project:
- Cost codes where invoiced amount exceeds revised budget (these are actively over-budget)
- Cost codes where the original estimate has been revised upward by more than 50% (indicates scope creep or poor initial estimation)
- Large remaining budget items that haven't started yet (upcoming financial exposure)

### Step 10: Generate the Audit Report

Produce TWO outputs:

**1. A structured spreadsheet (.xlsx)** with these tabs:
- **Summary**: High-level overview — total draw amount, math check results, cost codes over budget, and a list of specific questions for the GC. This should be the first tab the homeowner sees.
- **Line Items**: All extracted line items with columns: Supplier, Description, Amount, Invoice #, Date, Cost Code, Is Credit, Flags
- **Budget Cross-Reference** (if actuals provided): One row per cost code in this draw showing: This Draw amount, Prior Invoiced, Total Invoiced, OG Budget, Revised Budget, Estimated Total, Over Revised? (YES/NO with conditional formatting), $ Over Revised, $ Over OG, Scope Notes
- **Math Verification**: Each check performed, expected value, actual value, pass/fail. Include both arithmetic checks and cross-reference checks.
- **Flags & Issues**: Every anomaly found (both invoice-level and budget-level), severity (Info/Warning/Error), description, affected item, details. Sort by severity (Errors first).

**2. A concise narrative summary** in the chat that covers:
- Total draw amount and what it covers at a high level
- Any math errors found
- The most important flags/issues the homeowner should review
- **Budget cross-reference findings** (if actuals provided): Which cost codes are over budget, by how much, and whether the overruns happened this month or were pre-existing
- Specific questions the homeowner should ask the GC (numbered, actionable)
- Recommendation: pay as-is, pay with noted exceptions, or dispute

## Important Context

### General (applies to any project)

- Builder's Comp (BC) is the GC's markup, typically a consistent percentage applied to costs. Credits against BC are used when specific items shouldn't carry markup (e.g., items with separate contractual terms).
- Cost codes map vendor charges to budget categories. The actuals spreadsheet defines the mapping for each project.
- When a project context file exists alongside the draw PDF (e.g., `project-context.md` or similar), read it for project-specific details like GC name, contacts, cost code mappings, prior disputes, and special billing rules.
- If no project context file is found, extract what you can from the draw itself (GC name from letterhead, cost codes from the summary, markup rate from the financials) and ask the user for anything else you need.

### Project-Specific Context

Project-specific details (GC name, contacts, address, cost code mappings, Builder's Comp rate, actuals spreadsheet filename, prior dispute history, special billing rules) should be stored in a `project-context.md` file in the working directory. This keeps sensitive project data local and out of the skill itself.

If no project context file is found, the skill will extract what it can from the draw PDF and prompt the user for the rest.

## Implementation Library

The plugin includes a Python library at `lib/` with tested implementations for each processing step. Use these rather than writing from scratch:

- `lib.PDFExtractor` — PDF text extraction and page splitting (PyMuPDF)
- `lib.LLMService` — multi-provider LLM calls (Gemini/Claude/OpenAI) with rate limiting, caching, JSON repair
  - `llm.detect_document_boundaries(pages_text, pdf_bytes)` — find where each invoice starts/ends
  - `llm.extract_invoice_data(doc_type, pdf_bytes)` — extract structured data from a single invoice
  - `llm.validate_extraction(invoice, pdf_bytes)` — verify extraction accuracy
  - `llm.analyze_invoice_deep(invoice, pdf_bytes)` — fraud/quality analysis
- `lib.ValidationService` — line item matching with greedy and bipartite algorithms
  - `validation.validate_invoice(parent, supporting_invoices)` — runs all verifications in parallel
- `lib.ImageConverter` — convert photographed receipts (GIF/PNG/JPG) to PDF

Install dependencies: `pip install -r ${CLAUDE_PLUGIN_ROOT}/lib/requirements.txt`

## Tips for Accuracy

- Credit lines in the detail summary are often shown in parentheses and/or bold/italic/red text. The OCR text will show them as negative numbers or with parentheses like `(2,149.00)`.
- Lumber yard invoice amounts in the summary may appear misaligned in the OCR — cross-check against the actual invoices in the supporting pages.
- Some invoices have PO numbers (e.g., `PROJ-2900-2`, `PROJ-4750-1`) which map to cost codes. These can help verify correct cost code assignment.
- Progress billings show cumulative contract amounts with percentage completion — the "amount due" is the incremental draw, not the total contract value.
- Use `invoice:cleanup` when extracted data needs normalization (vendor names, dates, progress billing math).
- Use `invoice:email-drafter` to turn audit findings into a plain-text email for the GC.
