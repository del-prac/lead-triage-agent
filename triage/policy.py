"""Deterministic decision policy. The LLMs supply facts; this file makes every call.

Order of precedence:
  1. extraction failed                                             -> ESCALATE
  2. duplicates inherit their primary's decision, but only after this row's own
     safety checks pass (injection, phishing, formula, sensitive)
  3. sensitive requests (privacy, legal, compliance), even from deny-listed senders -> ESCALATE
  4. known bad actors (deny list)                                  -> REJECT
  5. bad faith / non-buyers (phishing, junk, fishing, non-buyers)  -> REJECT
  6. label = fit (budget floor) x readiness (buying stage), gated by credibility;
     high value + low credibility -> ESCALATE; QUALIFY is worked in descending value order;
     tracks: QUALIFY sell_now | process, NURTURE readiness | growth
  7. intake integrity (impossible or future timestamp, spreadsheet formula in a field) -> ESCALATE
  8. competitors: scored as if genuine; ESCALATE with that score if worth having, else REJECT
  9. adjustments: placeholder budget or a no-contact request never reach QUALIFY
 10. blockers: a missing or conflicting field that would change the
     label, on a lead worth having                                -> ESCALATE
 11. injection attempts: never QUALIFY; REJECT unless blocked      -> REJECT / ESCALATE
"""
from __future__ import annotations

from .scoring import credibility_confidence, credibility_score, drivers, label, value_score

FLAGGED_ISSUES = {
    "malformed_timestamp", "future_timestamp", "email_website_mismatch", "lookalike_domain", "placeholder_budget",
    "unparseable_budget", "insecure_website", "placeholder_website", "malformed_email", "malformed_website",
    "disposable_email", "missing_email", "missing_message", "executable_link", "budget_normalized", "formula_injection",
}
CONTACT_MISSING = {"missing_email", "malformed_email"}
INTEGRITY = {"malformed_timestamp", "future_timestamp"}


def _result(decision, reason, confidence, flags, review=False, path=None, scores=None):
    out = {"decision": decision, "reason": reason, "confidence": round(max(0.3, min(0.95, confidence)), 2),
           "flags": flags, "review_queue": review, "path": path or []}
    out["scores"] = scores or {}
    return out


def _known_actor(f: dict, cfg: dict, kind: str) -> dict | None:
    """Match an entry by exact email, or by domain (email or website). Shared domains such as
    disposable or free-mail providers should be listed by email, never by domain."""
    domains = {d for d in (f.get("email_domain"), f.get("website_domain")) if d}
    for entry in cfg["known_actors"][kind]:
        if entry.get("email") and entry["email"].lower() == (f.get("email") or ""):
            return entry
        if entry.get("domain") and entry["domain"].lower() in domains:
            return entry
    return None


def _budget_conflict(f: dict, ex: dict, cfg: dict) -> tuple[float, str] | None:
    """When the row's budget evidence disagrees, return (alternative budget, what the alternative is), else None.

    Only alternatives the row itself supports: a figure in the message, the stated number read in
    thousands when it is implausibly small for the scale the lead claims, or a per-location reading.
    No invented numbers.
    """
    stated, text = f["budget"], ex.get("budget_in_text")
    if stated is None:
        return None
    if stated <= 0:
        return (text, "the figure in the message") if text and text > 0 else None
    if text and not (0.5 <= text / stated <= 2):
        return text, "the figure in the message"
    if ex["company_scale"] in ("mid_market", "enterprise") and stated <= cfg["budget"]["scale_conflict_max"]:
        return stated * 1000, "the budget read in thousands, given the stated company scale"
    n = ex.get("location_count") or 0
    if n >= 2 and stated < cfg["budget"]["floor"] <= stated * n:
        return stated * n, f"the budget read as per location ({n} locations)"
    return None


