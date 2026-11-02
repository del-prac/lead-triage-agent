"""LLM backends with schema validation, bounded retry and an on-disk cache.

Backends:
  cli  -- `claude -p` with all tools disabled and no project context (dev loop).
  api  -- Anthropic Messages API, schema enforced through a forced tool call.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

MAX_ATTEMPTS = 2


class LLMFailure(Exception):
    pass


def validate(schema: dict, value, path: str = "$") -> list[str]:
    """Minimal JSON-schema check: type, enum, required, properties, items, min/max."""
    errors: list[str] = []
    types = schema.get("type")
    types = types if isinstance(types, list) else [types] if types else []
    py = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
    ok = not types
    for t in types:
        if t == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            ok = True
        elif t == "integer" and isinstance(value, int) and not isinstance(value, bool):
            ok = True
        elif t in py and isinstance(value, py[t]):
            ok = True
    if not ok:
        return [f"{path}: expected {types}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return [f"{path}: not a finite number"]
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: missing")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                errors += validate(sub, value[key], f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(schema["items"], item, f"{path}[{i}]")
    return errors


def api_schema(schema):
    """Adapt a schema for structured outputs: every object gets additionalProperties false, numeric bounds
    are removed (unsupported there; validate() still enforces them), and type lists become anyOf."""
    if isinstance(schema, list):
        return [api_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: api_schema(v) for k, v in schema.items() if k not in ("minimum", "maximum")}
    if isinstance(out.get("type"), list):
        rest = {k: v for k, v in out.items() if k != "type"}
        return {"anyOf": [{"type": t, **(rest if t != "null" else {})} for t in out["type"]]}
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


class Client:
    def __init__(self, backend: str, cache_dir: Path | None, log, prices: dict | None = None):
        self.backend = backend
        self.prices = prices or {}
        self.cache_dir = cache_dir
        self.log = log
        self.cost_usd = 0.0
        self._lock = threading.Lock()          # guards cost_usd and the per-key lock table
        self._key_locks: dict[str, threading.Lock] = {}
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
        if backend == "api":
            import anthropic  # deferred: the cli backend needs no dependencies
            self._api = anthropic.Anthropic()

    def call(self, *, step: str, lead_id: str, model: str, system: str, prompt: str, schema: dict) -> dict:
        key = hashlib.sha256(json.dumps([model, system, prompt, schema], sort_keys=True).encode()).hexdigest()[:24]
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:  # one model call per identical payload; a second caller waits and reads the cache
            return self._call_locked(step, lead_id, model, system, prompt, schema, key)

    def _call_locked(self, step, lead_id, model, system, prompt, schema, key) -> dict:
        cached = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if cached and cached.exists():
            out = json.loads(cached.read_text())
            self.log(step=step, lead_id=lead_id, event="llm_cache_hit", model=model, cache_key=key, output=out)
            return out

        last_errors: list[str] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.time()
            try:
                out, meta = self._invoke(model, system, prompt, schema)
                errors = validate(schema, out)
            except Exception as exc:  # transport or parse failure counts as an attempt
                out, meta, errors = None, {}, [f"{type(exc).__name__}: {exc}"]
            self.log(step=step, lead_id=lead_id, event="llm_call", model=model, backend=self.backend,
                     attempt=attempt, cache_key=key, seconds=round(time.time() - started, 2),
                     prompt=prompt, output=out, validation_errors=errors, **meta)
            if not errors:
                if cached:  # write-then-rename so a reader never sees a partial file
                    tmp = cached.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
                    tmp.write_text(json.dumps(out, indent=2))
                    os.replace(tmp, cached)
                return out
            last_errors = errors
        raise LLMFailure(f"{step} failed validation after {MAX_ATTEMPTS} attempts: {last_errors}")

    def _invoke(self, model: str, system: str, prompt: str, schema: dict) -> tuple[dict, dict]:
        if self.backend == "cli":
            # Empty cwd + --safe-mode: no CLAUDE.md, hooks or plugins leak into the model's context.
            with tempfile.TemporaryDirectory() as empty:
                proc = subprocess.run(
                    ["claude", "-p", "--model", model, "--tools", "", "--safe-mode",
                     "--no-session-persistence", "--output-format", "json",
                     "--system-prompt", system, "--json-schema", json.dumps(schema)],
                    input=prompt, capture_output=True, text=True, timeout=300, cwd=empty,
                )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip()[:500] or f"exit {proc.returncode}")
            res = json.loads(proc.stdout)
            cost = res.get("total_cost_usd") or 0.0
            with self._lock:
                self.cost_usd += cost
            out = res.get("structured_output")
            if out is None:
                out = json.loads(res.get("result") or "null")
            return out, {"cost_usd": cost}
        # Structured outputs (output_config.format). Current models reject forced tool_choice, so a forced
        # tool call is not used. Thinking tokens count toward max_tokens, hence the headroom.
        resp = self._api.messages.create(
            model=model, max_tokens=16000, system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": api_schema(schema)}},
        )
        price_in, price_out = self.prices.get(model, (0.0, 0.0))
        cost = (resp.usage.input_tokens * price_in + resp.usage.output_tokens * price_out) / 1_000_000
        with self._lock:
            self.cost_usd += cost
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model refused: {getattr(resp.stop_details, 'category', None)}")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text), {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens,
                                  "cost_usd": round(cost, 6),
                                  "stop_reason": resp.stop_reason, "request_id": resp._request_id}
