---
name: price-check
description: "Verify cost reasonableness of invoice line items: labor rates vs market, material prices vs retail, equipment rental rates vs published sheets, and specialty contract pricing vs industry benchmarks."
---

# Invoice Price Check

## Purpose

Answer the question: "Are we paying a fair price?" For each charge on the invoice, assess whether the unit cost, labor rate, rental rate, or contract price is reasonable for the market, location, and scope.

This is not about catching fraud. It's about identifying charges that are significantly above market so the homeowner can ask informed questions. A $5 difference on lumber doesn't matter. A labor rate that's 2x market does.

## Input

The path to `extracted.json` produced by `invoice:ingest`. The supporting documents' `line_items` arrays contain the detail needed — SKUs, unit prices, quantities, model numbers, hourly rates, rental periods.

Also read `project-context.md` if it exists, for project location (affects market rates) and any known vendor terms.

## Output

Write `price_check_results.json` to the working directory:

```json
{
  "location": "Park City, UT (mountain resort area — expect 15-30% premium over Wasatch Front)",
  "check_timestamp": "2026-03-31T10:00:00Z",
  "checks": [
    {
      "supporting_document_id": "supp_001",
      "vendor": "Apex Drywall",
      "category": "labor_rate",
      "item": "Drywall framing labor",
      "invoiced_rate": 40.00,
      "invoiced_unit": "per hour",
      "market_range_low": 35.00,
      "market_range_high": 55.00,
      "assessment": "within_range",
      "confidence": "medium",
      "notes": "Rate of $40/hr for drywall framing in Park City is at the low end of market. Typical range $35-55/hr for residential in Wasatch Back.",
      "source": "LLM construction knowledge"
    }
  ],
  "summary": {
    "total_checked": 15,
    "within_range": 12,
    "below_market": 1,
    "above_market": 2,
    "unable_to_verify": 0,
    "potential_savings": 0.00
  }
}
```

## What to Check

Work through the supporting documents and check every item where a meaningful price comparison is possible. Skip tax lines, delivery charges under $200, and items where you can't determine a unit rate.

### 1. Labor Rates

For any invoice that shows hours and a rate (or where rate can be calculated from total / hours):

| What to Extract | How to Assess |
|---|---|
| Hourly rate | Compare to market for that trade, in that location |
| Total hours | Flag if hours seem high for the described scope (e.g., 166 hours of framing for "window shade pockets") |
| Crew size | If visible (e.g., "8h x2" = 2-person crew), note effective hourly cost |

**Market rate research:** Use WebSearch to find current labor rates for the specific trade in the project's metro area. Search for things like "[trade] labor rate [city] [year]" or check construction cost databases. If web search isn't available, use your training knowledge but note "LLM estimate" as the source and lower confidence.

Common residential construction labor rates (as baseline — adjust for location):

| Trade | National Avg | Mountain West Premium |
|---|---|---|
| Framing | $25-45/hr | +15-30% |
| Drywall | $30-50/hr | +15-30% |
| Painting | $35-55/hr | +15-30% |
| Finish carpentry | $40-65/hr | +15-30% |
| General labor / cleanup | $20-35/hr | +15-30% |
| Concrete cutting | $50-80/hr | +10-20% |

### 2. Material Prices (retail items)

For Home Depot, BFS, Sunpro, lumber yards, and hardware store receipts that show SKUs or product descriptions:

| What to Extract | How to Assess |
|---|---|
| Product name / SKU | Search for current retail price |
| Unit price on receipt | Compare to current retail |
| Quantity | Flag bulk purchases that could qualify for volume discount |

**Research approach:**
- For Home Depot items: use WebSearch with the SKU number or product description
- For BFS/lumber yard: search the product description + "price" — BFS is contractor-grade, so retail comparison may show a contractor discount
- For specialty items (Simpson connectors, Freud blades): search manufacturer + model

**What matters:** A $2 difference on a box of screws doesn't matter. A 50% markup on a $135 LVL beam does. Focus on items over $50 where the unit price seems high.

