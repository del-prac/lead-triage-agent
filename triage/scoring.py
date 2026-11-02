"""Observable scoring. Every point comes from rules.json['scoring'] and is returned as a breakdown.

value       -- what the lead is worth if it is real (0-100)
credibility -- how far the row can be trusted (0-100)
label() turns budget, readiness and the two scores into a decision and a track; QUALIFY leads are worked in
descending value order; confidence on a scored decision is derived from credibility alone (how much of the row can be trusted).
"""
from __future__ import annotations


def _clamp(x: float) -> int:
    return int(max(0, min(100, round(x))))


def budget_points(budget: float | None, v: dict) -> tuple[int, str]:
    if budget is None:
        return v["budget_unknown"], "budget unknown"
    if budget <= 0:
        return v["budget_zero"], "$0 budget"
    for floor, pts in v["budget_bands"]:
        if budget >= floor:
            return pts, f"${budget:,.0f}/mo budget"
    return 0, f"${budget:,.0f}/mo budget"


def value_score(f: dict, ex: dict, cfg: dict, budget: float | None) -> tuple[int, list[dict]]:
    v = cfg["scoring"]["value"]
    parts = []

    def add(signal, pts):
        if pts:
            parts.append({"signal": signal, "points": pts})

    add(*reversed(budget_points(budget, v)))
    add(f"{ex['company_scale'].replace('_', '-')} scale", v["scale"].get(ex["company_scale"], 0))
    if ex["services"]:
        add("services: " + ", ".join(ex["services"]), min(v["per_service"] * len(ex["services"]), v["services_cap"]))
    add(f"industry {ex['industry_category']}", v["industry"].get(ex["industry_category"], 0))
    add(f"timeline {ex['timeline']}", v["timeline"].get(ex["timeline"], 0))
    if ex["budget_expected_to_grow"]:
        add("budget expected to grow", v["budget_expected_to_grow"])
    add(f"source {f['source']}", v["source"].get(f["source"] or "", 0))
    return _clamp(sum(p["points"] for p in parts)), parts


def credibility_score(f: dict, ex: dict, issues: set[str], links: dict, cfg: dict,
                      known_relationship: bool, budget_conflict: bool) -> tuple[int, list[dict]]:
    c = cfg["scoring"]["credibility"]
    parts = []

    def add(signal, pts):
        if pts:
            parts.append({"signal": signal, "points": pts})

    if f.get("email_kind"):
        add(f"{f['email_kind']} email", c["email_kind"].get(f["email_kind"], 0))
    for code in ("email_website_mismatch", "lookalike_domain", "malformed_email", "missing_company", "missing_website",
                 "placeholder_budget", "unparseable_budget", "malformed_timestamp", "missing_message"):
        if code in issues:
            add(code.replace("_", " "), c[code])
    if ex["identity_withheld"]:
        add("identity withheld", c["identity_withheld"])
    if budget_conflict:
        add("budget contradicts other fields", c["budget_conflict"])
    if links.get("resubmission_of"):
        add("resubmission with a different ask", c["resubmission_new_ask"])
    add(f"source {f['source']}", c["source"].get(f["source"] or "", 0))
    if known_relationship:
        add("known relationship (allow list)", c["known_relationship"])
    return _clamp(c["start"] + sum(p["points"] for p in parts)), parts


def label(budget: float | None, stage: str, grows: bool, value: int, cred: int, cfg: dict) -> tuple[str, str, str | None]:
    """Label = fit x readiness; credibility gates; value only matters for escalation and for ordering.

    stage is ready_now | evaluating | exploring | future | undetermined. Returns (label, why, track).
    QUALIFY tracks: 'sell_now' (ready to sign) or 'process' (vendor selection, RFP, discovery).
    NURTURE tracks: 'readiness' (meets the bar, not ready) or 'growth' (below the floor today).
    """
    t = cfg["scoring"]["thresholds"]
    floor, growth_min = cfg["budget"]["floor"], cfg["budget"]["growth_nurture_min"]
    if value >= t["value_escalate"] and cred < t["credibility_trusted"]:
        return "ESCALATE", "high value but low credibility", None
    if cred < t["credibility_floor"]:
        return "REJECT", "too little of the lead can be trusted", None
    if budget is not None and budget <= 0:
        return "REJECT", "no budget", None
    if budget is not None and budget < floor:
        if budget >= growth_min or grows or stage == "future":
            return "NURTURE", f"credible buyer below the ${floor:,.0f} floor today; nurture toward it", "growth"
        return "REJECT", f"budget far below the ${floor:,.0f} floor with no sign of growth", None
    if stage == "undetermined":
        if value >= t["value_escalate"]:
            return "ESCALATE", "readiness and need undetermined on a lead worth having", None
        return "NURTURE", "meets the bar but readiness is undetermined", "readiness"
    if stage in ("ready_now", "evaluating"):
        track = "sell_now" if stage == "ready_now" else "process"
        how = "ready to buy now" if stage == "ready_now" else "actively selecting a vendor (sales process)"
        if budget is None:
            return "QUALIFY", f"{how}; budget unconfirmed", track
        return "QUALIFY", f"meets the ${floor:,.0f} floor and is {how}", track
    return "NURTURE", f"meets the bar but is {stage}, not ready to buy", "readiness"


def credibility_confidence(cred: int) -> float:
    """Confidence in a scored label tracks how much of the row can be trusted: 0.45 + 0.5 x credibility."""
    return 0.45 + 0.5 * cred / 100


def drivers(parts: list[dict], n: int = 3, negative_only: bool = False) -> str:
    chosen = [p for p in parts if p["points"] < 0] if negative_only else parts
    chosen = sorted(chosen, key=lambda p: -abs(p["points"]))[:n]
    return ", ".join(f"{p['signal']} {p['points']:+d}" for p in chosen)
