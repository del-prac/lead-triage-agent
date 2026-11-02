# Lead Triage Agent (Beat Claude: AI Automation Intern 012, brief 2026-07)

First-pass routing of inbound website leads for Single Grain. Every row of the input CSV gets
`QUALIFY`, `NURTURE`, `REJECT` or `ESCALATE`, a one-sentence reason and a 0-1 confidence, plus a track
and the scores behind the call. **Escalations** (the leads a person must decide) are listed with their
questions and options in `out/escalations.md`, and in the **Review: escalations** tab of the review UI.

**Write-up:** [`WRITEUP.md`](WRITEUP.md) (the same text as submitted, [`WRITEUP.pdf`](WRITEUP.pdf)).

Fixture checksum (`shasum -a 256 fixtures/inbound_leads.csv`):
`cc1927ca771c37b186a2abdb7b9757594da79ec6dda074e5a514dd2761cc8599`

## Run it

```bash
# Dev loop: the local `claude` CLI, all tools disabled, no project context. No Python dependencies.
python -m triage --input fixtures/inbound_leads.csv --out out/

# API backend (same prompts and schemas; schema enforced through a forced tool call).
pip install anthropic
export ANTHROPIC_API_KEY=...
python -m triage --backend api --input fixtures/inbound_leads.csv --out out/
# (or put ANTHROPIC_API_KEY=... in a git-ignored .env file in the project root)
# Verified: on the fixture the API backend makes the same 20 decisions as the CLI backend (out-api/),
# at about $0.20 per 20 leads (Sonnet extraction ~95% of it); the run prints its cost.

# Any other fixture with the same columns, plus an optional second-opinion audit by a stronger model:
python -m triage --input path/to/new_leads.csv --out out-new/ --audit

# Change rules.json, then re-decide a finished run from its stored extractions (no model calls, no cache):
python -m triage --rescore --out out/

# Deterministic tests (no model calls):
python -m pytest tests/
```

## Outputs (in `--out`)

| File | For | What it is |
|---|---|---|
| `decisions.csv` | the brief | `lead_id, decision, track, reason, confidence, value_score, credibility_score, readiness, flags, review_queue` |
| `sales_queue.csv` | sales | Every QUALIFY, highest value first, with budget and confirm-on-call notes |
| `escalations.md` / `.json` | a person | One card per ESCALATE: the question to answer, what each answer leads to (computed by re-running the rules), first action, urgency, the lead verbatim, scores, model evidence. Then the **secondary escalation**: bad-faith rejects for batch review, each with the deny/allow entry and its effect |
| `decisions.json` | debugging | Every normalized fact, issue, screen result, extracted field and score breakdown per row |
| `run_log.jsonl` | evidence | Every step per row, every prompt and model output, validation errors and retries. First line carries the input sha256 |
| `audit.json` | a person | (`--audit`) Opus lists decisions it disagrees with. Advisory only; never changes a decision |

## Review UI (optional)

```bash
python -m triage.ui            # http://127.0.0.1:8012, reads out/ ; --out out-redteam/ for another run
```

One local page (standard library only, bound to 127.0.0.1 unless `--host` names a private address) with
four tabs. **Leads**: filter by decision, track, value and credibility, sort by any column (value,
highest first, by default); a toggle shows each lead's score breakdown, the model's evidence quotes, the
injection-screen verdict and the message as submitted; an ESCALATE badge opens that lead's card.
**Review: escalations**: one card per escalation (the question, what each answer leads to, first action,
urgency), then the bad-faith batch list with deny / allow buttons that write `known_actors` in
`rules.json`. **Source data**: every CSV row exactly as submitted beside its decision, sortable, to check
the triage against the source. **Tune**:
edit any weight or threshold and see, live, which decisions, scores and sales-queue positions change
against the saved rules; Save writes `rules.json` and rewrites the run's output files on the spot (the
same `--rescore`, no model calls). Every number is computed by the agent's own policy code on the run's cached model
outputs, so the UI makes no model calls and cannot disagree with the agent. Lead text is rendered as
text, never as HTML.

## How it works

