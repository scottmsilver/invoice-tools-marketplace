---
name: analyzer
description: "Audit a contractor invoice: orchestrate ingestion, verify math, cross-reference budget, match line items, flag anomalies, and produce a structured report. Use when reviewing any contractor billing."
---

# Invoice Analyzer

## Purpose

Orchestrate a full audit of a construction draw request or invoice. This skill does NOT read the PDF itself — it dispatches `invoice:ingest` to do that, then works from the structured JSON output.

The analyzer's job is judgment: verify math, cross-reference budgets, detect anomalies, ask smart questions, and produce a report the homeowner can act on.

## Expected Inputs

You may be given a single file, multiple files, a directory, or a zip file. Common file types:
- `.pdf` — invoices, receipts, pay applications (the main input)
- `.xlsx` / `.xls` — actuals spreadsheets, budgets (optional, for budget cross-referencing)
- `.md` — project context files

If no actuals spreadsheet is provided, skip budget cross-referencing steps and note it in the report.

## Dispatch Strategy

After identifying the input files, decide how to process them:

### Ingest (PDF reading + extraction)

```
IF page_count <= 8 AND structure looks simple:
  Run ingest INLINE (read the PDF yourself, extract data, skip sub-agent)
ELSE:
  Dispatch invoice:ingest as a SUB-AGENT via the Agent tool
  Input: PDF path
  Output: extracted.json in the working directory
```

For most real-world GC draw requests (20-50 pages), always use a sub-agent.

### Matching (line items vs supporting docs)

```
IF line_items <= 7 AND supporting_documents <= 4:
  Run matching INLINE
ELSE:
  Dispatch invoice:matcher as a SUB-AGENT via the Agent tool
  Can run in PARALLEL with math verification
  Input: path to extracted.json
  Output: matching_results.json in the working directory
```

### Price Check (cost reasonableness)

```
IF first run for this project OR user requests it:
  Dispatch invoice:price-check as a SUB-AGENT via the Agent tool
  Can run in PARALLEL with matcher and math verification
  Input: path to extracted.json
  Output: price_check_results.json in the working directory
ELSE:
  Skip — vendor rates don't change month to month
```

The price-check agent examines supporting document line items for labor rates, material unit prices, equipment rental rates, and contract-level pricing. It compares against market rates for the project's location.

### Everything else: INLINE

Math verification, budget cross-reference, anomaly detection, and report generation always run inline in the orchestrator.

## Workflow

### Step 0: Identify Inputs and Project Context

Scan the provided path for files. Identify:
- The main invoice PDF
- Any actuals spreadsheet
- Any project context file (`project-context.md`)

**If `project-context.md` exists:** Load it. It contains answers from prior runs — markup rate, GC contacts, known vendors, prior disputes, etc. Skip questions the user has already answered.

**If `project-context.md` does NOT exist:** This is the first run for this project. Before doing any analysis, run the **First-Run Interview** (see below). This ensures you have the critical context needed for an accurate audit. Do not skip this — an audit without knowing the contracted markup rate, for example, can't verify the most important line on the invoice.

#### First-Run Interview

Use the **AskUserQuestion** tool to ask these questions interactively. Group them into 1-2 AskUserQuestion calls (max 4 questions per call). Provide sensible options for each question so the user can pick from common answers rather than typing everything.

**Call 1 — Core project details (always ask):**

```
AskUserQuestion({
  questions: [
    {
      question: "What is the contracted Builder's Comp / markup rate?",
      header: "Markup Rate",
      options: [
        {label: "15%", description: "Most common residential GC rate"},
        {label: "12%", description: "Lower end for larger projects"},
        {label: "10%", description: "Negotiated rate"},
        {label: "18%", description: "Higher end, smaller GCs"}
      ],
      multiSelect: false
    },
    {
      question: "What is the retention policy?",
      header: "Retention",
      options: [
        {label: "10% until 75% complete", description: "Standard — 10% held, released at 75% project completion"},
        {label: "10% until substantial", description: "10% held until substantial completion"},
        {label: "5% until 50%", description: "Lower retention, earlier release"},
        {label: "No retention", description: "Not part of this contract"}
      ],
      multiSelect: false
    },
    {
      question: "Is it normal for this GC to include invoices dated after the draw period?",
      header: "Billing Period",
      options: [
        {label: "Yes, normal", description: "They batch invoices as they come in, dates don't need to match"},
        {label: "No, should match", description: "All invoices should be dated within the billing month"},
        {label: "Not sure", description: "Haven't tracked this before"}
      ],
      multiSelect: false
    },
    {
      question: "Any cost codes you're already watching or concerned about?",
      header: "Watchlist",
      options: [
        {label: "Nothing specific", description: "No particular codes to flag"},
        {label: "A few codes", description: "I have some I'm tracking"},
        {label: "Several codes", description: "Multiple budget lines I'm watching"}
      ],
      multiSelect: false
    }
  ]
})
```

