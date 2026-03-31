---
name: analyzer
description: "Audit a contractor invoice: extract line items, verify math, cross-reference supporting receipts, flag anomalies, and produce a structured report. Use when reviewing any contractor billing — invoices, pay applications, or receipts."
---

# Invoice Analyzer

## Purpose

Homeowners managing construction projects receive invoices from their general contractor. These vary widely in format — some are multi-page PDFs with a cover page, line item summary, financial summary, and supporting receipts all bundled together; others are a single-page invoice or a loose collection of receipts. There is no standard layout.

Common elements you may encounter (not all will be present):
- A **summary or cover page** with the total amount due
- A **line item breakdown** listing individual charges, credits, and fees — columns and labels vary by GC
- A **financial summary** with markup (often called Builder's Comp), retention, credits, and totals
- **Supporting invoices** backing individual line items — these come in every format imaginable: formal typed invoices, handwritten receipts, photographed receipts, pay applications, progress billings, rental invoices, T&M breakdowns, etc.

Your job is to figure out the structure of whatever you're given, extract every charge and credit, verify all math, and audit the result so the homeowner can make an informed decision about whether to pay, dispute specific items, or request corrections.

**Deep scrub is non-negotiable.** Do not skim the invoice or sample a few receipts. Every single line item must be extracted, every supporting document must be read, and every amount must be cross-checked. A half-done audit is worse than no audit — it creates false confidence. If an invoice has 30 supporting receipts, read all 30. If the math is off by $0.22, flag it.

## Expected Inputs

You may be given a single file, multiple files, a directory, or a zip file. Whatever the input, look at **every file** provided and figure out what each one is.

Common file types you'll encounter:
- `.pdf` — invoices, receipts, pay applications
- `.xlsx` / `.xls` — actuals spreadsheets, budgets, cost trackers
- `.md` — project context files
- `.jpg` / `.png` / `.gif` — photographed receipts

If given a zip, extract it first. If given a directory, scan it recursively.

### Actuals spreadsheet

An actuals spreadsheet is optional — many users won't have one. If one is present, use it for budget cross-referencing (Steps 7-9). If not, skip those steps and note in the report that budget cross-referencing was skipped. Don't ask for it.

The spreadsheet format is project-specific. Common sheet names:
- **Cost Codes**: Master budget tracker with columns for Cost Code ID, Description, OG Budget, Revised Budget, monthly invoice columns, Invoiced Work To Date, Remaining Cost to Complete, Estimated Total, Variance columns
- **Scope**: Remaining work items by cost code with notes on what's left
- **CO Log**: Change order log
- **Notes/Costs**: Supplementary cost breakdowns for large items

### Project context

Look for a **project-context file** (e.g., `project-context.md`) among the provided files or in the working directory. This file contains project-specific details like GC name, contacts, cost code mappings, Builder's Comp rate, and special billing rules. If found, load it before processing.

## Workflow

### Step 0: Extract Zip (if applicable)

If the user provided a zip file:

```python
import zipfile, tempfile, os

tmpdir = tempfile.mkdtemp(prefix="invoice_audit_")
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

Identify the main invoice PDF, any actuals spreadsheet, and any context file. Then proceed to Step 1.

### Step 1: Split Multi-Document PDFs

If a PDF contains multiple invoices or receipts concatenated together, use `invoice:boundary-detector` to identify where each document starts and ends. This is common — GCs often bundle a summary page plus dozens of supporting invoices into a single PDF.

If the PDF is a single document, skip this step.

### Step 2: Extract Structured Data

Use `invoice:extractor` on each document (the main invoice and each supporting document identified in Step 1). The extractor handles all the format variation — typed invoices, photographed receipts, handwritten notes, progress billings, pay applications.

From the main invoice's summary or line item breakdown, you should end up with every line item as structured data:

| Field | Description |
|-------|-------------|
| supplier | Vendor/subcontractor name |
| description | Material or service description |
| amount | Dollar amount (negative for credits) |
| invoice_number | Vendor invoice number (may be blank for internal GC charges) |
| date | Invoice date |
| cost_code | Cost code / activity number |
| is_credit | Boolean — true if this is a credit line (shown in parentheses or red) |

Also extract any financial summary present. Common elements (not all will appear):
- **Subtotal** (sum of all line items including credits)
- **Markup / Builder's Comp** (the GC's markup percentage applied to costs)
- **Markup credits** (credits against markup for items that shouldn't carry it)
- **Retention held** (withheld amount, if any)
- **Invoice total** (subtotal + markup + credits - retention)
- **Balance due from prior invoices** (carry-forward unpaid amount)
- **Total due** (invoice total + prior balance)

### Step 3: Ask Clarifying Questions

After extraction, review what you've found and ask the user about anything that would help you audit more accurately. Check the project-context file first — the answer may already be there from a previous invoice.

Examples of useful questions (ask only what's relevant, not a checklist):
- **Vendor identity**: "I see charges from XYZ Corp — what do they do on this project?" (helps you judge whether the amount and cost code make sense)
- **P&O (profit & overhead)**: "Does this vendor include P&O in their invoices, or is P&O added by the GC on top?" (affects how you verify markup math)
- **Expected rates**: "The GC is marking up at 18% — is that the contracted rate?" (can't verify without knowing)
- **Unfamiliar charges**: "There's a $4,500 line item for 'site conditions' with no supporting invoice — do you know what this is?"
- **Credits you don't understand**: "There's a $2,100 credit from ABC Plumbing — do you know what it's offsetting?"
- **Progress billing context**: "This is invoice #3 from the electrician showing 60% complete on a $45K contract — does that track with where the work actually is?"

**Save what you learn.** When the user answers a question that would be useful for future invoices (vendor identity, P&O status, contracted rates, cost code mappings), append it to the `project-context.md` file in the working directory under a `## Known Vendors` or `## Project Details` section as appropriate. Don't re-ask what's already recorded.

Questions about a specific line item or charge (e.g., "what's this $4,500 for?") are useful for this audit but probably don't need to be persisted — use your judgment.

### Step 4: Verify Math

Perform these verification checks:

1. **Line item sum**: Do all extracted amounts sum to the stated Subtotal?
2. **Markup rate**: If markup is present, calculate it as a percentage of the non-credit subtotal. Is it the expected rate? Flag if it deviates from the project's contracted rate.
3. **Markup credit logic**: Do markup credits correspond to specific line items that shouldn't carry markup? Verify the math.
4. **Invoice total arithmetic**: Does Subtotal + Markup + Credits - Retention = Invoice Total?
5. **Total due**: Does Invoice Total + Prior Balance = Total Due?
6. **Cross-check**: If there's a cover page or separate totals page, does it match the line item detail?

### Step 5: Flag Anomalies

Check for and flag:

- **Missing invoice numbers**: Line items billed to the homeowner without a traceable vendor invoice number (common for internal GC charges — note these separately as they deserve extra scrutiny)
- **Out-of-period dates**: Supporting invoices dated significantly before or after the billing period
- **Unmatched credits**: Credits that don't clearly correspond to a charge (or credits where the offset isn't equal)
- **Large round numbers without detail**: Lump-sum charges without itemized backup
- **Duplicate-looking entries**: Multiple charges from the same vendor for similar descriptions
- **50% deposits on large items**: Flag these for awareness — they represent future obligations
- **Internal labor charges**: Labor billed by the GC without external invoice backup — these rely on the GC's own timesheets
- **Invoices billed to jobsite personnel rather than the homeowner**: These sometimes lack proper PO routing