| Step | Done by | What |
|---|---|---|
| 1. Normalize | Python | Validate and clean every field: timestamp, email shape and kind (company / freemail / education / disposable / placeholder), website, budget (`"15k"` → 15000; `999999` is a placeholder, never a budget), HTML stripped, links in the message checked for executables. Produces facts and issues; decides nothing |
| 2. Injection screen, layer 1 | Python | Patterns over **every** free-text field, not just the message |
| 3. Injection screen, layer 2 | Haiku 5.5 | Catches rewordings the patterns miss; returns a probability |
| 4. Extraction | Sonnet 5.5 | Request type, services, industry, scale, buying stage, locations, growth, compliance need, language, plus a verbatim **evidence quote** for each judgement. Told to weigh every clause of the message, not one word. Describes the lead; never decides |
| 5. Dedupe and grouping | Python | Same person + same ask → duplicate (inherits the decision, routed once). Same person + different ask → decided on its own merits. Same company, different people → cross-referenced |
| 6. Scores and decision | Python | Every point and every decision; see below |
| 7. Escalation packet, sales queue | Python | What a person needs to act |
| 8. Audit (optional) | Opus 5.5 | Second opinion on the finished decisions |

Each model is matched to the difficulty of its step (`rules.json` → `models`). All model output must match
a JSON schema: two attempts, then the row escalates as "extraction failed".

## The decision model

**Two observable scores**, every point from `rules.json` and listed per row:

- **Value** (what the lead is worth if real): budget band, company scale, services we sell, industry
  category, timeline, budget expected to grow, source (referral / podcast / webinar).
- **Credibility** (how far the row can be trusted): starts at 100; penalties for free-mail, education,
  disposable or placeholder email, email/website mismatch, missing company or website, identity
  explicitly withheld, budget contradicting other fields, resubmission; bonus for referral or an
  allow-listed sender.

**Label = fit × readiness, gated by credibility; value orders the work.**

| Situation | Decision | Track |
|---|---|---|
| Meets the $3K floor, ready now | QUALIFY | `sell_now` |
| Meets the floor, actively selecting a vendor (evaluating agencies, RFP) | QUALIFY | `process` |
| Meets the floor, exploring or waiting on a later event | NURTURE | `readiness` |
| Credible buyer below the floor | NURTURE | `growth` (help them grow into a client) |
| High value, low credibility | ESCALATE | |
| $0 budget, or too little of the row can be trusted | REJECT | |

`sales_queue.csv` lists every QUALIFY in descending value, so sales works the most valuable first.

**Gates** (questions of kind, not degree), in order: extraction failure → ESCALATE; duplicates inherit
their primary's decision only after their own safety checks pass; privacy/legal and compliance requests →
ESCALATE (even from a deny-listed sender); deny list → REJECT; phishing, junk, non-buyers, fishing → REJECT;
values the capture system produced that no person types (impossible or future timestamp) → ESCALATE as an
intake-integrity fault; a spreadsheet formula in any field → ESCALATE for security review.

**Kept out of the sales queue without escalating:** a placeholder-looking budget, and a lead that asks not
to be contacted yet (NURTURE, flagged `honour_no_contact`).

**Input and output safety:** the CSV is validated before any model call (UTF-8, a `lead_id` column, unique
ids, quoted line breaks preserved) and the run stops with a clear message otherwise. Every CSV this tool
writes neutralises cells starting with `= + - @`, so opening an export cannot execute a formula a lead typed.

**Escalation is not a dumping ground.** Missing data alone never escalates; the test is whether the
missing or conflicting field would change the call on a lead worth having:

- No contact email on a strong lead; empty message on a real-looking company and budget.
- An email domain that is a lookalike of the website's (`cobaltgrove` vs `cobalt-grove`): typo or
  impersonation. An all-in budget (includes media spend) whose plausible fee share is under the floor.
  A failed model injection screen.
- Budget evidence that disagrees (the field vs the message, a $0 field with a figure in the message, an
  enterprise with a $50 budget, a multi-location business below the floor): a **credible** lead is scored on the larger reading and
  flagged "confirm on the first call" (a wasted call costs far less than a lost deal); a lead that is
  **not credible** escalates.
