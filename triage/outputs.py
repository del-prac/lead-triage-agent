"""Writing a run's outputs, and re-scoring a finished run without any model calls.

The model steps (screen, extract) produce the facts; everything after them is deterministic. So a change
to rules.json never needs the models again: rescore() reloads the stored records and re-decides them.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from .escalations import build as build_escalations
from .policy import decide


def safe_cell(value):
    """Neutralise spreadsheet formulas in exported text so opening the CSV cannot execute anything."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def evaluate(records: list[dict], cfg: dict) -> dict[str, dict]:
    """Decide every record under cfg, primaries before their duplicates."""
    decided: dict[str, dict] = {}
    for rec in sorted(records, key=lambda r: bool(r["links"].get("duplicate_of"))):
        decided[rec["lead_id"]] = decide(rec, cfg, decided)
    return decided


def load_records(out_dir: Path, input_csv: Path | None = None) -> list[dict]:
    """Rebuild the per-row records a run produced, joined to the raw CSV rows it read."""
    detail = json.loads((out_dir / "decisions.json").read_text())
    if input_csv is None:
        start = json.loads((out_dir / "run_log.jsonl").read_text().splitlines()[0])
        input_csv = Path(start["input"])
        if not input_csv.is_absolute():
            input_csv = Path(__file__).resolve().parent.parent / input_csv
    raw = {r["lead_id"]: r for r in csv.DictReader(io.StringIO(input_csv.read_text(encoding="utf-8-sig"), newline=""))}
    return [{"lead_id": d["lead_id"], "raw": raw[d["lead_id"]], "facts": d["facts"], "issues": d["issues"],
             "screen": d["screen"], "extract": d["extract"], "links": d["links"]} for d in detail]


def write_outputs(records: list[dict], decided: dict[str, dict], cfg: dict, out_dir: Path) -> list[dict]:
    """decisions.csv, decisions.json, escalations.md/.json and sales_queue.csv. Returns the decisions.json rows."""
    with (out_dir / "decisions.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["lead_id", "decision", "track", "reason", "confidence", "value_score",
                    "credibility_score", "readiness", "flags", "review_queue"])
        for rec in records:
            d = decided[rec["lead_id"]]
            s = d.get("scores") or {}
            w.writerow([safe_cell(c) for c in (
                rec["lead_id"], d["decision"], s.get("track", ""), d["reason"],
                d["confidence"], s.get("value", ""), s.get("credibility", ""), s.get("readiness", ""),
                ";".join(d["flags"]), d["review_queue"])])

    detail = [{"lead_id": r["lead_id"], "facts": r["facts"], "issues": r["issues"], "screen": r["screen"],
               "extract": r["extract"], "links": r["links"], **decided[r["lead_id"]]} for r in records]
    (out_dir / "decisions.json").write_text(json.dumps(detail, indent=2, ensure_ascii=False))

    build_escalations(records, decided, cfg, out_dir)

    # Sales queue: every QUALIFY routed separately, highest value first (duplicates ride with their primary).
    queue = [(rec, decided[rec["lead_id"]]) for rec in records
             if decided[rec["lead_id"]]["decision"] == "QUALIFY" and "do_not_route_separately" not in decided[rec["lead_id"]]["flags"]]
    queue.sort(key=lambda rd: -(rd[1]["scores"].get("value") or 0))
    with (out_dir / "sales_queue.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "lead_id", "track", "value_score", "company", "budget", "notes"])
        for i, (rec, d) in enumerate(queue, 1):
            notes = ";".join(fl for fl in d["flags"] if fl.startswith(("budget_conflict", "budget_unconfirmed", "has_followups", "regulated")))
            w.writerow([safe_cell(c) for c in (
                i, rec["lead_id"], d["scores"].get("track"), d["scores"].get("value"),
                rec["facts"]["company"] or "", rec["facts"]["budget"] if rec["facts"]["budget"] is not None else "", notes)])
    return detail


def rescore(out_dir: Path, cfg: dict, records: list[dict] | None = None, input_csv: Path | None = None) -> dict:
    """Re-decide a finished run under cfg and rewrite its outputs. No model calls. Logged to run_log.jsonl."""
    records = records if records is not None else load_records(out_dir, input_csv)
    decided = evaluate(records, cfg)
    write_outputs(records, decided, cfg, out_dir)
    counts: dict[str, int] = {}
    for d in decided.values():
        counts[d["decision"]] = counts.get(d["decision"], 0) + 1
    with (out_dir / "run_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "step": "rescore",
                             "note": "re-decided from stored extractions under the current rules; no model calls",
                             "counts": counts}) + "\n")
    return counts