If the user says they're watching codes, ask a follow-up AskUserQuestion with the specific cost codes from the actuals spreadsheet as multiSelect options.

**After answers received**, also extract GC name, contacts, and homeowner name from the invoice cover page (usually available without asking).

After the user answers, **write `project-context.md`** in the working directory with all the answers, structured like this:

```markdown
## Project Details
- GC: [name]
- Contacts: [names, emails, phones]
- Homeowner: [name]
- Project: [name/address]
- Markup Rate: [%]
- Retention: [policy]
- Billing Period: [policy]

## Known Vendors
- [Vendor]: [what they do, any special terms]

## Budget Notes
- [any codes being watched, pending COs]

## Prior Disputes / Open Items
- [carry-forward items]
```

On subsequent runs, the analyzer loads this file and only asks about things that are new or changed.

### Step 1: Ingest

Dispatch or run `invoice:ingest` on the PDF. Wait for `extracted.json`.

Read `extracted.json` into your working context. You now have:
- All line items from the parent invoice summary
- All supporting documents with their extracted data
- The financial summary (subtotal, markup, credits, retention, totals)
- Any extraction warnings

### Step 2: Follow-Up Questions (if needed)

Review the extracted data against the project context. Ask about anything NEW that the project context doesn't already cover:

- Unfamiliar vendors that appeared for the first time
- Unfamiliar charges or credit patterns
- Progress billing context that needs homeowner judgment ("does 60% complete track with actual work?")

**Save what you learn.** Append new answers to `project-context.md` under the appropriate section. Don't re-ask what's already recorded.

### Step 3: Verify Math

Using the extracted financial summary, perform these checks:

1. **Line item sum**: Do all extracted line item amounts sum to the stated subtotal?
2. **Markup rate**: Calculate markup as a percentage of the subtotal. Is it the expected rate?
3. **Markup credit logic**: Do markup credits correspond to specific items? Verify the math.
4. **Invoice total arithmetic**: Subtotal + Markup + Credits - Retention = Invoice Total?
5. **Total due**: Invoice Total + Prior Balance = Total Due?
6. **Cross-check**: Does the cover page total match the detail page total?

### Step 4: Match Line Items to Supporting Documents

Dispatch or run `invoice:matcher` (see dispatch strategy above).

If running inline, for each line item on the parent summary:
- Find the corresponding supporting document by invoice number, vendor name, and amount
- Flag mismatches: wrong invoice number, amount differences, vendor name discrepancies
- Note items with no supporting document (internal GC charges are expected to lack backup)

### Step 5: Flag Anomalies

Check for and flag:
- **Missing invoice numbers**: Line items without traceable vendor invoice numbers
- **Out-of-period dates**: Invoices dated significantly before or after the billing period
- **Invoice number mismatches**: Summary invoice # differs from actual supporting doc
- **Amount mismatches**: Even $0.01 differences between summary and backup
- **Unmatched credits**: Credits that don't clearly correspond to a charge
- **Large round numbers without detail**: Lump-sum charges without itemized backup
- **Duplicate-looking entries**: Multiple charges from same vendor for similar amounts
- **50% deposits**: Flag for awareness — represent future obligations
- **Internal labor**: GC labor billed without external invoice backup
- **Missing documentation**: Credit card statements instead of proper invoices

### Step 6: Cross-Reference Against Actuals (if spreadsheet provided)

Read the actuals spreadsheet. For each cost code in this invoice:

1. **Match amounts**: Aggregate invoice line items by cost code. Compare against the actuals.
2. **Budget status**: For each code, calculate whether it's over the revised budget.
3. **Missing entries**: Check if any invoice charges are NOT in the actuals spreadsheet.
4. **Trend analysis**: Identify codes where invoiced amount exceeds revised budget, or where original estimate was revised upward by more than 50%.

### Step 6.5: Price Check (if dispatched)

If `invoice:price-check` was dispatched, wait for `price_check_results.json`. Incorporate findings into the anomaly list and report. Items flagged `above_market` or `significantly_above` should appear in the Flags & Issues tab and in the narrative.