- Another agency asking for pricing: scored as if it were a genuine buyer; held for a person if it would
  be worth having, with that score in the reason.

**Bad faith** (injection attempts, phishing, junk, fishing) → REJECT and onto the secondary escalation:
a batch review where a person adds the sender to `known_actors.deny` or `.allow`. An injection-flagged row
is never QUALIFY; if it would qualify on merits it escalates instead.

## Prompt-injection defence

Lead text is data: wrapped as JSON in `<lead_data>` tags the system prompts describe as untrusted; models
have no tools (`claude -p --tools "" --safe-mode` from an empty working directory); outputs must match a
schema; and every decision is made in Python, where lead text cannot reach it. Tested on the fixture's
injection row and on our own variants in `tests/adversarial_leads.csv` (polite wording, injection in the
name field, and a false-positive control: "Ignore my previous email, our budget changed").

## Assumptions

Labels: *benchmarked* (from a cited source) or *assumed* (our judgement). All live in `rules.json`.

| Assumption | Label | Basis |
|---|---|---|
| Qualify floor $3K/month | benchmarked | Single Grain's own location pages quote startups at $3K–$10K; its contact form's lowest bracket is "Less than $3,000". Directories (Clutch, DesignRush) list $10K+ |
| High-value budget $10K/month | benchmarked | the directories' minimum |
| Regulated industries are in-market | benchmarked | Single Grain advertises healthcare and finance work |
| All value and credibility weights | assumed | starting points, meant to be recalibrated against real outcomes |
| A tiny budget from a credible enterprise is in thousands | assumed | flagged for confirmation, never silent |
| At least 15% of an all-in budget is agency fee | assumed | below that, the fee may miss the floor, so the lead escalates |
| Websites in rows are not fetched | assumed | the fixture is synthetic; fetching untrusted URLs is a risk |

## Independent review (a different model lineage: OpenAI Codex)

- **Code audit** (`docs/codex-audit-prompt.md` → `docs/codex-audit-001.md`): read-only, adversarial;
  16 findings, 6 high. All were repaired, each with a regression test (`test_F0xx_*`); 18 of the 19 new
  tests fail on the pre-fix commit and pass after.
- **Blind red-team dataset** (`docs/codex-redteam-prompt.md` → `tests/redteam_codex/`): a separate Codex
  agent that saw only the brief and the example fixture (its session log confirms it read nothing else)
  wrote 25 deliberately hard rows plus its own expected decisions. We ran the agent on them before
  reading the key: **17 of 25 acceptable before the fixes, 24 of 25 after** (`runs/09-redteam-before-fixes`
  vs `out-redteam`). Fixes were general rules (`test_R0xx_*`), never row patches.

## Design history

`runs/` keeps each design's full output against the same fixture, so every change is inspectable:

| Run | Design |
|---|---|
| `01-budget-ladder` | Budget thresholds pick the label |
| `02-opus-audit.json` | Opus audit of run 2 (budget ladder + first fixes). It caught a duplicate that could be routed twice and a missing decision-path entry; it also made one wrong claim, which a person caught |
| `03-scored-no-ceiling` | Value × credibility matrix; a $3K lead qualified by stacking small signals |
| `04-scored-ceiling` | Budget ceiling added to stop that |
| `05-readiness` | Label = fit × readiness; value only ranks |
| `06-nurture-tracks` | Growth vs readiness nurture; evidence quotes; competitors scored |
| `07-compared-against-key` | Qualify tracks; budget conflicts resolved by credibility. **The one-time comparison with the public expected decisions was made against this run** |
| `08-post-comparison-10k-rule` | Post-comparison changes, including a rule later withdrawn as overfit |
| `09-redteam-before-fixes` | The agent on Codex's blind red-team rows, before the audit fixes |
| `10-fixture-before-audit-fixes` | The fixture, before the audit fixes (decisions identical to `out/`) |
| `out/`, `out-redteam/`, `out-adversarial/` | Final: audit and red-team fixes, and the "weigh every clause" instruction |