### Step 6: Match Line Items to Supporting Documents

Use `invoice:matcher` to cross-reference each line item on the summary against the supporting documents extracted in Steps 1-2. The matcher handles vendor name variations, amount tolerances, and invoice number mismatches.

For items that don't match, or where the matcher flags discrepancies (amount differences, vendor name mismatches, missing backup), include those in the anomaly list.

Note: Some items (internal GC labor, small retail receipts) may have minimal supporting documentation — flag these but don't treat as errors.

### Step 7: Cross-Reference Against Actuals (requires actuals spreadsheet)

Read the Cost Codes sheet from the actuals spreadsheet. For each cost code that appears in this invoice:

1. **Match invoice amounts to actuals**: Aggregate the invoice's line items by cost code. Compare each code's total against the corresponding column in the actuals. Flag any mismatches — these could indicate the GC's summary doesn't match their own books.

2. **Budget status check**: For each cost code, calculate:
   - Prior invoiced amount (total invoiced minus this invoice)
   - Total invoiced including this invoice
   - Whether the code is over its **revised budget** (this is the most actionable flag)
   - Whether the code is significantly over the **original estimate** (>$10K over, useful for tracking scope creep)

3. **Remaining budget**: Show how much is left in each code's budget after this invoice. Codes with $0 remaining that are still showing charges deserve extra scrutiny.

