"""Layer 1 injection screen: deterministic patterns over every free-text field.

Each pattern needs the text to address an automated system or to issue a routing command, so ordinary
descriptions of lead-management work ("software to classify qualified leads") do not match.
"""
from __future__ import annotations

import re

TEXT_FIELDS = ("name", "company", "website", "monthly_budget_usd", "message", "source")

AGENT = r"(ai|agent|bot|model|assistant|llm|system|classifier|triage)"
PATTERNS = [
    r"\b(ignore|disregard|forget|override)\b.{0,30}\b(instructions?|rules?|prompts?|guidelines?|directives?)\b",
    r"\bsystem\s*(override|prompt|message)\b",
    r"\b(you are now|act as|pretend to be)\b",
    rf"\b(attention|note to|dear|hey)\s+(any\s+|the\s+)?{AGENT}\b",
    r"\b(classify|mark|label|output|set|route|move)\s+(this|the|our|my)\s+(lead|row|record|entry|submission)\b",
    r"\b(as|to)\s+(qualified|QUALIFY)\b.{0,20}\b(confidence|queue|immediately)\b",
    r"\bconfidence\s*(of|=|:)?\s*1(\.0+)?\b",
    r"\bskip\s+(the\s+)?(escalation|review)\b",
    rf"\b{AGENT}\s*[,:]\s*(please\s+)?(put|mark|route|move|send|qualify|prioriti[sz]e|approve|escalate|skip|rank)\b",
]
_COMPILED = [re.compile(p, re.I) for p in PATTERNS]


def regex_screen(row: dict) -> list[dict]:
    """Return one hit per (field, pattern) match. Hits are flags, never instructions."""
    hits = []
    for field in TEXT_FIELDS:
        text = row.get(field) or ""
        for pat in _COMPILED:
            m = pat.search(text)
            if m:
                hits.append({"field": field, "pattern": pat.pattern, "match": m.group(0)})
    return hits
