#!/usr/bin/env python3
"""Compute objective tok/s from a job's per-call timing logs.

Reads the llm_timing.jsonl files emitted by scripts/sitecustomize.py (one line
per LLM call: latency_s, completion_tokens, tok_s, finish_reason, errors).
Reports:

  1. The realized decode-inclusive tok/s distribution across the whole job,
     for sizing the step budget:
         steps_affordable ~= agent_timeout_sec / (avg_output_tokens_per_step
                                                  / tok_s + avg_bash_overhead_sec)
  2. A wall-clock timeline (tok/s, in-flight requests, timeouts, truncations
     per bucket) — shows whether speed degrades as the run ramps up.
  3. tok/s grouped by concurrent in-flight requests — the direct test for
     "request contention causes slower decode + timeouts".

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
from bisect import bisect_left, bisect_right
from datetime import datetime


def pctl(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def _ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def from_timing_logs(job: str):
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
            c["t0"], c["t1"] = _ts(c.get("start")), _ts(c.get("end"))
            calls.append(c)
    return calls


def is_timeout(c) -> bool:
    return "timeout" in (c.get("error") or "").lower()


def concurrency_segments(calls):
    """Step function of in-flight request count: list of (t_start, t_end, n)."""
    events = []
    for c in calls:
        if c["t0"] is not None and c["t1"] is not None and c["t1"] > c["t0"]:
            events += [(c["t0"], 1), (c["t1"], -1)]
    events.sort()
    segs, n, prev = [], 0, None
    for t, d in events:
        if prev is not None and t > prev and n > 0:
            segs.append((prev, t, n))
        n += d
        prev = t
    return segs


def mean_concurrency(c, segs, starts):
    """Time-weighted mean in-flight count over this call's span (incl. itself)."""
    t0, t1 = c["t0"], c["t1"]
    if t0 is None or t1 is None or t1 <= t0 or not segs:
        return None
    lo = max(0, bisect_right(starts, t0) - 1)
    hi = bisect_left(starts, t1)
    total = 0.0
    for s0, s1, n in segs[lo : hi + 1]:
        overlap = min(s1, t1) - max(s0, t0)
        if overlap > 0:
            total += overlap * n
    return total / (t1 - t0)


def print_timeline(calls, n_buckets=24):
    timed = [c for c in calls if c["t1"] is not None]
    if len(timed) < 2:
        return
    lo = min(c["t0"] or c["t1"] for c in timed)
    hi = max(c["t1"] for c in timed)
    span = hi - lo
    if span <= 0:
        return
    width = span / n_buckets
    buckets = [[] for _ in range(n_buckets)]
    for c in timed:
        i = min(n_buckets - 1, int((c["t1"] - lo) / width))
        buckets[i].append(c)
    segs = concurrency_segments(timed)
    starts = [s[0] for s in segs]
    print(f"\nTIMELINE  ({n_buckets} buckets x {width/60:.1f} min, by call end time)")
    print("  t+min   calls  inflight  tok/s p50   timeouts  trunc  errs")
    for i, b in enumerate(buckets):
        if not b:
            continue
        tps = pctl([c.get("tok_s") for c in b if c.get("ok")], 50)
        conc = [x for x in (mean_concurrency(c, segs, starts) for c in b) if x]
        tmo = sum(1 for c in b if is_timeout(c))
        trunc = sum(1 for c in b if c.get("finish_reason") == "length")
        errs = sum(1 for c in b if not c.get("ok")) - tmo
        tps_s = f"{tps:9.1f}" if tps is not None else f"{'--':>9}"
        conc_s = f"{st.mean(conc):8.1f}" if conc else f"{'--':>8}"
        print(f"  {i*width/60:6.0f}  {len(b):5d}  {conc_s}  {tps_s}"
              f"  {tmo:8d}  {trunc:5d}  {errs:4d}")


def print_contention(calls):
    """tok/s grouped by mean in-flight concurrency — the contention test."""
    timed = [c for c in calls if c["t0"] and c["t1"] and c["t1"] > c["t0"]]
    if len(timed) < 2:
        return
    segs = concurrency_segments(timed)
    starts = [s[0] for s in segs]
    by_level: dict[int, list] = {}
    for c in timed:
        m = mean_concurrency(c, segs, starts)
        if m is None:
            continue
        by_level.setdefault(int(round(m)), []).append(c)
    print("\nCONTENTION  (per-call tok/s vs concurrent in-flight requests)")
    print("  inflight  calls   tok/s p50   tok/s p90   latency p50   timeouts")
    for lvl in sorted(by_level):
        b = by_level[lvl]
        ok = [c for c in b if c.get("ok")]
        tps50 = pctl([c.get("tok_s") for c in ok], 50)
        tps90 = pctl([c.get("tok_s") for c in ok], 90)
        lat50 = pctl([c.get("latency_s") for c in ok], 50)
        tmo = sum(1 for c in b if is_timeout(c))
        print(
            f"  {lvl:8d}  {len(b):5d}   "
            f"{tps50 if tps50 is not None else float('nan'):9.1f}   "
            f"{tps90 if tps90 is not None else float('nan'):9.1f}   "
            f"{lat50 if lat50 is not None else float('nan'):11.1f}   {tmo:8d}"
        )
    print("  ^ falling tok/s and rising timeouts with inflight = endpoint contention.")


def from_result_json(job: str):
    """Fallback: output_tokens / wall_clock per trial (contaminated lower bound)."""

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
        tps = [c["tok_s"] for c in ok if c.get("tok_s")]
        lat = [c["latency_s"] for c in ok if c.get("latency_s")]
        comp = [c["completion_tokens"] for c in ok if c.get("completion_tokens")]
        tmo = sum(1 for c in calls if is_timeout(c))
        trunc = sum(1 for c in calls if c.get("finish_reason") == "length")
        print(f"OBJECTIVE per-call tok/s  (from {len(calls)} logged calls, {len(ok)} ok)")
        print(f"  tok/s     : p50 {pctl(tps,50):.1f}  p90 {pctl(tps,90):.1f}  "
              f"min {min(tps):.1f}  max {max(tps):.1f}")
        print(f"  latency_s : p50 {pctl(lat,50):.1f}  p90 {pctl(lat,90):.1f}")
        print(f"  out toks  : p50 {pctl(comp,50):.0f}  p90 {pctl(comp,90):.0f}")
        print(f"  failures  : {len(calls) - len(ok)} total "
              f"({tmo} timeouts, {len(calls) - len(ok) - tmo} other)")
        print(f"  truncated : {trunc} calls hit max_tokens (finish_reason=length)")
        tp = pctl(tps, 50)
        ot = pctl(comp, 50)
        if tp and ot:
            print(f"\n  => median step gen time ~= {ot/tp:.1f}s ({ot:.0f} tok / {tp:.1f} tok/s)")
            print(f"     in a 5400s (90min) budget, decode alone caps ~{int(5400/(ot/tp))} steps")
        print_timeline(calls)
        print_contention(calls)
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
