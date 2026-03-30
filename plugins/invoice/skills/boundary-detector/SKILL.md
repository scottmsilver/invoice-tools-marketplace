---
name: boundary-detector
description: "Use when a multi-page PDF contains multiple separate documents concatenated together. Trigger when processing a construction draw request PDF that has a cover page, detail summary, and multiple supporting invoices all in one file, or any PDF where separate documents need to be identified and split."
---

# Document Boundary Detector

## Purpose

Detect where separate invoices start and end within a multi-page construction draw request PDF. A typical draw PDF contains 20-50 pages with 10-30 separate documents concatenated together.

## Implementation

Use `lib.LLMService.detect_document_boundaries()`. The lib handles the LLM prompt, vision model integration, page number remapping, gap filling, and JSON parsing with repair. It returns a list of `DocumentBoundary` objects.

```python
from lib.llm_service import LLMService
from lib.pdf_extractor import PDFExtractor

llm = LLMService(provider="gemini", api_key=os.environ["GEMINI_API_KEY"])

with PDFExtractor(pdf_path) as pdf:
    pages_text = pdf.extract_all_text()
    pdf_bytes = open(pdf_path, "rb").read()

    # Exclude cover page (1) and detail summary (2) from supporting doc detection
    boundaries = llm.detect_document_boundaries(
        pages_text, pdf_bytes=pdf_bytes, exclude_pages=[1, 2]
    )

    for b in boundaries:
        print(f"Pages {b.pages}: {b.type.value} (confidence {b.confidence})")
```

## Typical Draw PDF Structure

```
Page 1:     Cover page (GC letterhead, total due)
Page 2:     Detail summary (line items table, financial summary)
Pages 3-4:  Supporting invoice #1 (may be multi-page)
Page 5:     Supporting invoice #2
Pages 6-7:  Supporting invoice #3
...
Page N:     Labor detail sheet (internal GC document)
```

## Detection Signal Strength

**Strong** (high confidence new document):
- Different company letterhead/logo
- New "INVOICE" or "Invoice #" header
- New "Bill To" / "Ship To" block
- "Page 1 of N" indicator resets to 1
- Completely different visual layout

**Medium:**
- Different paper size/orientation
- Abrupt font/style change
- PO number change

**Weak** (use with other evidence):
- Blank space or page break
- Change in background color

## Multi-Page Document Grouping

Some supporting invoices span multiple pages. The lib handles this via its grouping prompt, but be aware of these common patterns:
- Pay applications: invoice page + payment request page
- Fireplace/hearth vendors: multiple pages of line items
- Specialty contractors: invoice page + terms page
- Labor detail sheets: 2-3 pages of daily entries
- Credit card receipt followed by matching invoice (same total = same document)
