---
name: email-drafter
description: "Produce two outputs from invoice audit findings: (1) a homeowner insight summary highlighting what actually matters, and (2) a plain-text email to the GC. Calibrates tone to severity — jokes about pennies, direct about missing COs."
---

# Invoice Communications

## Purpose

Turn structured audit findings into two outputs:

1. **Homeowner Insight Summary** — what you'd tell a friend who just got a $265K draw request and asked "should I be worried about anything?" This is the analysis. Not a restatement of the spreadsheet, but the interpretation: which trends are worrying, which charges deserve scrutiny, what patterns suggest scope creep without approval, and what to push back on.

2. **GC Email** — a plain-text email to the contractor that raises the right questions without being adversarial.

Both outputs use the same tone calibration and writing rules.

## Input

The skill expects:
1. **Audit findings** — from the analyzer's XLSX report, narrative summary, `extracted.json`, `matching_results.json`, `price_check_results.json`, or any combination. If `price_check_results.json` exists, incorporate its findings: items flagged `above_market` or `significantly_above` deserve prominent mention in the insight summary; items `below_market` are positive signals worth noting briefly ("contractor discounts being passed through")
2. **Context** about the relationship (GC name, project, any prior disputes) — check `project-context.md`
3. **Purpose**: initial inquiry, response to GC explanation, follow-up, dispute

If no audit has been run yet, tell the user to run `invoice:analyzer` first.

## Tone Calibration

| Severity | Tone | Example phrasing |
|----------|------|-------------------|
| Trivial discrepancies (pennies, rounding) | Self-deprecating, joking | "I'm only mentioning this so you know how bored I was on a Saturday night" — never be earnest about tiny amounts, it makes the sender look petty |
| Housekeeping (typos, code mismatches) | Casual, brief | "A few typos on the summary I wanted to flag" |
| Budget variance (explained) | Acknowledging | "That tracks" or "I see the increases" |
| Budget overrun (unexplained) | Direct question | "Do we have a CO for that?" |
| Missing documentation | Firm but fair | "I don't see one in my records" |
| Potential unauthorized work | Very direct | "This wasn't something we approved" |
| Dispute | Professional, factual | State the facts, request resolution, no threats |

## Writing Rules (apply to BOTH outputs)

- No em dashes. Use commas, periods, or restructure.
- No AI vocabulary: "I'd like to bring to your attention", "I wanted to reach out", "please don't hesitate", "at your earliest convenience", "I hope this email finds you well"
- Short paragraphs. One topic per paragraph.
- Sound like a person, not generating content.
- For trivially small discrepancies (under $1, rounding errors, pennies), either skip them entirely or mention them with a joke. Being serious about $0.02 makes the sender look like they're nickel-and-diming.

---

## Output 1: Homeowner Insight Summary

This is the "what should I actually worry about?" document. Not a list of every finding, but a prioritized narrative about what matters.

### What counts as an insight

An insight is NOT "invoice #305970 doesn't match." That's a finding. An insight is:
- "The GC has three invoice numbers wrong on this draw. That's sloppy bookkeeping. Not a billing concern per se, but it means you can't trust their records as a reference if there's a dispute later."
- "Framing labor was originally budgeted at $57K. It was revised to $150K — already a 2.6x increase. Now it's at $189K with no change order on file. The scope note says ceiling drops, medicine cabinets, and upper-level walls weren't in the original bid. That's real work, but $132K in unplanned framing labor without a CO is the kind of thing that should have been a conversation before it was a line item."
- "Three separate credits for 'kitchen beam' ($3,693 total) suggest work that was started and reversed. Worth understanding whether this was a design change or something that was built without approval and then backed out."

### Structure

1. **Lead with the total and a one-sentence characterization.** "This is a $265K draw that's mostly clean but has a few things worth pushing on."

2. **The big concerns (2-4 items).** These are budget overruns without COs, scope changes that weren't discussed, patterns that suggest the project is running differently than planned. Frame each as: what happened, why it matters, what to do about it.

3. **Future exposure.** Large deposits, upcoming obligations, budget codes that are trending over. "You have $94K in future obligations from Dixon Glass alone."

4. **The bookkeeping stuff.** Invoice number errors, missing documentation, the $0.02 kind of stuff. Group it, handle it lightly. "Three invoice numbers are wrong on the summary. Not a billing issue, just sloppy data entry. Have them fix their records."

5. **Bottom line.** One sentence: pay, pay with conditions, or dispute. And the 2-3 conditions that matter most.

### What NOT to do

- Don't restate every line item. The spreadsheet does that.
- Don't list 13 numbered questions. That's the email's job.
- Don't use "Error/Warning/Info" severity labels. Translate to human judgment: "concerning", "worth asking about", "just a heads-up".
- Don't hedge everything. Have a point of view. "This looks like scope creep that wasn't properly documented" is more useful than "there appears to be a budget variance that may warrant discussion."
- Don't explain what Builder's Comp is or how progress billing works. The homeowner has been through 20 draws. They know.

---

## Output 2: GC Email

### Format

Plain text. No bold, italic, or markdown formatting.

### Structure

For an **initial inquiry** (first email about a new invoice):
1. Acknowledge receipt of the invoice
2. Note any math issues
3. List the most important flags (budget overruns first)
4. Housekeeping items
5. Specific numbered questions
6. Close

For a **response to GC explanation**:
1. Acknowledge what they said (1-2 sentences)
2. Confirm the items that check out (bullet list if several)
3. Ask about anything they didn't address
4. Housekeeping items
5. Any open items from prior invoices
6. Close

For a **follow-up**:
1. Reference the prior email/conversation
2. List what's still outstanding
3. Close

### Email rules

- Lead with the response/acknowledgment if replying to something.
- End with the open question, not a pleasantry.
- Sign off with just "Thanks," and the name. No "Best regards", "Warm regards", "Sincerely".
- Group related items. Don't alternate between budget concerns and typos.
- Questions should be numbered and specific. "Can you walk me through the framing labor increase?" not "Please explain the budget variance."
- Keep budget/CO questions near the top. Keep housekeeping (typos, invoice number corrections) near the bottom.
- For the $0.02 type stuff, mention it with a joke or skip it. Never lead with it.

## Project Context

Project-specific details (GC name, contacts, homeowner name, project address, relationship tone, prior dispute history) should be stored in a `project-context.md` file in the working directory.

If no project context file is found, ask the user for the GC's name and any relevant context before drafting.
