---
name: boundary-detector
description: "Split a multi-page PDF into its individual documents. Use when a PDF contains multiple invoices, receipts, or pay applications concatenated together."
---

# Document Boundary Detector

## Purpose

Detect where separate invoices start and end within a multi-page PDF. A typical contractor invoice PDF contains 20-50 pages with 10-30 separate documents concatenated together.

## How It Works

Read every page of the PDF and identify where each distinct document begins and ends. Return a list of document boundaries with page ranges, document types, and confidence levels.

## Typical PDF Structure

The structure varies, but a common pattern:

```
Page 1:     Cover page (GC letterhead, total due)
Page 2:     Detail summary (line items table, financial summary)
Pages 3-4:  Supporting invoice #1 (may be multi-page)
Page 5:     Supporting invoice #2
Pages 6-7:  Supporting invoice #3
...
Page N:     Labor detail sheet (internal GC document)
```

Not every PDF will follow this layout. Work with whatever structure is present.

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

Some supporting invoices span multiple pages. Be aware of these common patterns:
- Pay applications: invoice page + payment request page
- Fireplace/hearth vendors: multiple pages of line items
- Specialty contractors: invoice page + terms page
- Labor detail sheets: 2-3 pages of daily entries
- Credit card receipt followed by matching invoice (same total = same document)
