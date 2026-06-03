#!/usr/bin/env python3
"""Re-price a finished job under any pricing curve — no rerun needed.

The runs record token counts per LLM call (llm_timing.jsonl, written by
scripts/sitecustomize.py), so cost is pure arithmetic after the fact. The
cost_usd baked into result.json/trajectories reflects whatever curve was
registered at run time; this script recomputes from tokens under a different
curve, e.g. to compare pricing scenarios or correct a wrong registry entry.

    cost = (prompt - cached) * input_cost_per_token
         + cached            * cache_read_input_token_cost (falls back to input)
         + completion        * output_cost_per_token

Usage:
    python3 scripts/reprice.py jobs/<job-dir> pricing/<curve>.json
    python3 scripts/reprice.py jobs/<job-dir> pricing/a.json pricing/b.json   # compare

Model entries are matched by exact slug, then by suffix after the provider
prefix (subconscious/tim-qwen3.6-27b -> tim-qwen3.6-27b). Calls with no
matching entry are reported, not silently skipped.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict


def load_calls(job: str):
    calls = []
    for f in glob.glob(os.path.join(job, "*", "agent", "llm_timing.jsonl")):
        trial = os.path.basename(os.path.dirname(os.path.dirname(f)))
        for line in open(f, errors="ignore"):
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except Exception:
                continue
            c["trial"] = trial
            calls.append(c)
    return calls


def price_for(model: str, curve: dict) -> dict | None:
    if model in curve:
        return curve[model]
    if model and "/" in model:
        return curve.get(model.split("/")[-1])
    return None


def call_cost(c: dict, entry: dict) -> float:
    prompt = c.get("prompt_tokens") or 0
    cached = c.get("cached_tokens") or 0
    comp = c.get("completion_tokens") or 0
    in_cost = entry.get("input_cost_per_token") or 0.0
    cache_cost = entry.get("cache_read_input_token_cost", in_cost) or 0.0
    out_cost = entry.get("output_cost_per_token") or 0.0
    return (prompt - cached) * in_cost + cached * cache_cost + comp * out_cost


def reprice(job: str, curve_path: str) -> int:
    curve = json.load(open(curve_path))
    calls = load_calls(job)
    if not calls:
        print(f"no llm_timing.jsonl under {job} (timing probe not enabled for this job)")
        return 1

    by_trial: dict[str, float] = defaultdict(float)
    by_model: dict[str, float] = defaultdict(float)
    toks = defaultdict(int)
    unmatched: dict[str, int] = defaultdict(int)
    for c in calls:
        model = c.get("model") or "?"
        entry = price_for(model, curve)
        if entry is None:
            unmatched[model] += 1
            continue
        cost = call_cost(c, entry)
        by_trial[c["trial"]] += cost
        by_model[model] += cost
        toks["prompt"] += c.get("prompt_tokens") or 0
        toks["cached"] += c.get("cached_tokens") or 0
        toks["completion"] += c.get("completion_tokens") or 0

    print(f"=== {curve_path}  (job: {job}, {len(calls)} calls) ===")
    for model, cost in sorted(by_model.items()):
        print(f"  {model}: ${cost:.4f}")
    print(f"  tokens: {toks['prompt']:,} prompt ({toks['cached']:,} cached), "
          f"{toks['completion']:,} completion")
    print(f"  TOTAL: ${sum(by_model.values()):.4f}  "
          f"(mean/trial ${sum(by_trial.values())/max(1,len(by_trial)):.4f}, "
          f"max trial ${max(by_trial.values(), default=0):.4f}, n={len(by_trial)})")
    for model, n in unmatched.items():
        print(f"  WARNING: {n} calls for {model!r} have no entry in {curve_path} (not counted)")
    return 0


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: reprice.py jobs/<job-dir> pricing/<curve>.json [more-curves...]",
              file=sys.stderr)
        return 2
    job = sys.argv[1].rstrip("/")
    rc = 0
    for curve_path in sys.argv[2:]:
        rc = max(rc, reprice(job, curve_path))
        print()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
