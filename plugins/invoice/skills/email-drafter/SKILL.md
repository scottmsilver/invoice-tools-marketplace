---
name: email-drafter
description: "Use when generating an email to a contractor or GC based on invoice audit findings. Trigger when the user asks to 'write an email', 'draft a response', 'turn this into an email', or wants to communicate audit findings, questions, or disputes to their builder."
---

# Email Draft Generator

## Purpose

Turn structured audit findings into a professional email to the general contractor. Calibrate tone to severity. Sound like a homeowner, not a lawyer or an AI.

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

## Writing Rules

- No em dashes. Use commas, periods, or restructure.
- No bold, italic, or markdown formatting. Plain text email.
- No AI vocabulary: "I'd like to bring to your attention", "I wanted to reach out", "please don't hesitate", "at your earliest convenience", "I hope this email finds you well"
- Short paragraphs. One topic per paragraph.
- Lead with the response/acknowledgment if replying to something.
- End with the open question, not a pleasantry.
- Sign off with just "Thanks," and the name. No "Best regards", "Warm regards", "Sincerely".
- Sound like a person typing an email, not generating one.
- For trivially small discrepancies (under $1, rounding errors, pennies), either skip them entirely or mention them with a joke. Being serious about $0.02 makes the sender look like they're nickel-and-diming. Self-deprecation works well here ("only mentioning this so you know how thorough/bored I was").

## Input

The skill expects:
1. **Audit findings** from the draw-analyzer or manual review
2. **Context** about the relationship (GC name, project, any prior disputes)
3. **Purpose**: responding to an explanation, initial inquiry, follow-up, dispute

## Structure

For a **response to GC explanation**:
1. Acknowledge what they said (1-2 sentences)
2. Confirm the items that check out (bullet list if several)
3. Ask about anything they didn't address
4. Housekeeping items (typos, code fixes)
5. Any open items from prior draws
6. Close

For an **initial inquiry** (first email about a new draw):
1. Acknowledge receipt of the draw
2. Note any math issues
3. List the most important flags (budget overruns first)
4. Housekeeping items
5. Specific numbered questions
6. Close

## Project Context

Project-specific details (GC name, contacts, homeowner name, project address, relationship tone, prior dispute history) should be stored in a `project-context.md` file in the working directory. This keeps sensitive information local and out of the skill itself.

If no project context file is found, the skill will ask the user for the GC's name and any relevant context before drafting.
