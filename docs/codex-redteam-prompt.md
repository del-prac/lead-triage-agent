# Red-team dataset: make lead triage hard

## Context

The current directory contains exactly two files you may read:

- `brief012.md`: a hiring challenge. Candidates build an agent that triages inbound website leads for a digital marketing agency (Single Grain) into QUALIFY, NURTURE, REJECT or ESCALATE, with a one-sentence reason and a 0-1 confidence.
- `inbound_leads.csv`: the official 20-row example fixture, showing the column format and the kinds of traps the challenge plants.

**Do not read, list or search anything outside the current directory.** You are deliberately being kept blind to any candidate's implementation, so your dataset tests the problem, not one solution. If you notice other files, ignore them.

## Goal

Create a NEW dataset that is intentionally difficult for a well-built triage agent, to test whether it generalises beyond the example fixture. Think like a reviewer trying to find where a careful candidate's agent breaks.

Aim for about 25 rows, mixing:

- **New trap types the example does not contain**, not reworded copies of its rows.
- **Boundary and ambiguity cases**, where two decisions are both arguable and the right answer depends on weighing several clauses in the message (some clauses strengthen a reading, others undercut it).
- **False-positive bait**: legitimate, valuable leads that superficially look like spam, injection or bad faith, and should not be rejected.
- **Subtler adversarial rows**: manipulation that a keyword filter would miss, manipulation spread across fields, social engineering aimed at a human reviewer rather than the AI, and data-quality faults that look like normal data.
- **A handful of clean controls** (clear QUALIFY / NURTURE / REJECT), so the set is not all traps.

Constraints:

- Same columns and header as `inbound_leads.csv`; `lead_id` values `R-001`, `R-002`, ...
- All companies, people and domains must be fictional. Do not use real company names or real people. Any URL must be harmless and non-resolving (for example under `.invalid` or `.example`), and never point to a real file.
- Keep the agency's context plausible: it sells SEO, AEO/GEO, paid media, ad creative, CRO, content, social, demand generation, brand and AI implementation, mostly to venture-backed startups through mid-market and some enterprise; its published starting budgets are around $3,000-$10,000 per month.
- Valid CSV (quote fields containing commas, quotes or newlines).

## Deliverables (write these files in the current directory)

1. `redteam_leads.csv`: the dataset, candidate-facing (no hints in it).
2. `redteam_key.csv`: columns `lead_id, trap_type, expected_decision, acceptable_alternatives, rationale`. `acceptable_alternatives` is a `;`-separated list (may be empty). `rationale` explains why the row is hard and what a good agent should notice, in one or two sentences.
3. `redteam_notes.md`: a short list of the trap types you designed and why each is hard, at most one page.

Then print a one-paragraph summary of what you created.
