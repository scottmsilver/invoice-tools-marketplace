---
name: matcher
description: "Match invoice line items to their supporting receipts/documents. Cross-reference amounts, vendor names, and invoice numbers to verify each charge has backup."
---

# Invoice Line Matcher

## Purpose

Match each line item on a GC's summary to its corresponding supporting document. Handle cases where descriptions don't match exactly, amounts differ slightly, or vendor names vary.

## How It Works

Two matching strategies, applied in order:

1. **Greedy** (fast): exact match on invoice totals first, then fuzzy (±2% tolerance), then line-item matching
2. **Bipartite** (optimal): builds a cost matrix and solves via Hungarian algorithm for globally optimal assignment when greedy produces poor results

Both include semantic validation — flag matches where amounts match but descriptions don't make sense (e.g., an electrical invoice matched to a plumbing line item just because the totals happen to be close).

## Verification Types Run

The `validate_invoice` method runs these checks in parallel:
1. **work_requested** — PO/contract/authorization found?
2. **totals_match** — line items sum to invoice total?
3. **correct_recipient** — billed to the right entity?
4. **has_supporting_details** — supporting docs cover the parent total?
5. **reasonable_cost** — any wildly expensive or absurd charges?
6. **work_completed** — completion date, signature, or approval stamp?
7. **receipt_exists** — each parent line item matched to a supporting invoice?
8. **unmatched_supporting_invoice** — any supporting docs not referenced by parent?

## Construction-Specific Patterns

- GC prefix on vendor names (e.g., "GC - Home Depot") means purchased by GC staff — match to "Home Depot"
- Internal GC charges (labor, credits) have no supporting invoice by design — don't treat as errors
- Credits always have a matching charge somewhere (same vendor, opposite sign)
- Progress billings show cumulative amounts — the "amount due" column is the incremental amount for this period

## Construction Synonym Pairs

These help with semantic matching:
- "rough materials" = "lumber", "framing materials", "building supplies"
- "electrical supplies" = "wire", "conduit", "breakers", "panels"
- "equipment rental" = "tool rental", "lift rental", "scaffold"
- "jobsite materials" = "misc materials", "supplies"
- "finish carpentry" = "trim", "millwork", "cabinetry labor"
- "painting materials" = "paint", "primer", "surface protection"
