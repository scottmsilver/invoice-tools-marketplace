# Invoice Tools — Claude Code Plugin

A Claude Code plugin for homeowners managing large residential construction projects. Audits monthly draw requests from your general contractor, extracts invoice data from PDFs, cross-references against your budget, and drafts emails to your GC.

## What It Does

Every month your GC sends a draw request — a PDF with a cover page, a detail summary of every charge, and a stack of supporting invoices (typed invoices, handwritten receipts, progress billings, rental invoices, etc.). This plugin reads all of it, verifies the math, checks every invoice against the summary, cross-references your budget spreadsheet, and tells you what to pay, what to question, and what to dispute.

## Skills

### `/invoice:draw-analyzer`

The main skill. Give it the draw PDF and your actuals spreadsheet, and it produces:

- A structured audit spreadsheet (.xlsx) with tabs for line items, budget cross-reference, math verification, and flagged issues
- A narrative summary with specific questions for your GC
- Flags for budget overruns, missing COs, invoice typos, cost code mismatches, out-of-period charges, and missing documentation

```
> /invoice:draw-analyzer

Then point it at your files:
  data/feb/Draw Request.pdf
  data/feb/Actuals.xlsx
```

### `/invoice:extractor`

Extracts structured data from a single PDF invoice — typed invoices, photographed receipts, handwritten invoices, progress billings, rental invoices, pay applications. Handles wildly different formats.

```
> /invoice:extractor

Upload or point it at any construction invoice PDF.
```

### `/invoice:matcher`

Matches line items from the GC's detail summary to their supporting invoices. Flags mismatches in amounts, vendor names, and invoice numbers. Uses greedy and bipartite matching algorithms.

```
> /invoice:matcher

Give it the parent summary and a set of supporting invoices.
```

### `/invoice:boundary-detector`

Splits a multi-document PDF into individual documents. Construction draw PDFs typically concatenate a cover page, detail summary, and 20-40 supporting invoices into one file. This skill finds where each document starts and ends.

```
> /invoice:boundary-detector

Point it at a concatenated draw request PDF.
```

### `/invoice:cleanup`

Normalizes extracted invoice data — fixes vendor name variations, handles credits in parentheses, distinguishes subtotals from line items, cleans up progress billing math, and standardizes date formats.

```
> /invoice:cleanup

Run it on raw extraction output that needs normalization.
```

### `/invoice:email-drafter`

Turns audit findings into a plain-text email to your GC. Calibrates tone to severity — self-deprecating about penny discrepancies, direct about missing change orders, professional about disputes.

```
> /invoice:email-drafter

Run it after the draw-analyzer to draft your response.
```

## Installation

1. Clone this repo into your Claude Code plugins directory:
   ```
   git clone https://github.com/scottmsilver/invoice-tools-marketplace.git \
     ~/.claude/plugins/marketplaces/invoice-tools
   ```

2. Install Python dependencies:
   ```
   pip install -r ~/.claude/plugins/marketplaces/invoice-tools/plugins/invoice/lib/requirements.txt
   ```

3. Restart Claude Code. The skills will appear as `invoice:draw-analyzer`, `invoice:extractor`, etc.

## Project Setup

For best results, keep a `project-context.md` file alongside your draw PDFs with project-specific details:

- GC name and contacts
- Builder's Comp rate
- Cost code mappings
- Prior dispute history
- Special billing rules

The draw-analyzer will pick this up automatically. If it's not there, it extracts what it can from the draw itself.

## What Gets Flagged

- Math errors (line item sums, BC rate, retention, totals)
- Invoice number mismatches between summary and supporting docs
- Cost code mismatches (summary vs PO numbers)
- Budget overruns by cost code (against both original and revised budgets)
- Missing change orders for over-budget items
- Out-of-period invoices
- Missing vendor invoices (e.g., only a credit card receipt)
- 50% deposits and future payment exposure
- Internal GC labor charges without external backup
- Invoices billed to jobsite personnel rather than the homeowner

## Python Library

The `lib/` directory contains tested implementations:

- `PDFExtractor` — PDF text extraction and page splitting (PyMuPDF)
- `LLMService` — multi-provider LLM calls (Gemini/Claude/OpenAI) with rate limiting, caching, JSON repair
- `ValidationService` — line item matching with greedy and bipartite algorithms
- `ImageConverter` — convert photographed receipts to PDF

## License

MIT
