"""Local review UI: python -m triage.ui [--out out/] [--port 8012]

Loads a finished run (decisions.json + the run's input CSV) and serves one page with three tabs:
Leads (filter, rationale), Review (escalations and the bad-faith batch list) and Tune (edit weights and
see which decisions change). Every number shown is computed by the same policy code the agent runs,
on the run's cached model outputs, so the UI makes no model calls and cannot drift from the agent.
Binds to 127.0.0.1 only. Lead text is untrusted and the page renders it as text, never as HTML.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .escalations import _cases, _review_entry
from .outputs import evaluate, load_records, rescore  # noqa: F401  (load_records/evaluate re-exported for tests)

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "ui_page.html"
EDITABLE = ("scoring", "budget")


def summarise(rec: dict, d: dict) -> dict:
    s = d.get("scores") or {}
    ex = rec.get("extract") or {}
    return {"lead_id": rec["lead_id"], "decision": d["decision"], "track": s.get("track"),
            "value": s.get("value"), "credibility": s.get("credibility"), "confidence": d["confidence"],
            "reason": d["reason"], "flags": d["flags"], "review_queue": d["review_queue"],
            "value_breakdown": s.get("value_breakdown", []), "credibility_breakdown": s.get("credibility_breakdown", []),
            "evidence": ex.get("evidence") or {}, "summary": ex.get("summary", ""), "screen": rec["screen"],
            "raw": rec["raw"]}


def simulate(records: list[dict], cfg: dict, edited: dict) -> dict:
    """Re-decide everything with edited scoring/budget sections; report what changed against the saved rules."""
    trial = copy.deepcopy(cfg)
    for key in EDITABLE:
        if key in edited:
            trial[key] = edited[key]
    base, new = evaluate(records, cfg), evaluate(records, trial)

    def label(d):
        s = d.get("scores") or {}
        return " ".join(x for x in (d["decision"], s.get("track")) if x)

    def score(d, key):
        return (d.get("scores") or {}).get(key)

    def queue(decided):
        q = [k for k, d in decided.items() if d["decision"] == "QUALIFY" and "do_not_route_separately" not in d["flags"]]
        return sorted(q, key=lambda k: -(score(decided[k], "value") or 0))

    counts = {k: {"before": 0, "after": 0} for k in ("QUALIFY", "NURTURE", "REJECT", "ESCALATE")}
    changed, scores = [], []
    for rec in records:
        lid = rec["lead_id"]
        b, n = base[lid], new[lid]
        counts[b["decision"]]["before"] += 1
        counts[n["decision"]]["after"] += 1
        if label(b) != label(n):
            changed.append({"lead_id": lid, "before": label(b), "after": label(n), "reason": n["reason"]})
        if (score(b, "value"), score(b, "credibility")) != (score(n, "value"), score(n, "credibility")):
            scores.append({"lead_id": lid, "value": [score(b, "value"), score(n, "value")],
                           "credibility": [score(b, "credibility"), score(n, "credibility")]})
    return {"counts": counts, "changed": changed, "scores": scores,
            "queue": {"before": queue(base), "after": queue(new)}}


def state(records: list[dict], cfg: dict) -> dict:
    decided = evaluate(records, cfg)
    leads, cards, review = [], [], []
    for rec in records:
        d = decided[rec["lead_id"]]
        leads.append(summarise(rec, d))
        if d["decision"] == "ESCALATE":
            cards.append({"lead_id": rec["lead_id"], "why": d["reason"], "cases": _cases(rec, d, cfg, decided)})
        elif d["review_queue"]:
            review.append(_review_entry(rec, d, cfg, decided))
    return {"leads": leads, "escalations": cards, "bad_faith": review,
            "rules": {k: cfg[k] for k in EDITABLE}, "known_actors": cfg["known_actors"]}


def make_handler(records: list[dict], rules_path: Path, out_dir: Path):
    def cfg() -> dict:
        return json.loads(rules_path.read_text())

    def commit(new_cfg: dict) -> dict:
        """Write the rules, then rewrite the run's outputs under them (deterministic; no model calls)."""
        rules_path.write_text(json.dumps(new_cfg, indent=2) + "\n")
        return rescore(out_dir, new_cfg, records)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

        def log_message(self, *args):  # keep the terminal quiet
            pass

        def do_GET(self):
            if self.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                self._json(state(records, cfg()))
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            current = cfg()
            if self.path == "/api/simulate":
                self._json(simulate(records, current, body))
            elif self.path == "/api/save-rules":
                for key in EDITABLE:
                    if key in body:
                        current[key] = body[key]
                self._json({"saved": str(rules_path), "outputs_rewritten": str(out_dir), "counts": commit(current)})
            elif self.path in ("/api/known-actor", "/api/known-actor-remove"):
                kind, entry = body.get("kind"), body.get("entry") or {}
                if kind not in ("deny", "allow") or not any(entry.values()):
                    return self._json({"error": "kind must be deny or allow, with an email or domain"}, 400)
                entries = current["known_actors"][kind]
                if self.path.endswith("remove"):
                    current["known_actors"][kind] = [e for e in entries if e != entry]
                elif entry not in entries:
                    entries.append(entry)
                self._json({"kind": kind, "entry": entry, "removed": self.path.endswith("remove"),
                            "counts": commit(current)})
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="triage.ui", description="Local review UI for a finished triage run.")
    ap.add_argument("--out", type=Path, default=ROOT / "out", help="a run's output directory")
    ap.add_argument("--input", type=Path, default=None, help="the run's input CSV (default: read from run_log.jsonl)")
    ap.add_argument("--rules", type=Path, default=ROOT / "rules.json")
    ap.add_argument("--port", type=int, default=8012)
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to bind (default localhost only; the UI has no login, so bind only to a private network)")
    args = ap.parse_args(argv)
    records = load_records(args.out, args.input)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(records, args.rules, args.out))
    print(f"Lead triage UI on http://{args.host}:{args.port}  ({len(records)} leads from {args.out}; Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