### 3. Equipment Rental Rates

For Sunbelt, United, Herc, Savage, Wheeler, and other rental companies:

| What to Extract | How to Assess |
|---|---|
| Equipment type / model | Search for published rental rates |
| Daily / weekly / monthly rate | Compare to competitor published rates |
| Delivery / pickup charges | Flag if over $200 each way |
| Rental period | Flag if rental period seems long for the described work |

**Research approach:** Search for "[equipment type] rental rate [city]" or check Sunbelt/United published rate cards. Scaffold rates are especially worth checking — they're billed daily and compound fast.

### 4. Specialty Equipment & Fixtures

For items with manufacturer model numbers (fireplace units, HVAC, glass, roofing systems):

| What to Extract | How to Assess |
|---|---|
| Manufacturer + model | Search for MSRP / dealer pricing |
| Install labor | Is it included in the price or separate? What's the implied labor rate? |
| Warranty terms | Note if warranty is included in the price |

**Examples from typical construction invoices:**
- Fireplace units (Mason Lite, Napoleon, Lennox): check MSRP
- Hidden valve systems (Johnstone HVS-SC): check manufacturer pricing
- Glass railing systems: check per-linear-foot installed pricing
- Roofing (per square, per linear foot of copper): check regional installed rates

### 5. Contract-Level Reasonableness

For large subcontracts shown as progress billings, assess the overall contract value against the scope:

| What to Extract | How to Assess |
|---|---|
| Total contract amount | Is it reasonable for the described scope? |
| Scope description | Does the price per unit (per sqft, per linear foot) make sense? |
| Change orders | Are CO amounts proportional to the added scope? |

**Examples:**
- Structural steel: $/lb or $/ton for fabricated + installed
- Roofing: $/square (100 sqft) for material + labor
- Heat tape: $/linear foot installed
- Glass railings: $/linear foot installed
- Elevator glass: compare to residential elevator quotes

## Assessment Scale

| Assessment | Meaning |
|---|---|
| `below_market` | Price is notably below typical market rate. Could indicate contractor discount being passed through (good) or scope that's less than described. |
| `within_range` | Price is within the normal range for this item/trade/location. No action needed. |
| `above_market` | Price is notably above typical market rate. Worth asking about. Could be justified by access difficulty, rush, specialty, or mountain premium. |
| `significantly_above` | Price is 50%+ above market. Flag prominently. |
| `unable_to_verify` | Can't determine a market rate for this item. Note why. |

## Confidence Levels

| Level | Meaning |
|---|---|
| `high` | Based on current web search results or published rate sheets |
| `medium` | Based on LLM construction knowledge, adjusted for location |
| `low` | Rough estimate — unusual item, limited comparables |

## What NOT to Do

- Don't flag every item. Focus on the ones where pricing matters (high dollar, unusual rate, or items the homeowner is watching).
- Don't compare contractor pricing to DIY pricing. A GC buying through BFS at contractor rates should be cheaper than Home Depot retail — that's expected, not a red flag.
- Don't second-guess labor rates within 20% of market. Construction labor markets are tight, especially in mountain resort areas. A rate that's 10% above average isn't worth flagging.
- Don't treat delivery charges as overpriced. Getting materials to a mountain jobsite costs more than a valley delivery.
- Don't compare progress billing contract totals to the individual line items on the draw. The draw shows the incremental amount, not the contract — those are different comparisons.

## Tips

- Park City / Wasatch Back commands a 15-30% premium over Salt Lake Valley for labor and materials due to altitude, access, and cost of living.
- Winter work (October-March) may carry additional premiums for heating, snow management, and shortened workdays.
- Contractor discounts on materials (BFS, Sunpro) typically run 10-25% below retail. If the receipt shows retail pricing, the GC may be pocketing the discount.
- Some GCs buy materials on their account and pass through at cost. Others mark up. Check whether the receipt is from the GC's account (indicates pass-through) or a retail purchase.