### Step 6.7: Extract Flagged Pages

For every supporting document that has a Warning or Error-level flag (price overcharge, invoice number mismatch, missing itemization, budget overrun, or any issue the homeowner should see with their own eyes), extract the relevant pages from the source PDF into a standalone PDF.

Use PyMuPDF to extract pages:
```python
import fitz, os
os.makedirs("extracted_pages", exist_ok=True)
src = fitz.open(source_pdf_path)
out = fitz.open()
out.insert_pdf(src, from_page=page_num-1, to_page=page_num-1)  # 0-indexed
out.save(f"extracted_pages/{vendor}_{invoice_num}.pdf")
```

Name each file descriptively: `{Vendor}_{InvoiceNum}.pdf` (e.g., `Hearth_Home_303970.pdf`).

For multi-page supporting docs (like the Arches pay request), extract all pages belonging to that document.

The `extracted.json` has the page numbers for each supporting document in the `pages` field — use those directly. No need to re-detect boundaries.

This gives the homeowner the actual source document for anything flagged, so they can verify findings and share specific pages with the GC.

### Step 6.8: Consolidate Findings

Before generating reports, write `audit_findings.md` — a single human-readable document that consolidates ALL findings from every source (math, matcher, price-check, budget). This is the master findings document that the email-drafter reads.

Structure it as:

```markdown
# Audit Findings — [Invoice ID] [Date]

## Math
- [each check: pass/fail with detail]

## Discrepancies (from matcher)
- [each field mismatch: what GC says vs what doc says]

## Price Check Flags
- [each above_market or significantly_above item with the dollar comparison]
- [e.g., "Mason Lite MFP44: GC billed $8,295, retail is $2,800-$3,500. $4,500-$5,500 potential overpay."]
- [e.g., "Gas log components: $5,890 lump sum with no itemization. Typical range $2,000-$4,000."]

## Budget Overruns
- [each code over revised budget with $ amount and whether a CO exists]

## Other Flags
- [50% deposits, missing backup, out-of-period dates, credit patterns, retention changes]

## Extracted Pages
- [list each extracted PDF with the reason it was flagged]
```

**This step is critical.** The email-drafter reads `audit_findings.md` as its primary input. If a finding isn't in this file, it won't make it into the email. Every price-check flag, every matcher discrepancy, every budget overrun MUST appear here.

### Step 7: Generate Report

Produce TWO outputs:

**1. A structured spreadsheet (.xlsx)** with these tabs:
- **Summary**: Total invoice amount, math check results, budget alerts, questions for GC
- **Line Items**: Every line item with columns: Supplier, Description, Amount, Invoice #, Date, Cost Code, Is Credit, Flags
- **Budget Cross-Reference** (if actuals provided): Cost code, This Invoice, Prior Invoiced, Total, OG Budget, Revised Budget, Over Revised?, $ Over
- **Math Verification**: Each check, expected value, actual value, pass/fail
- **Price Check** (if run): Items checked, market range, assessment, notes
- **Flags & Issues**: Every anomaly found, severity (Error/Warning/Info), description, affected item

**3. Extracted page PDFs** in the `extracted_pages/` directory for every flagged supporting document. Name: `{Vendor}_{InvoiceNum}.pdf`.

**2. A concise narrative summary** in the chat covering:
- Total amount and what it covers
- Math errors found
- Most important flags
- Budget cross-reference findings (if actuals provided)
- Price check findings (if run) — focus on items above market
- Numbered questions for the GC
- Recommendation: pay as-is, pay with exceptions, or dispute

## Important Context

### Builder's Comp (Markup)
Builder's Comp is the GC's markup, typically a consistent percentage applied to costs. Credits against BC are used when specific items shouldn't carry markup (e.g., items with separate contractual terms, items being reversed).

### Cost Codes
Cost codes map vendor charges to budget categories. The actuals spreadsheet defines the mapping. When aggregating by cost code, include both charges and credits assigned to that code.

### Project Context
Project-specific details should be in `project-context.md` in the working directory. If not found, extract what you can from the invoice itself and ask the user for the rest.

## Tips for Accuracy

- Credit lines are often shown in parentheses and/or red text. Ensure they're extracted as negative.
- Progress billings show cumulative amounts — the "amount due" is the incremental, not the total.
- Lumber yard amounts may appear misaligned in OCR — cross-check against supporting invoices.
- PO numbers (e.g., SILV-2900-2) often encode the cost code.
