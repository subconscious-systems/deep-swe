#!/usr/bin/env python3
"""Objective tokens/sec benchmark for the eval's LLM endpoint.

Measures *raw model speed* and *how it degrades under concurrency* — the two
numbers you need to size step budgets and the 90-min agent timeout. This is a
controlled probe (fixed prompt, fixed output length), so it isolates the
endpoint from the noise that contaminates the per-trial wall-clock numbers
(bash exec time, retry backoff, mixed prompt sizes).

It reports three rates per request, then p50/p90 per concurrency level:
  - TTFT      : time to first token (prefill + queue wait). Spikes under load.
  - decode    : completion_tokens / (t_last - t_first)  -> steady-state gen rate.
                THIS is the number that dominates step time and step budgeting.
  - e2e       : completion_tokens / (t_last - t_start)   -> what you actually get.

Reads creds from .env (BASETEN_API_KEY / BASETEN_BASE_URL / BASETEN_MODEL),
same vars the eval uses. Outbound calls cost output tokens on your endpoint;
the default sweep is ~ (1+2+4+8) * ROUNDS * MAX_TOKENS tokens.

Usage:
    uv run --with httpx scripts/bench_tps.py                 # full sweep 1,2,4,8
    CONCURRENCY=1,8 ROUNDS=5 MAX_TOKENS=800 uv run --with httpx scripts/bench_tps.py
    # or: pip install httpx && python3 scripts/bench_tps.py
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

# --- config (env-overridable) ------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CONCURRENCY = [int(x) for x in os.environ.get("CONCURRENCY", "1,2,4,8").split(",")]
ROUNDS = int(os.environ.get("ROUNDS", "3"))          # requests per concurrency level = ROUNDS * level
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "600"))  # target output length per request
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.7"))
TOP_P = float(os.environ.get("TOP_P", "0.95"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "300"))

# A prompt that reliably generates a long, steady stream so decode rate is
# measured over many tokens (not a 5-token reply). We do NOT stop early.
PROMPT = (
    "Write a detailed technical explanation of how a B-tree database index "
    "works, covering node structure, splits, merges, range scans, and "
    "write amplification. Be thorough and keep writing until you are cut off."
)


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency on python-dotenv)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def one_request(client: httpx.Client, url: str, headers: dict, model: str) -> dict:
    """Stream one completion; return per-request timing + token counts."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
        "stream": True,
        # OpenAI-compatible: ask the server to emit a final usage chunk.
        "stream_options": {"include_usage": True},
    }
    t_start = time.perf_counter()
    t_first = None
    t_last = None
    chunk_tokens = 0          # fallback token count if usage is absent
    completion_tokens = None
    prompt_tokens = None
    err = None
    try:
        with client.stream("POST", url, headers=headers, json=body, timeout=REQUEST_TIMEOUT) as r:
            r.raise_for_status()
            for raw in r.iter_lines():
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[len("data:"):].strip()
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                # usage chunk arrives last (choices == [])
                if obj.get("usage"):
                    completion_tokens = obj["usage"].get("completion_tokens")
                    prompt_tokens = obj["usage"].get("prompt_tokens")
                for ch in obj.get("choices", []):
                    delta = ch.get("delta", {})
                    if delta.get("content"):
                        now = time.perf_counter()
                        if t_first is None:
                            t_first = now
                        t_last = now
                        chunk_tokens += 1
    except Exception as e:  # noqa: BLE001 - report, don't crash the sweep
        err = f"{type(e).__name__}: {e}"

    n_out = completion_tokens or chunk_tokens
    ttft = (t_first - t_start) if t_first else None
    decode_span = (t_last - t_first) if (t_first and t_last and t_last > t_first) else None
    e2e_span = (t_last - t_start) if t_last else None
    return {
        "err": err,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": n_out,
        "ttft_s": ttft,
        "decode_tps": (n_out - 1) / decode_span if (decode_span and n_out > 1) else None,
        "e2e_tps": n_out / e2e_span if (e2e_span and n_out) else None,
    }


def pctl(xs: list[float], p: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(round((p / 100) * (len(xs) - 1)))))
    return xs[k]


def run_level(client, url, headers, model, level: int) -> list[dict]:
    """Fire `level` requests at once, ROUNDS times, all timed concurrently."""
    results: list[dict] = []
    lock = threading.Lock()

    def task():
        res = one_request(client, url, headers, model)
        with lock:
            results.append(res)

    for _ in range(ROUNDS):
        with ThreadPoolExecutor(max_workers=level) as ex:
            for _ in range(level):
                ex.submit(task)
    return results


def main() -> int:
    load_dotenv(ROOT / ".env")
    key = os.environ.get("BASETEN_API_KEY")
    base = (os.environ.get("BASETEN_BASE_URL") or "").rstrip("/")
    model = os.environ.get("BASETEN_MODEL")
    if not (key and base and model):
        print("ERROR: set BASETEN_API_KEY / BASETEN_BASE_URL / BASETEN_MODEL (in .env)", file=sys.stderr)
        return 2
    url = f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    print(f"endpoint : {url}")
    print(f"model    : {model}")
    print(f"sweep    : concurrency={CONCURRENCY}  rounds={ROUNDS}  max_tokens={MAX_TOKENS}\n")
    print(f"{'conc':>4} {'reqs':>5} {'errs':>5} "
          f"{'decode p50':>11} {'decode p90':>11} {'ttft p50':>9} {'ttft p90':>9} {'e2e p50':>8}")

    summary = []
    with httpx.Client(http2=False) as client:
        for level in CONCURRENCY:
            res = run_level(client, url, headers, model, level)
            errs = [r for r in res if r["err"]]
            dec = [r["decode_tps"] for r in res]
            ttft = [r["ttft_s"] for r in res]
            e2e = [r["e2e_tps"] for r in res]
            row = dict(conc=level, reqs=len(res), errs=len(errs),
                       decode_p50=pctl(dec, 50), decode_p90=pctl(dec, 90),
                       ttft_p50=pctl(ttft, 50), ttft_p90=pctl(ttft, 90),
                       e2e_p50=pctl(e2e, 50))
            summary.append(row)
            f = lambda v, u="": f"{v:.1f}{u}" if v is not None else "  -"
            print(f"{level:>4} {len(res):>5} {len(errs):>5} "
                  f"{f(row['decode_p50']):>11} {f(row['decode_p90']):>11} "
                  f"{f(row['ttft_p50'],'s'):>9} {f(row['ttft_p90'],'s'):>9} {f(row['e2e_p50']):>8}")
            if errs:
                print(f"     first error: {errs[0]['err']}")

    out = ROOT / "scripts" / "bench_tps_result.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nwrote {out}")
    if len(summary) >= 2 and summary[0]["decode_p50"] and summary[-1]["decode_p50"]:
        drop = 100 * (1 - summary[-1]["decode_p50"] / summary[0]["decode_p50"])
        print(f"\ndecode tok/s at concurrency {summary[0]['conc']} -> {summary[-1]['conc']}: "
              f"{summary[0]['decode_p50']:.1f} -> {summary[-1]['decode_p50']:.1f}  "
              f"({drop:+.0f}% under load)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