### Step 8: Check Scope & Change Orders (if actuals spreadsheet has Scope/CO sheets)

1. Read the **Scope** sheet. For cost codes in this invoice, pull the scope notes to show what work was originally anticipated.
2. Read the **CO Log**. Check if any charges in this invoice correspond to change orders. Flag charges that look like changed scope but have no corresponding CO entry.
3. Check the **PO/vendor allocation columns** in the Cost Codes sheet. Verify that vendors billing against a cost code are the expected vendors for that PO.

### Step 9: Budget Trend Analysis (if actuals spreadsheet provided)

Identify the most concerning budget trends across the full project:
- Cost codes where invoiced amount exceeds revised budget (these are actively over-budget)
- Cost codes where the original estimate has been revised upward by more than 50% (indicates scope creep or poor initial estimation)
- Large remaining budget items that haven't started yet (upcoming financial exposure)

### Step 10: Generate the Report

Produce TWO outputs:

**1. A structured spreadsheet (.xlsx)** with these tabs:
- **Summary**: High-level overview — total invoice amount, math check results, cost codes over budget, and a list of specific questions for the GC. This should be the first tab the homeowner sees.
- **Line Items**: All extracted line items with columns: Supplier, Description, Amount, Invoice #, Date, Cost Code, Is Credit, Flags
- **Budget Cross-Reference** (if actuals provided): One row per cost code in this invoice showing: This Invoice amount, Prior Invoiced, Total Invoiced, OG Budget, Revised Budget, Estimated Total, Over Revised? (YES/NO with conditional formatting), $ Over Revised, $ Over OG, Scope Notes
- **Math Verification**: Each check performed, expected value, actual value, pass/fail. Include both arithmetic checks and cross-reference checks.
- **Flags & Issues**: Every anomaly found (both invoice-level and budget-level), severity (Info/Warning/Error), description, affected item, details. Sort by severity (Errors first).

**2. A concise narrative summary** in the chat that covers:
- Total invoice amount and what it covers at a high level
- Any math errors found
- The most important flags/issues the homeowner should review
- **Budget cross-reference findings** (if actuals provided): Which cost codes are over budget, by how much, and whether the overruns happened this month or were pre-existing
- Specific questions the homeowner should ask the GC (numbered, actionable)
- Recommendation: pay as-is, pay with noted exceptions, or dispute

## Important Context

### General (applies to any project)

- Builder's Comp (BC) is the GC's markup, typically a consistent percentage applied to costs. Credits against BC are used when specific items shouldn't carry markup (e.g., items with separate contractual terms).
- Cost codes map vendor charges to budget categories. The actuals spreadsheet defines the mapping for each project.
- When a project context file exists (e.g., `project-context.md` or similar), read it for project-specific details like GC name, contacts, cost code mappings, prior disputes, and special billing rules.
- If no project context file is found, extract what you can from the invoice itself (GC name from letterhead, cost codes from the summary, markup rate from the financials) and ask the user for anything else you need.

### Project-Specific Context

Project-specific details (GC name, contacts, address, cost code mappings, Builder's Comp rate, actuals spreadsheet filename, prior dispute history, special billing rules) should be stored in a `project-context.md` file in the working directory. This keeps sensitive project data local and out of the skill itself.

If no project context file is found, the skill will extract what it can from the invoice and prompt the user for the rest.

## Tips for Accuracy

- Credit lines in the detail summary are often shown in parentheses and/or bold/italic/red text. The OCR text will show them as negative numbers or with parentheses like `(2,149.00)`.
- Lumber yard invoice amounts in the summary may appear misaligned in the OCR — cross-check against the actual invoices in the supporting pages.
- Some invoices have PO numbers (e.g., `PROJ-2900-2`, `PROJ-4750-1`) which map to cost codes. These can help verify correct cost code assignment.
- Progress billings show cumulative contract amounts with percentage completion — the "amount due" is the incremental amount for this period, not the total contract value.
- Use `invoice:cleanup` when extracted data needs normalization (vendor names, dates, progress billing math).
- Use `invoice:email-drafter` to turn audit findings into a plain-text email for the GC.
