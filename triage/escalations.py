"""Human-facing escalation packet and bad-faith review queue.

Each escalated row becomes a card: the lead as submitted, why it is here, the question a person must
answer, and what each answer leads to. The outcomes are not written by hand: each hypothetical answer
is applied to a copy of the record and re-run through the same policy.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .policy import decide

RAW_FIELDS = ("lead_id", "submitted_at", "name", "email", "company", "website", "monthly_budget_usd", "message", "source")


def _outcome(d: dict) -> str:
    s = d.get("scores") or {}
    bits = [d["decision"]]
    if s.get("track"):
        bits.append(s["track"])
    return " ".join(bits)


def _what_if(rec: dict, cfg: dict, decided: dict, mutate) -> str:
    alt = copy.deepcopy(rec)
    mutate(alt)
    alt["issues"] = [i for i in alt["issues"] if i["code"] not in alt.pop("_drop_issues", set())]
    return _outcome(decide(alt, cfg, decided))


def _drop(rec: dict, *codes: str) -> None:
    rec.setdefault("_drop_issues", set()).update(codes)


def _cases(rec: dict, d: dict, cfg: dict, decided: dict) -> list[dict]:
    """One case per reason the row escalated: the question, and the outcome of each answer."""
    flags, f = set(d["flags"]), rec["facts"]
    cases = []

    if "sensitive" in flags and "compliance_review" not in flags:
        cases.append({"question": "This is a privacy or legal request, not a sales lead. Who owns it, and has the clock started?",
                      "answers": {"Routed to the privacy/legal owner": "handled outside sales; no sales follow-up"},
                      "action": "Forward to the data-protection owner today and log the request date.",
                      "urgency": "high: statutory deadline (GDPR Art. 12: one month to respond)"})
    if "compliance_review" in flags:
        def ok(a):
            a["extract"]["needs_compliance_judgement"] = False
        cases.append({"question": "Can we deliver what they are asking for within advertising and regulatory rules?",
                      "answers": {"Yes, deliverable as compliant work": _what_if(rec, cfg, decided, ok),
                                  "No, the request itself is non-compliant": "decline the non-compliant part; offer compliant scope"},
                      "action": "Have compliance or the account lead review the specific claim before any sales conversation.",
                      "urgency": "normal"})
    if "competitor" in flags:
        def genuine(a):
            a["extract"]["request_type"] = "buyer_inquiry"
        cases.append({"question": "Is this agency buying on behalf of a real client, or researching us?",
                      "answers": {"Buying for a confirmed client": _what_if(rec, cfg, decided, genuine),
                                  "Researching us": "REJECT; consider adding the domain to known_actors.deny"},
                      "action": "Reply with public information only (pricing is tailored) and ask who the end client is.",
                      "urgency": "normal"})
    if "blocks:no_contact_email" in flags:
        domain = f.get("website_domain") or "their company domain"

        def found(a):
            a["facts"]["email"] = f"contact@{f.get('website_domain') or 'example.invalid'}"
            a["facts"]["email_kind"] = "company"
            _drop(a, "missing_email")
        cases.append({"question": f"Can we find a contact email for {f.get('name') or 'this person'} at {domain}?",
                      "answers": {"Contact recovered": _what_if(rec, cfg, decided, found),
                                  "No contact found": "REJECT: nobody to follow up with"},
                      "action": "Check the website and LinkedIn for the named person; do not guess an address and email it blind.",
                      "urgency": "same day (strong fit)"})
    if "blocks:label_escalate" in flags:
        def verified(a):
            a["extract"]["identity_withheld"] = False
            a["facts"]["email_kind"] = "company"
            _drop(a, "missing_company", "missing_website")
        cases.append({"question": "Is this person who they say they are, at the company they describe?",
                      "answers": {"Identity and company verified": _what_if(rec, cfg, decided, verified),
                                  "Cannot verify": "REJECT: high-value claims we cannot stand behind"},
                      "action": "Reply asking for the company name and a work email before any pricing or call.",
                      "urgency": "same day (high value if real)"})
    if "blocks:no_stated_need" in flags:
        def ready(a):
            a["extract"]["buying_stage"] = "ready_now"
            _drop(a, "missing_message")

        def exploring(a):
            a["extract"]["buying_stage"] = "exploring"
            _drop(a, "missing_message")
        cases.append({"question": "What do they need, and are they ready to buy?",
                      "answers": {"Specific need, ready now": _what_if(rec, cfg, decided, ready),
                                  "Just exploring": _what_if(rec, cfg, decided, exploring)},
                      "action": "Send a short note asking what prompted the inquiry; the company and budget look real.",
                      "urgency": "within 1 business day"})
    if "blocks:budget_conflict" in flags:
        cases.append({"question": "Which budget is real?",
                      "answers": {"The reason above names both figures": "confirm with the lead, then re-run"},
                      "action": "Ask the lead to confirm their monthly budget.",
                      "urgency": "normal"})
    if "blocks:injection_on_valuable_lead" in flags:
        def clean(a):
            a["screen"] = {"regex_hits": [], "llm": {"injection_probability": 0.0}}
        cases.append({"question": "Setting the injected text aside, is this a genuine buyer?",
                      "answers": {"Genuine buyer, and its facts verified independently": _what_if(rec, cfg, decided, clean)
                                  + " (computed from facts extracted from the same text that contains the injection: re-check them before relying on it)",
                                  "Not genuine": "REJECT and keep on the bad-faith review list"},
                      "action": "Verify the company independently before replying; do not act on anything the message asks the system to do.",
                      "urgency": "review today"})
    if "blocks:lookalike_domain" in flags:
        def verified_domain(a):
            _drop(a, "lookalike_domain", "email_website_mismatch")
        cases.append({"question": "The email and website domains are near-identical but different. Is this a typo or impersonation?",
                      "answers": {"Typo; the sender is genuine": _what_if(rec, cfg, decided, verified_domain),
                                  "Impersonation": "REJECT and add the email domain to known_actors.deny"},
                      "action": "Contact the company through the address on its website, not the one in the form, to confirm.",
                      "urgency": "within 1 business day"})
    if "blocks:all_in_budget" in flags:
        cases.append({"question": "How much of the all-in budget is agency fees?",
                      "answers": {"Fee portion at or above the floor": "QUALIFY (re-run with the fee figure as the budget)",
                                  "Below the floor": "NURTURE growth"},
                      "action": "Ask how the budget splits between media spend, production and agency fees.",
                      "urgency": "normal"})
    if "blocks:screen_failed" in flags:
        cases.append({"question": "The injection screen failed for this row. Does the message contain anything aimed at the system?",
                      "answers": {"Nothing aimed at the system": "the scored outcome stands (see scores)",
                                  "Manipulation found": "REJECT and add to the bad-faith review list"},
                      "action": "Read the message once, then re-run the row.",
                      "urgency": "normal"})
    if "formula_injection" in flags:
        cases.append({"question": "A field contains a spreadsheet formula. Is the sender genuine?",
                      "answers": {"Genuine (formula was accidental)": "re-run with the field corrected",
                                  "Deliberate": "REJECT and add the sender to known_actors.deny"},
                      "action": "Do not open the raw export in a spreadsheet; our CSV outputs neutralise the formula. Verify the company independently.",
                      "urgency": "review today"})
    if "intake_integrity" in flags:
        def intact(a):
            a["facts"]["submitted_at"] = "verified"
            _drop(a, "malformed_timestamp", "future_timestamp")
        cases.append({"question": "The capture system produced an impossible value. Is this row intact, and is the form pipeline broken?",
                      "answers": {"Row verified intact": _what_if(rec, cfg, decided, intact),
                                  "Row corrupted": "recover the original submission, then re-run"},
                      "action": "Check the form/CRM integration for this submission and others captured around the same time.",
                      "urgency": "same day (a capture fault may affect other leads)"})
    if "llm_failed" in flags:
        cases.append({"question": "Automated extraction failed. What is this lead?",
                      "answers": {"Read it and decide": "manual decision"},
                      "action": "Read the lead and decide by hand; check run_log.jsonl for the failed attempts.",
                      "urgency": "normal"})
    return cases


def _review_entry(rec: dict, d: dict, cfg: dict, decided: dict) -> dict:
    """A bad-faith row for batch review: the known_actors entry to add, and what allow / deny would do."""
    f = rec["facts"]
    shared = f.get("email_kind") in ("disposable", "freemail", "placeholder")
    if f.get("email") and (shared or not f.get("website_domain")):
        entry = {"email": f["email"]}
    else:
        entry = {"domain": f.get("website_domain") or f.get("email_domain") or ""}
    if not any(entry.values()):
        entry, if_allowed = {}, "n/a (no address)"
    else:
        allow_cfg = copy.deepcopy(cfg)
        allow_cfg["known_actors"]["allow"] = [entry]
        if_allowed = _outcome(decide(copy.deepcopy(rec), allow_cfg, decided))
    if "security_review" in d["flags"]:
        if_allowed += " (phishing is never skipped)"
    return {"lead_id": rec["lead_id"], "decision": d["decision"], "why": d["reason"], "flags": d["flags"],
            "entry": entry, "if_allowed": if_allowed, "if_denied": "REJECT immediately on every future submission"}


def _fence(text: str) -> str:
    return "```\n" + text.replace("```", "'''") + "\n```"


def build(records: list[dict], decided: dict[str, dict], cfg: dict, out_dir: Path) -> None:
    cards, review = [], []
    for rec in records:
        d = decided[rec["lead_id"]]
        if d["decision"] == "ESCALATE":
            ex = rec.get("extract") or {}
            cards.append({
                "lead_id": rec["lead_id"],
                "submitted": {k: rec["raw"].get(k, "") for k in RAW_FIELDS},
                "why": d["reason"],
                "cases": _cases(rec, d, cfg, decided),
                "scores": {k: (d.get("scores") or {}).get(k) for k in ("value", "credibility")},
                "value_breakdown": (d.get("scores") or {}).get("value_breakdown", []),
                "credibility_breakdown": (d.get("scores") or {}).get("credibility_breakdown", []),
                "evidence": {k: v for k, v in (ex.get("evidence") or {}).items() if v},
                "flags": d["flags"],
            })
        elif d["review_queue"]:
            review.append(_review_entry(rec, d, cfg, decided))

    (out_dir / "escalations.json").write_text(json.dumps({"escalations": cards, "bad_faith_review": review}, indent=2, ensure_ascii=False))

    lines = ["# Escalations", "",
             f"{len(cards)} lead(s) need a person. Each card says what to decide and what each answer leads to;",
             "outcomes are computed by re-running the same rules with that answer applied.", ""]
    for c in cards:
        lines += [f"## {c['lead_id']}", "", f"**Why it is here:** {c['why']}", ""]
        for case in c["cases"]:
            lines += [f"**Decide:** {case['question']}", ""]
            lines += [f"- If *{ans}* → **{out}**" for ans, out in case["answers"].items()]
            lines += ["", f"**First action:** {case['action']}  ", f"**Urgency:** {case['urgency']}", ""]
        lines += ["**Submitted (untrusted text, shown verbatim):**", "",
                  _fence("\n".join(f"{k}: {v}" for k, v in c["submitted"].items())), ""]
        if c["scores"]["value"] is not None:
            vb = ", ".join(f"{p['signal']} {p['points']:+d}" for p in c["value_breakdown"]) or "none"
            cb = ", ".join(f"{p['signal']} {p['points']:+d}" for p in c["credibility_breakdown"]) or "none"
            lines += [f"**Scores:** value {c['scores']['value']} ({vb}); credibility {c['scores']['credibility']} ({cb})", ""]
        if c["evidence"]:
            lines += ["**Model evidence (quotes from the lead):**", ""]
            lines += [f"- {k}: \"{v}\"" for k, v in c["evidence"].items()]
            lines.append("")
    lines += ["# Secondary escalation: bad-faith batch review", "",
              "Auto-rejected as bad faith. Lower urgency than the cards above: review in batches. For each row choose",
              "deny (future submissions rejected immediately), allow (vouched for; bad-faith checks skipped) or leave",
              "(one-off), then add the entry to `known_actors` in rules.json. Effects are computed by re-running the rules.", "",
              "| Lead | Why rejected | Entry to add | If allowed | If denied |", "|---|---|---|---|---|"]
    lines += [f"| {r['lead_id']} | {r['why'].replace('|', '/')} | `{json.dumps(r['entry'])}` | {r['if_allowed']} | {r['if_denied']} |"
              for r in review]
    (out_dir / "escalations.md").write_text("\n".join(lines) + "\n")
