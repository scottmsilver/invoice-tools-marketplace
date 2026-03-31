---
name: extractor
description: "Extract structured data (line items, amounts, dates, vendor info) from an invoice, receipt, or pay application PDF. Handles typed, handwritten, and photographed documents."
---

# PDF Invoice Extractor

## Purpose

Extract structured invoice data from construction PDFs that come in wildly different formats: formal typed invoices, photographed receipts, handwritten notes, progress billings, rental invoices, and T&M breakdowns.

## Deep Extraction Requirement

Do NOT rely on superficial text extraction or a single pass. Every document must be individually extracted and verified:

1. **Extract every page** — process each supporting invoice individually, not just the summary page
2. **Cross-verify amounts** — compare what the summary claims vs what each supporting invoice actually says
3. **Flag discrepancies** — even small differences ($0.01) between summary and supporting docs matter
4. **Read the actual numbers** — don't trust OCR at face value. When amounts look wrong, re-read the source page

This is a financial audit tool. Missing a single line item or accepting an incorrect amount defeats the purpose.

## How It Works

For each document (identified by `invoice:boundary-detector` or provided individually):

1. Read the PDF pages using PyMuPDF
2. Extract text and, if needed, use vision to read photographed/handwritten content
3. Parse into structured data: vendor, line items, amounts, dates, invoice numbers
4. Handle credit/void detection, line-item-vs-aggregation, and timesheet rules
5. Verify the extraction is internally consistent (line items sum to total, etc.)

## Document Type Tips

| Type | Watch For |
|------|-----------|
| Standard invoice | Verify total = sum of line items + tax |
| Photographed receipt | May need OCR. Tax often on separate line |
| Progress billing | "amount_due" is the incremental amount for this period, NOT the cumulative total |
| Rental invoice | Separate rental from sales items and delivery charges |
| Pay application | Match to AIA format if applicable |
| Credit memo | All amounts should be negative |
| Timesheet/labor | Extract category totals only, not individual time entries |

## Common Edge Cases

- Lumber yard invoices: amounts may appear misaligned in columns
- Hardware store receipts: photographed on job site, often wrinkled or at angles
- Paint store receipts: "SOLD" stamp may obscure fields
- Fireplace/hearth invoices: old-style dot matrix printer format
- Progress billings: cumulative contract with percentage completion
- Online invoicing platforms: clean format but sometimes billed to jobsite personnel instead of homeowner
