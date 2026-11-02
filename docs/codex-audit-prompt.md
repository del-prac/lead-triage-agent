# Adversarial code audit: lead triage agent

You are auditing a small Python project you did not write, in the current repository. Read-only: do not modify anything.

## What it is

A take-home challenge submission: an agent that triages inbound website sales leads (CSV) into QUALIFY / NURTURE / REJECT / ESCALATE with a reason and a confidence. The design intent is in `README.md`. Deterministic Python makes every decision (`triage/policy.py`, `triage/scoring.py`); LLMs only screen for prompt injection (`prompts/screen.md`) and extract structured facts (`prompts/extract.md`), called via `triage/llm.py`. Weights and thresholds live in `rules.json`. Tests: `tests/test_deterministic.py`. Example outputs: `out/`.

Do not read `private/` or `runs/`; they are not part of the code under review.

## Goal

Find real defects, ranked by impact. Be skeptical and concrete. In scope:

1. **Correctness of the decision logic**: gate ordering in `policy.py`, edge cases that produce a wrong or contradictory decision, reasons that misdescribe the decision, confidence values that do not mean what they claim, any input that crashes the run.
2. **Prompt-injection defence**: any path by which text in a lead can influence a decision other than through the documented extracted fields; weaknesses in the regex screen (false negatives and false positives); anything the LLM output can do that the schema validation does not catch.
3. **Robustness on unseen inputs**: malformed CSVs, unusual encodings, extra or missing columns, empty files, very long fields, numeric edge cases in budget parsing, the `--backend api` path (it has never been executed: check it against the Anthropic Messages API as you understand it), cache-key collisions, concurrency in the thread pool and the run log.
4. **Dedupe / grouping** (`triage/dedupe.py`): cases where two different people are merged, or the same person is not.
5. **Escalation packet** (`triage/escalations.py`): what-if outcomes that are computed wrongly or could mislead a reviewer.
6. **Mismatch between README claims and the code.**
7. **Test gaps**: important behaviours with no test.

Out of scope: style, naming, adding type hints, restructuring for its own sake, and the choice of weights in `rules.json` (they are documented assumptions) unless a weight produces an internally inconsistent result.

## Method

Read the code; trace specific inputs through it by hand where useful. You may run `python -m pytest tests/ -q` and you may run Python snippets that import the modules with synthetic records, but do not invoke the LLM steps (`claude` / network). For every finding, state a concrete input or state that triggers it.

## Output

First a short prose summary (at most 10 lines). Then a section titled exactly `## Findings JSON` containing one fenced JSON array. Each element:

```json
{"id": "F-001", "severity": "high|medium|low", "file": "triage/policy.py", "line": 123,
 "title": "...", "trigger": "concrete input or state", "observed": "what happens",
 "expected": "what should happen", "confidence": "verified|likely|speculative",
 "suggested_fix": "one or two sentences"}
```

Use `verified` only when you ran it or traced it line by line. Order by severity. If you find nothing in a category, say so in the summary rather than padding.
