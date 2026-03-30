---
name: cleanup
description: "Use when extracted invoice data has formatting issues, ambiguous amounts, or needs normalization. Trigger when processing raw OCR output, handling credits in parentheses, distinguishing subtotals from line items, cleaning up progress billing data, or normalizing vendor names and amounts from messy construction documents."
---

# LLM Data Cleanup

## Purpose

Clean and normalize messy invoice data extracted from construction PDFs. Most of the heavy lifting (credit handling, void detection, line-item-vs-aggregation, timesheet rules) is already in `lib.LLMService.extract_invoice_data()`. This skill covers additional normalization that happens after extraction.

## When to Use

Use this skill when extracted data still needs cleanup that the extraction prompt didn't handle:
- Progress billing math needs verification
- Vendor names need normalization across the draw
- Amounts, dates, or invoice numbers need format standardization

## Progress Billing Verification

Progress billings show cumulative contract data. The correct draw amount is the **incremental** amount, not the cumulative total.

```
Contract Amount: $50,000.00
% Complete: 100%
Amount Due: $50,000.00        ← NOT this
Less Prior Requests: $42,000.00
Due This Request: $8,000.00   ← THIS is what goes in the draw
```

Always verify: `this_draw = total_due - prior_payments`. Flag if it doesn't check out.

## Vendor Name Normalization

GCs often prefix vendor names with their own abbreviation when purchasing on behalf of the project. Normalize by stripping the GC prefix:

| Raw | Normalized |
|-----|-----------|
| GC - Home Depot | Home Depot |
| GC - Benjamin Moore | Benjamin Moore |
| GC - Ace Hardware | Ace Hardware |
| [GC Full Name] | [GC Name] (internal) |
| [GC DBA / Legal Entity Name] | [GC Name] |

The specific GC name and its variants should be defined in your `project-context.md` file.

## Amount Normalization

- Strip currency symbols: $1,234.56 -> 1234.56
- Handle comma vs period decimals: 1.234,56 (European) -> 1234.56
- Round to 2 decimal places
- Flag amounts that look like typos (e.g., $38.55 on summary vs $38.33 on receipt)

## Date Normalization

Normalize all dates to YYYY-MM-DD. Infer year from billing period context when not explicit.

## Invoice Number Cleanup

- Strip leading zeros unless they appear intentional
- Preserve alphanumeric formats (e.g., ABC1234-00)
- Flag potential OCR errors: O vs 0, l vs 1, S vs 5
