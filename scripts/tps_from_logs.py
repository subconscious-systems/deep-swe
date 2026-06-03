#!/usr/bin/env python3
"""Compute objective tok/s from a job's per-call timing logs.

Reads the llm_timing.jsonl files emitted by scripts/sitecustomize.py (one line
per LLM call: latency_s, completion_tokens, tok_s). Reports the true realized
decode-inclusive tok/s distribution across the whole job and per trial, so you
can size the step budget:

    steps_affordable ~= agent_timeout_sec / (avg_output_tokens_per_step / tok_s
                                             + avg_bash_overhead_sec)

Falls back to a coarse estimate from result.json (output_tokens / wall_clock)
when no timing logs exist — that number is a *floor* (folds in bash exec,
retry backoff, and concurrency contention), useful only as a lower bound.

Usage:
    python3 scripts/tps_from_logs.py jobs/<job-dir>
"""
from __future__ import annotations

import glob
import json
import os
import statistics as st
import sys


def pctl(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def from_timing_logs(job: str):
    calls = []
    for f in glob.glob(os.path.join(job, "*", "agent", "llm_timing.jsonl")):
        for line in open(f, errors="ignore"):
            line = line.strip()
            if not line:
                continue
            try:
                calls.append(json.loads(line))
            except Exception:
                pass
    return calls


def from_result_json(job: str):
    """Fallback: output_tokens / wall_clock per trial (contaminated lower bound)."""
    from datetime import datetime

    def p(t):
        return datetime.fromisoformat(t.replace("Z", "+00:00")) if t else None

    rows = []
    for f in glob.glob(os.path.join(job, "*", "result.json")):
        r = json.load(open(f))
        ar = r.get("agent_result") or {}
        s, e = p(r.get("started_at")), p(r.get("finished_at"))
        out = ar.get("n_output_tokens")
        if s and e and out:
            rows.append(out / (e - s).total_seconds())
    return rows


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: tps_from_logs.py jobs/<job-dir>", file=sys.stderr)
        return 2
    job = sys.argv[1].rstrip("/")

    calls = from_timing_logs(job)
    if calls:
        ok = [c for c in calls if c.get("ok")]
        tps = [c["tok_s"] for c in ok]
        lat = [c["latency_s"] for c in ok]
        comp = [c["completion_tokens"] for c in ok if c.get("completion_tokens")]
        print(f"OBJECTIVE per-call tok/s  (from {len(calls)} logged calls, {len(ok)} ok)")
        print(f"  tok/s     : p50 {pctl(tps,50):.1f}  p90 {pctl(tps,90):.1f}  "
              f"min {min(tps):.1f}  max {max(tps):.1f}")
        print(f"  latency_s : p50 {pctl(lat,50):.1f}  p90 {pctl(lat,90):.1f}")
        print(f"  out toks  : p50 {pctl(comp,50):.0f}  p90 {pctl(comp,90):.0f}")
        print(f"  errors    : {len(calls) - len(ok)} failed calls")
        tp = pctl(tps, 50)
        ot = pctl(comp, 50)
        if tp and ot:
            print(f"\n  => median step gen time ~= {ot/tp:.1f}s ({ot:.0f} tok / {tp:.1f} tok/s)")
            print(f"     in a 5400s (90min) budget, decode alone caps ~{int(5400/(ot/tp))} steps")
        return 0

    print("No llm_timing.jsonl found (instrumentation not enabled for this job).")
    print("Falling back to result.json wall-clock estimate (LOWER BOUND only):\n")
    rows = from_result_json(job)
    if not rows:
        print("  no result.json timing available either.")
        return 1
    print(f"  effective tok/s (output/wallclock, contaminated): "
          f"p50 {pctl(rows,50):.1f}  p90 {pctl(rows,90):.1f}  "
          f"min {min(rows):.1f}  max {max(rows):.1f}  (n={len(rows)})")
    print("  ^ includes bash exec + retry backoff + concurrency contention.")
    print("  Enable scripts/sitecustomize.py and re-run for the true per-call rate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
