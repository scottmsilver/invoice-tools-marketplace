---
description: Extract structured data from construction invoices, receipts, or pay applications
argument-hint: [pdf-path]
---

Use the `invoice:ingest` skill to read the provided PDF, detect document boundaries, extract structured data from every document, and write `extracted.json`. If a path argument is provided, use that as the PDF. Otherwise, ask the user which PDF to process.
