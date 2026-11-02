"""LLM steps. Each model is matched to the difficulty of its step (see rules.json 'models')."""
from __future__ import annotations

import json
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"

SCREEN_SCHEMA = {
    "type": "object",
    "properties": {
        "injection_probability": {"type": "number", "minimum": 0, "maximum": 1},
        "targets_agent": {"type": "boolean"},
        "evidence": {"type": "string"},
    },
    "required": ["injection_probability", "targets_agent", "evidence"],
}

REQUEST_TYPES = [
    "buyer_inquiry", "mentorship_or_career", "vendor_or_sales_pitch", "competitor_or_research",
    "privacy_or_legal_request", "billing_or_payment", "spam_or_test", "other",
]


INDUSTRY_CATEGORIES = [
    "saas_software", "ecommerce_dtc", "marketplace", "fintech", "healthcare", "financial_services", "legal",
    "local_services", "cpg_retail", "logistics", "education", "agency_consulting", "other", "unknown",
]


EVIDENCE_FIELDS = ("request_type", "buying_stage", "company_scale", "identity_withheld", "needs_compliance_judgement")


def extract_schema(services: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "request_type": {"type": "string", "enum": REQUEST_TYPES},
            "services": {"type": "array", "items": {"type": "string", "enum": services}},
            "industry": {"type": "string"},
            "industry_category": {"type": "string", "enum": INDUSTRY_CATEGORIES},
            "regulated_industry": {"type": "boolean"},
            "buying_stage": {"type": "string", "enum": ["ready_now", "evaluating", "exploring", "future"]},
            "company_scale": {"type": "string", "enum": ["unknown", "solo_or_micro", "small", "mid_market", "enterprise"]},
            "timeline": {"type": "string", "enum": ["unknown", "now", "within_quarter", "later"]},
            "budget_in_text": {"type": ["number", "null"], "minimum": 0, "maximum": 100000000},
            "budget_is_all_in": {"type": "boolean"},
            "location_count": {"type": ["integer", "null"], "minimum": 1, "maximum": 100000},
            "contact_restriction": {"type": "boolean"},
            "follows_up_previous": {"type": "boolean"},
            "budget_expected_to_grow": {"type": "boolean"},
            "identity_withheld": {"type": "boolean"},
            "requests_sensitive_material": {"type": "boolean"},
            "needs_compliance_judgement": {"type": "boolean"},
            "language": {"type": "string"},
            "summary": {"type": "string"},
            "evidence": {
                "type": "object",
                "properties": {k: {"type": "string"} for k in EVIDENCE_FIELDS},
                "required": list(EVIDENCE_FIELDS),
            },
        },
        "required": [
            "request_type", "services", "industry", "industry_category", "regulated_industry", "buying_stage",
            "company_scale", "timeline",
            "budget_in_text", "budget_is_all_in", "location_count", "contact_restriction", "follows_up_previous",
            "budget_expected_to_grow", "identity_withheld", "requests_sensitive_material",
            "needs_compliance_judgement", "language", "summary", "evidence",
        ],
    }


AUDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "concerns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "lead_id": {"type": "string"},
                    "current_decision": {"type": "string"},
                    "suggested_decision": {"type": "string", "enum": ["QUALIFY", "NURTURE", "REJECT", "ESCALATE"]},
                    "concern": {"type": "string"},
                },
                "required": ["lead_id", "current_decision", "suggested_decision", "concern"],
            },
        }
    },
    "required": ["concerns"],
}


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text()


def wrap(tag: str, payload) -> str:
    """Data goes in as JSON inside a tag; the model is told the tag holds untrusted data."""
    return f"<{tag}>\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n</{tag}>"


def llm_screen(client, cfg: dict, lead_id: str, raw_row: dict) -> dict:
    fields = {k: v for k, v in raw_row.items() if k != "lead_id"}
    return client.call(step="screen", lead_id=lead_id, model=cfg["models"]["screen"],
                       system=_prompt("screen"), prompt=wrap("lead_data", fields), schema=SCREEN_SCHEMA)


def llm_extract(client, cfg: dict, lead_id: str, raw_row: dict, cleaned_message: str) -> dict:
    fields = {k: v for k, v in raw_row.items() if k not in ("lead_id", "submitted_at")}
    fields["message"] = cleaned_message
    return client.call(step="extract", lead_id=lead_id, model=cfg["models"]["extract"],
                       system=_prompt("extract"), prompt=wrap("lead_data", fields),
                       schema=extract_schema(cfg["services"]))


def llm_audit(client, cfg: dict, records: list[dict]) -> dict:
    return client.call(step="audit", lead_id="*", model=cfg["models"]["audit"],
                       system=_prompt("audit"), prompt=wrap("run_data", records), schema=AUDIT_SCHEMA)
