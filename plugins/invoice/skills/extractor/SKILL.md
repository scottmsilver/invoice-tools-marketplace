---
name: extractor
description: "Use when extracting structured data from construction invoices, receipts, or pay applications in PDF format. Trigger when the user uploads a PDF invoice, photographed receipt, handwritten invoice, progress billing, rental invoice, or any vendor document that needs line items, amounts, dates, and vendor info extracted into structured data."
---

# PDF Invoice Extractor

## Purpose

Extract structured invoice data from construction PDFs that come in wildly different formats: formal typed invoices, photographed receipts, handwritten notes, progress billings, rental invoices, and T&M breakdowns.

## Deep Extraction Requirement

Do NOT rely on superficial text extraction or a single pass. Every invoice in the draw must be individually extracted and verified:

1. **Extract every page** — process each supporting invoice individually, not just the summary page
2. **Cross-verify amounts** — compare what the summary claims vs what each supporting invoice actually says
3. **Flag discrepancies** — even small differences ($0.01) between summary and supporting docs matter
4. **Read the actual numbers** — don't trust OCR at face value. When amounts look wrong, re-read the source page

This is a financial audit tool. Missing a single line item or accepting an incorrect amount defeats the purpose.

## Implementation

Use `lib.LLMService` and `lib.PDFExtractor`. The lib handles provider selection (Gemini preferred, Claude fallback), rate limiting, in-memory caching, and JSON repair. The extraction prompt, credit/void handling, line-item-vs-aggregation logic, and timesheet rules are all in `lib.LLMService.extract_invoice_data()`.

```python
import sys, os
sys.path.insert(0, os.environ.get("CLAUDE_PLUGIN_ROOT", "."))

from lib.llm_service import LLMService
from lib.pdf_extractor import PDFExtractor

llm = LLMService(provider="gemini", api_key=os.environ["GEMINI_API_KEY"])

with PDFExtractor(pdf_path) as pdf:
    pages_text = pdf.extract_all_text()
    pdf_bytes = open(pdf_path, "rb").read()

    # Detect document boundaries (where each invoice starts/ends)
    boundaries = llm.detect_document_boundaries(pages_text, pdf_bytes=pdf_bytes)

    # Extract each invoice individually
    for boundary in boundaries:
        invoice_pdf = pdf.extract_pages_as_pdf(boundary.pages)
        invoice = llm.extract_invoice_data(boundary.type, invoice_pdf)
```

Default Gemini model: `gemini-3.1-flash-lite-preview`. Override via `model_name` parameter.

## Document Type Tips

| Type | Watch For |
|------|-----------|
| Standard invoice | Verify total = sum of line items + tax |
| Photographed receipt | May need OCR. Tax often on separate line |
| Progress billing | "amount_due" is the incremental draw, NOT the cumulative total |
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
