"""Deterministic validation and normalization. Emits facts and issues; never decides."""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import unquote, urlparse

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")
URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
TAG_RE = re.compile(r"<[^>]+>")
# A cell starting with one of these is executed as a formula by spreadsheet software.
FORMULA_RE = re.compile(r"^\s*([=+@\t\r]|-[^\d\s])")
FORMULA_FIELDS = ("name", "email", "company", "website", "monthly_budget_usd", "message", "source")


def _registrable(domain: str) -> str:
    parts = domain.split(".")
    return parts[-2] if len(parts) >= 2 else domain


def is_lookalike(a: str, b: str) -> bool:
    """Two different domains whose names differ only by punctuation or a character or two."""
    x, y = _registrable(a), _registrable(b)
    if x == y:
        return False
    if x.replace("-", "") == y.replace("-", ""):
        return True
    return min(len(x), len(y)) >= 5 and SequenceMatcher(None, x, y).ratio() >= 0.85


def _issue(code: str, field: str, detail: str = "") -> dict:
    return {"code": code, "field": field, "detail": detail}


def domain_of_email(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def domain_of_url(url: str) -> str:
    host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
    return host.lower().removeprefix("www.")


def is_placeholder_domain(domain: str, cfg: dict) -> bool:
    labels = domain.split(".")
    return len(labels) >= 2 and labels[-2] in cfg["placeholder_labels"]


def parse_budget(raw: str, cfg: dict) -> tuple[float | None, list[dict]]:
    text = (raw or "").strip().lower().replace(",", "").replace("$", "").replace("usd", "").strip()
    if not text:
        return None, [_issue("missing_budget", "monthly_budget_usd")]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([km]?)", text)
    if not m:
        return None, [_issue("unparseable_budget", "monthly_budget_usd", raw)]
    value = float(m.group(1)) * {"": 1, "k": 1_000, "m": 1_000_000}[m.group(2)]
    issues = []
    if m.group(2):
        issues.append(_issue("budget_normalized", "monthly_budget_usd", f"{raw!r} -> {value:.0f}"))
    digits = m.group(1).split(".")[0]
    if value >= cfg["budget"]["placeholder_min"] or (len(digits) >= 4 and len(set(digits)) == 1):
        return None, issues + [_issue("placeholder_budget", "monthly_budget_usd", raw)]
    return value, issues


def clean_message(raw: str) -> tuple[str, list[dict]]:
    text = html.unescape(raw or "")
    stripped = TAG_RE.sub(" ", text)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    issues = []
    if stripped != (raw or "").strip():
        issues.append(_issue("markup_stripped", "message"))
    if not stripped:
        issues.append(_issue("missing_message", "message"))
    return stripped, issues


def normalize_row(row: dict, cfg: dict) -> dict:
    """Return {'facts': {...}, 'issues': [...]} for one raw CSV row."""
    issues: list[dict] = []
    facts: dict = {}

    ts = (row.get("submitted_at") or "").strip()
    try:
        when = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        facts["submitted_at"] = when.isoformat()
        # A submission cannot arrive after the run that reads it (one day of clock-skew allowed).
        if when > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1):
            issues.append(_issue("future_timestamp", "submitted_at", ts))
    except ValueError:
        facts["submitted_at"] = None
        issues.append(_issue("malformed_timestamp", "submitted_at", ts))

    for field in FORMULA_FIELDS:
        if FORMULA_RE.match(row.get(field) or ""):
            issues.append(_issue("formula_injection", field, (row.get(field) or "")[:60]))
            break

    email = (row.get("email") or "").strip().lower()
    facts["email"] = email or None
    facts["email_domain"] = domain_of_email(email) if email else None
    if not email:
        issues.append(_issue("missing_email", "email"))
    elif not EMAIL_RE.match(email):
        issues.append(_issue("malformed_email", "email", email))
    else:
        d = facts["email_domain"]
        facts["email_kind"] = (
            "disposable" if d in cfg["disposable_domains"]
            else "placeholder" if is_placeholder_domain(d, cfg)
            else "freemail" if d in cfg["freemail_domains"]
            else "education" if d.endswith(".edu")
            else "company"
        )
        if facts["email_kind"] in ("disposable", "placeholder"):
            issues.append(_issue(f"{facts['email_kind']}_email", "email", d))

    website = (row.get("website") or "").strip()
    facts["website_domain"] = domain_of_url(website) if website else None
    if website:
        parsed = urlparse(website)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            issues.append(_issue("malformed_website", "website", website))
        elif parsed.scheme == "http":
            issues.append(_issue("insecure_website", "website", website))
    else:
        issues.append(_issue("missing_website", "website"))

    company = (row.get("company") or "").strip()
    facts["company"] = company or None
    if not company:
        issues.append(_issue("missing_company", "company"))

    budget, b_issues = parse_budget(row.get("monthly_budget_usd", ""), cfg)
    facts["budget"] = budget
    issues += b_issues

    message, m_issues = clean_message(row.get("message", ""))
    facts["message"] = message
    issues += m_issues

    links = URL_IN_TEXT_RE.findall(row.get("message") or "")
    facts["message_links"] = links
    def executable(url: str) -> bool:
        # Decode and check every path segment, so trailing slashes, encoding and query strings do not hide it.
        segments = [s for s in unquote(urlparse(url).path).lower().split("/") if s]
        return any(s.endswith(tuple(cfg["executable_extensions"])) for s in segments)

    exe = [u for u in links if executable(u)]
    if exe:
        issues.append(_issue("executable_link", "message", exe[0]))

    if facts.get("email_kind") == "company" and facts["website_domain"]:
        if facts["email_domain"] != facts["website_domain"] and not facts["email_domain"].endswith("." + facts["website_domain"]):
            issues.append(_issue("email_website_mismatch", "email", f"{facts['email_domain']} vs {facts['website_domain']}"))
            if is_lookalike(facts["email_domain"], facts["website_domain"]):
                issues.append(_issue("lookalike_domain", "email", f"{facts['email_domain']} vs {facts['website_domain']}"))

    if facts["website_domain"] and is_placeholder_domain(facts["website_domain"], cfg):
        issues.append(_issue("placeholder_website", "website", facts["website_domain"]))

    facts["source"] = (row.get("source") or "").strip().lower() or None
    facts["name"] = (row.get("name") or "").strip() or None
    return {"facts": facts, "issues": issues}