def decide(rec: dict, cfg: dict, decided: dict[str, dict]) -> dict:
    f, ex, links = rec["facts"], rec.get("extract"), rec["links"]
    issues = {i["code"] for i in rec["issues"]}
    t = cfg["scoring"]["thresholds"]

    if ex is None:
        return _result("ESCALATE", "Automated extraction failed schema validation twice, so a human needs to read this lead.",
                       0.6, ["llm_failed"], path=["llm_failed"])

    flags: list[str] = sorted(issues & FLAGGED_ISSUES)
    scr = rec["screen"]
    llm_screen = scr.get("llm")
    # The model screen counts only when it says the text addresses an automated system (not, e.g., a
    # procurement rule aimed at human recipients).
    injection = bool(scr["regex_hits"]) or bool(
        llm_screen and llm_screen.get("injection_probability", 0) >= cfg["injection_threshold"]
        and llm_screen.get("targets_agent", True))
    screen_failed = llm_screen is None
    if injection:
        flags.append("injection_attempt")
    if screen_failed:
        flags.append("screen_failed")
    rt = ex["request_type"]

    # 2. Duplicates inherit only after this row's own safety checks pass.
    if dup := links.get("duplicate_of"):
        unsafe = injection or issues & {"executable_link", "formula_injection"} or rt == "privacy_or_legal_request" \
            or ex["needs_compliance_judgement"]
        if not unsafe:
            p = decided[dup]
            return _result(p["decision"], f"Duplicate of {dup} (same person, same ask): route once via {dup}, with this message appended to it.",
                           p["confidence"], [f"duplicate_of:{dup}", "do_not_route_separately"], path=["duplicate"],
                           scores=dict(p.get("scores") or {}))
        flags.append(f"duplicate_of:{dup}")

    for key in ("resubmission_of", "same_account_as", "followups"):
        if links.get(key):
            val = links[key] if isinstance(links[key], str) else ",".join(links[key])
            flags.append(f"{'has_followups' if key == 'followups' else key}:{val}")
    if ex["language"] and ex["language"] != "en":
        flags.append(f"non_english:{ex['language']}")
    if ex["regulated_industry"]:
        flags.append("regulated_industry")

    # Scores are computed up front so every row, including gated ones, carries them in the output.
    conflict = _budget_conflict(f, ex, cfg)
    budget = f["budget"] if f["budget"] is not None else ex.get("budget_in_text")
    allowed = _known_actor(f, cfg, "allow")
    cred, c_parts = credibility_score(f, ex, issues, links, cfg, bool(allowed), conflict is not None)
    # A budget conflict on a credible lead is resolved toward the larger reading (a wasted sales call
    # costs far less than a lost deal) and flagged for the first call. On a non-credible lead it escalates.
    conflict_unresolved = None
    if conflict:
        alt, what = conflict
        if cred >= t["credibility_trusted"]:
            flags.append(f"budget_conflict:confirm_${max(budget, alt):,.0f}_on_first_call")
            budget = max(budget, alt)
        else:
            conflict_unresolved = (f"budget evidence disagrees (${f['budget']:,.0f} stated vs ${alt:,.0f} from {what}) "
                                   f"and credibility {cred} is too low to assume the larger figure")
    value, v_parts = value_score(f, ex, cfg, budget)
    scores = {"value": value, "credibility": cred, "value_breakdown": v_parts, "credibility_breakdown": c_parts}
    if allowed:
        flags.append("known_relationship")

    def gate(decision, reason, conf, extra, review=False, path=None):
        return _result(decision, reason, conf, flags + extra, review=review, path=path, scores=scores)

    # 3. Sensitive requests are legal obligations whoever sends them, so they precede the deny list.
    if rt == "privacy_or_legal_request":
        return gate("ESCALATE", "Privacy or legal request, which needs whoever owns data and legal requests (often on a deadline), not sales triage.",
                    0.9, ["sensitive"], path=["sensitive"])
    if ex["needs_compliance_judgement"]:
        return gate("ESCALATE", "Handling this request needs a compliance judgement that should not be automated.",
                    0.85, ["sensitive", "compliance_review"], path=["compliance"])
    # 4-5. Known bad actors, bad faith and non-buyers.
    if denied := _known_actor(f, cfg, "deny"):
        who = denied.get("note") or denied.get("email") or denied.get("domain")
        return gate("REJECT", f"Known bad-faith actor on the deny list ({who}).",
                    0.95, ["known_bad_actor"], review=True, path=["deny_list"])
    if "executable_link" in issues or rt == "billing_or_payment":
        return gate("REJECT", "Suspected phishing (payment pressure and/or a link to an executable); not a lead, flagged for security review and the link was not opened.",
                    0.9, ["security_review"], review=True, path=["phishing"])
    if rt == "spam_or_test" or (f.get("email_kind") == "placeholder" and "placeholder_budget" in issues):
        return gate("REJECT", "Test or junk submission with no real request.", 0.9, ["junk"], review=True, path=["junk"])
    if rt in ("mentorship_or_career", "vendor_or_sales_pitch"):
        what = "a mentorship or career request" if rt == "mentorship_or_career" else "a vendor pitch"
        return gate("REJECT", f"Not a buyer: {what}, so there is no sales opportunity.", 0.9, ["not_a_buyer"], path=["not_a_buyer"])
    # Allow-listed senders were vouched for by a reviewer: the bad-faith judgements (fishing, competitor,
    # injection) are skipped for them. Phishing, junk and sensitive requests are never skipped.
    if f.get("email_kind") == "disposable" and ex["requests_sensitive_material"] and not allowed:
        return gate("REJECT", "Disposable email address plus an up-front request for methodology or client results, a fishing pattern rather than a buyer.",
                    0.75, ["fishing"], review=True, path=["fishing"])

    # 6. Label = fit x readiness, gated by credibility. Value orders the sales queue.
    # An empty message gives no evidence of readiness: code overrides whatever the model guessed.
    stage = "undetermined" if "missing_message" in issues else ex["buying_stage"]
    grows = ex["budget_expected_to_grow"]
    decision, why, track = label(budget, stage, grows, value, cred, cfg)
    as_captured = decision + (f" {track}" if track else "")

    # 7. Intake integrity: values no person types (an impossible or future timestamp) mean the capture
    # system corrupted the row; a spreadsheet formula in a field is an attack on whoever opens the export.
    if issues & INTEGRITY:
        bad = "an impossible" if "malformed_timestamp" in issues else "a future"
        return gate("ESCALATE", f"The capture system produced {bad} timestamp ({rec['raw'].get('submitted_at')}), "
                    f"so the row may be corrupted; verify it before acting (as captured it would be {as_captured}).",
                    0.85, ["intake_integrity"], path=["intake_integrity"])
    if "formula_injection" in issues:
        return gate("ESCALATE", "A field starts with a spreadsheet formula, which would execute when the export is opened; "
                    f"it has been neutralised in the output, and the sender's identity should be verified before contact "
                    f"(as captured it would be {as_captured}).", 0.85, ["security_review"], review=True, path=["formula_injection"])

    # 8. Competitors are scored as if genuine, then held for a human if they would be worth having.
    if rt == "competitor_or_research" and not allowed:
        meets_bar = decision == "QUALIFY" or (decision == "NURTURE" and track == "readiness")
        if meets_bar and cfg["scoring"]["competitor_action"] == "ESCALATE":
            as_valid = decision + (f" {track}" if track else "")
            return gate("ESCALATE", f"Would be {as_valid} (value {value}, credibility {cred}) if a genuine buyer; held because another "
                        "agency is asking for pricing and deliverables, possibly for a client, so a human should decide what to share.",
                        0.7, ["competitor", "possible_partner"], review=True, path=["competitor_scored"])
        return gate("REJECT", f"Another agency or consultant asking for pricing and deliverables, and not worth having even as a buyer ({why}).",
                    0.8, ["competitor"], review=True, path=["competitor"])

    # Confidence here is credibility-derived: it says how much of the row can be trusted.
    conf = credibility_confidence(cred)
    path = ["scored"]

    # 9. Adjustments that keep a lead out of the sales queue without escalating it.
    if decision == "QUALIFY" and budget is None and "placeholder_budget" in issues:
        decision, track = "NURTURE", "readiness"
        why = f"the budget field looks like a placeholder ({rec['raw'].get('monthly_budget_usd')}); confirm the real figure before a sales call"
    if decision == "QUALIFY" and ex.get("contact_restriction"):
        decision, track = "NURTURE", "readiness"
        why = "fits and is buying, but asked not to be contacted by sales yet; honour that and send what they asked for"
        flags.append("honour_no_contact")

    blockers: dict[str, str] = {}
    if decision == "ESCALATE":
        detail = f" ({drivers(c_parts, negative_only=True)})" if cred < t["credibility_trusted"] else ""
        blockers["label_escalate"] = f"{why}: value {value}, credibility {cred}{detail}"

    # 10. Blockers: would the missing/conflicting field change the label, on a lead worth having?
    if conflict_unresolved:
        blockers["budget_conflict"] = conflict_unresolved
    elif conflict:
        conf -= 0.15
        why += f" (budget assumed ${budget:,.0f} from conflicting fields; confirm on the first call)"
    high_value = value >= t["value_escalate"] or (budget is not None and budget >= cfg["budget"]["high_value_budget"])
    if issues & CONTACT_MISSING:
        if high_value:
            blockers["no_contact_email"] = "strong fit but no usable contact email; a human should try to recover contact details"
        elif decision != "REJECT":
            decision, why, conf, track = "REJECT", f"no usable contact email and value {value} is too low to chase", 0.6, None
    if "missing_message" in issues and high_value:
        blockers["no_stated_need"] = "company and budget look real but the message is empty, so need and readiness are unknown"
    if decision == "QUALIFY" and "lookalike_domain" in issues:
        blockers["lookalike_domain"] = (f"email domain {f.get('email_domain')} and website {f.get('website_domain')} are near-identical "
                                        "but different (a typo or impersonation); verify before outreach")
    if decision == "QUALIFY" and ex.get("budget_is_all_in") and budget:
        share = cfg["budget"]["all_in_fee_share_min"]
        if budget * share < cfg["budget"]["floor"]:
            blockers["all_in_budget"] = (f"${budget:,.0f} is an all-in figure including media spend, so the agency-fee portion is unknown "
                                         f"and may be under the ${cfg['budget']['floor']:,.0f} floor; confirm it")
    if decision == "QUALIFY" and screen_failed:
        blockers["screen_failed"] = "the model injection screen failed for this row, so it cannot be qualified automatically"

    if blockers:
        decision, conf, track = "ESCALATE", 0.8, None
        path.append("blocked")

    # 11. Injection: never QUALIFY; bad faith otherwise rejects, all go to the review queue.
    review = False
    if injection and not allowed:
        review = True
        path.append("injection_override")
        if decision in ("QUALIFY", "ESCALATE"):
            decision, conf = "ESCALATE", 0.8
            blockers["injection_on_valuable_lead"] = "text in the lead tries to instruct an automated agent"
        else:
            decision, conf, track = "REJECT", 0.8, None
            why = (f"rejected as bad faith and queued for review: text in the lead tries to instruct an automated agent "
                   f"(on its merits alone it would have been {as_captured}, {why})")

    scores["readiness"] = stage
    if decision == "QUALIFY":
        if budget is None:
            flags.append("budget_unconfirmed")
    if decision in ("QUALIFY", "NURTURE") and track:
        scores["track"] = track
        flags.append(f"track:{track}")
    if decision == "ESCALATE":
        reason = "Escalate: " + "; ".join(blockers.values()) + "."
        flags += [f"blocks:{code}" for code in blockers]
    else:
        reason = why[0].upper() + why[1:]
        if decision == "QUALIFY":
            reason += f"; value {value} ({drivers(v_parts)})"
        neg = drivers(c_parts, negative_only=True)
        reason += f"; credibility {cred} ({neg})." if neg else "."
    return _result(decision, reason, conf, flags, review=review, path=path, scores=scores)
