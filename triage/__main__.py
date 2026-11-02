"""CLI: python -m triage --input fixtures/inbound_leads.csv --out out/

Pipeline per row: normalize -> regex screen -> LLM screen (Haiku) -> LLM extract (Sonnet)
then across rows: dedupe/grouping -> deterministic policy -> optional audit (Opus).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .dedupe import link_records
from .outputs import load_records, rescore, safe_cell, write_outputs  # noqa: F401  (safe_cell re-exported)
from .extract import llm_audit, llm_extract, llm_screen
from .llm import Client, LLMFailure
from .normalize import normalize_row
from .policy import decide
from .screen import regex_screen

ROOT = Path(__file__).resolve().parent.parent


class RunLog:
    def __init__(self, path: Path):
        self.fh = path.open("w", encoding="utf-8")
        self.lock = threading.Lock()

    def __call__(self, **event):
        event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
        with self.lock:
            self.fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            self.fh.flush()


def process_row(raw: dict, cfg: dict, client: Client, log: RunLog, use_llm: bool) -> dict:
    lead_id = raw["lead_id"]
    norm = normalize_row(raw, cfg)
    log(step="normalize", lead_id=lead_id, facts=norm["facts"], issues=norm["issues"])
    rec = {"lead_id": lead_id, "raw": raw, **norm, "screen": {"regex_hits": regex_screen(raw)}, "extract": None}
    log(step="regex_screen", lead_id=lead_id, hits=rec["screen"]["regex_hits"])
    if not use_llm:
        return rec
    try:
        rec["screen"]["llm"] = llm_screen(client, cfg, lead_id, raw)
    except LLMFailure as exc:
        rec["screen"]["llm"] = None
        log(step="screen", lead_id=lead_id, event="llm_failed", error=str(exc))
    try:
        rec["extract"] = llm_extract(client, cfg, lead_id, raw, norm["facts"]["message"])
    except LLMFailure as exc:
        log(step="extract", lead_id=lead_id, event="llm_failed", error=str(exc))
    return rec


def load_rows(data: bytes) -> tuple[list[dict], str | None]:
    """Parse and validate the input before any model call. Returns (rows, problem)."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))  # keeps quoted newlines intact
    if not reader.fieldnames or "lead_id" not in reader.fieldnames:
        return [], f"missing required column 'lead_id' (found {reader.fieldnames})"
    rows = list(reader)
    ids = [(r.get("lead_id") or "").strip() for r in rows]
    if "" in ids:
        return [], f"row {ids.index('') + 2} has no lead_id"
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        return [], f"duplicate lead_id values {dupes}; every row needs its own id"
    return rows, None


def load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines from a local, git-ignored .env into the environment (existing vars win)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="triage", description="First-pass triage of inbound website leads.")
    ap.add_argument("--input", type=Path, default=ROOT / "fixtures" / "inbound_leads.csv")
    ap.add_argument("--out", type=Path, default=ROOT / "out")
    ap.add_argument("--rules", type=Path, default=ROOT / "rules.json")
    ap.add_argument("--backend", choices=["cli", "api"], default="cli",
                    help="cli = `claude -p` with tools disabled; api = Anthropic SDK (needs ANTHROPIC_API_KEY)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--no-cache", action="store_true", help="ignore cached LLM outputs")
    ap.add_argument("--audit", action="store_true", help="run the advisory Opus audit over the final decisions")
    ap.add_argument("--rescore", action="store_true",
                    help="re-decide the run already in --out under the current rules; no model calls, no cache needed")
    args = ap.parse_args(argv)

    cfg = json.loads(args.rules.read_text())
    if args.rescore:
        counts = rescore(args.out, cfg)
        print(f"rescored {args.out} under {args.rules} (no model calls): {counts}")
        return 0
    if args.backend == "api":
        load_dotenv(ROOT / ".env")
    data = args.input.read_bytes()
    checksum = hashlib.sha256(data).hexdigest()
    try:
        rows, problem = load_rows(data)
    except UnicodeDecodeError as exc:
        rows, problem = [], f"input is not UTF-8 ({exc})"
    if problem:
        print(f"triage: cannot process {args.input}: {problem}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    log = RunLog(args.out / "run_log.jsonl")
    shown = args.input.resolve()
    shown = shown.relative_to(ROOT) if shown.is_relative_to(ROOT) else args.input  # no local paths in shared logs
    log(step="start", input=str(shown), sha256=checksum, rows=len(rows), backend=args.backend,
        models=cfg["models"], brief_version=cfg["brief_version"])
    client = Client(args.backend, None if args.no_cache else args.out / "cache", log, cfg.get("prices_per_mtok"))

    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        records = list(pool.map(lambda r: process_row(r, cfg, client, log, True), rows))

    link_records(records)
    for rec in records:
        if rec["links"]:
            log(step="dedupe", lead_id=rec["lead_id"], links=rec["links"])

    decided: dict[str, dict] = {}
    for rec in sorted(records, key=lambda r: bool(r["links"].get("duplicate_of"))):
        decided[rec["lead_id"]] = decide(rec, cfg, decided)
        log(step="decide", lead_id=rec["lead_id"], **decided[rec["lead_id"]])

    detail = write_outputs(records, decided, cfg, args.out)

    if args.audit:
        try:
            audit = llm_audit(client, cfg, detail)
            (args.out / "audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False))
        except LLMFailure as exc:
            log(step="audit", event="llm_failed", error=str(exc))

    counts: dict[str, int] = {}
    for d in decided.values():
        counts[d["decision"]] = counts.get(d["decision"], 0) + 1
    log(step="end", seconds=round(time.time() - started, 1), counts=counts, cost_usd=round(client.cost_usd, 4))
    print(f"sha256 {checksum}\nrows {len(rows)}  decisions {counts}  cost ${client.cost_usd:.4f}  -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
