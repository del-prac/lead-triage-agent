"""Same-person dedupe and same-account grouping. Two different concepts, kept apart.

- Same person (email, or name+company when email is missing): a repeat submission.
  Same ask -> duplicate (merged, inherits the primary's decision).
  Different ask -> resubmission_new_ask (decided on its own merits, flagged).
- Same account (shared company/website domain, different people): each person is
  decided individually; the rows are only cross-referenced for sales.
"""
from __future__ import annotations

import re

SUFFIXES = re.compile(r"\b(inc|llc|ltd|corp|co|group|gmbh|sa|plc)\.?$", re.I)


def _norm_company(name: str | None) -> str:
    return SUFFIXES.sub("", (name or "").lower()).strip(" ,.")


def _person_key(rec: dict) -> str | None:
    f = rec["facts"]
    if f["email"]:
        return f"email:{f['email']}"
    if f["name"] and f["company"]:
        return f"name:{f['name'].lower()}|{_norm_company(f['company'])}"
    return None


def _account_key(rec: dict) -> str | None:
    f = rec["facts"]
    if f["website_domain"]:
        return f["website_domain"]
    if f.get("email_kind") == "company":
        return f["email_domain"]
    return None


def _same_ask(later: dict | None, first: dict | None) -> bool:
    """Same request type AND either overlapping services or an explicit follow-up of the earlier note.
    Two separate projects from one person (SEO, then paid media) stay separate."""
    if not later or not first or later["request_type"] != first["request_type"]:
        return False
    if later.get("follows_up_previous"):
        return True
    a, b = set(later.get("services") or []), set(first.get("services") or [])
    return bool(a & b) or (not a and not b)


def link_records(records: list[dict]) -> None:
    """Annotate records in place with 'links' (duplicate_of, resubmission_of, same_account_as)."""
    by_person: dict[str, dict] = {}
    by_account: dict[str, list[dict]] = {}
    for rec in records:
        rec["links"] = {}
        pkey = _person_key(rec)
        if pkey and pkey in by_person:
            first = by_person[pkey]
            same_ask = _same_ask(rec.get("extract"), first.get("extract"))
            if same_ask:
                rec["links"]["duplicate_of"] = first["lead_id"]
                first["links"].setdefault("followups", []).append(rec["lead_id"])
            else:
                rec["links"]["resubmission_of"] = first["lead_id"]
        elif pkey:
            by_person[pkey] = rec
        akey = _account_key(rec)
        if akey:
            by_account.setdefault(akey, []).append(rec)

    for group in by_account.values():
        people = {_person_key(r) for r in group}
        if len(people) > 1:
            for r in group:
                others = [o["lead_id"] for o in group if o is not r and _person_key(o) != _person_key(r)]
                if others:
                    r["links"]["same_account_as"] = others
