"""Auto-loaded LiteLLM timing probe for real eval runs (NO source changes).

Python imports a module named ``sitecustomize`` automatically at interpreter
startup if it is anywhere on ``sys.path``. We use that to register a LiteLLM
success/failure callback *inside the agent container* without touching
mini-swe-agent or pier. The callback writes one JSON line per LLM call to
$MSWEA_TIMING_LOG (default /logs/agent/llm_timing.jsonl), capturing:

    start, end, latency_s, prompt_tokens, completion_tokens, decode-inclusive
    tok/s (completion_tokens / latency), and whether the call was an error.

LiteLLM hands `log_success_event` real datetime start_time/end_time bracketing
the whole call, so this is the true realized per-call rate under the eval's
actual prompt sizes and concurrency — the objective tok/s you want for sizing
steps. (It's call-latency tok/s = prefill+queue+decode; for pure steady-state
decode use scripts/bench_tps.py, which streams and times first-vs-last token.)

Wiring (mini-swe-agent/full-spark.yaml), so the container finds this file:
    agents[].env:
      PYTHONPATH: /opt/deep-swe/scripts        # dir containing this file
      MSWEA_TIMING_LOG: /logs/agent/llm_timing.jsonl
    environment.mounts:  (add alongside the existing log mounts)
      - type: bind
        source: ${DEEP_SWE_ROOT}/scripts
        target: /opt/deep-swe/scripts
        read_only: true

If anything here fails it must NOT break the agent — every hook is best-effort.
"""
from __future__ import annotations

import json
import os
import threading

_LOG_PATH = os.environ.get("MSWEA_TIMING_LOG", "/logs/agent/llm_timing.jsonl")
_lock = threading.Lock()


def _write(record: dict) -> None:
    try:
        with _lock, open(_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass  # never let logging crash the run


def _record(kwargs, response_obj, start_time, end_time, ok: bool) -> None:
    try:
        latency = (end_time - start_time).total_seconds()
    except Exception:
        latency = None
    usage = getattr(response_obj, "usage", None) or {}
    if not isinstance(usage, dict):
        usage = getattr(usage, "model_dump", lambda: {})() or vars(usage)
    comp = usage.get("completion_tokens")
    prompt = usage.get("prompt_tokens")
    cached = None
    try:
        details = usage.get("prompt_tokens_details") or {}
        if not isinstance(details, dict):
            details = getattr(details, "model_dump", lambda: {})() or vars(details)
        cached = details.get("cached_tokens")
    except Exception:
        pass
    # finish_reason distinguishes normal stops from max_tokens truncations
    # (runaway-loop indicator); error class distinguishes Timeout (contention /
    # queueing indicator) from 5xx etc. Both best-effort.
    finish_reason = None
    try:
        choices = getattr(response_obj, "choices", None)
        if choices:
            finish_reason = getattr(choices[0], "finish_reason", None)
    except Exception:
        pass
    error = None
    if not ok:
        try:
            exc = (kwargs or {}).get("exception")
            if exc is not None:
                error = f"{type(exc).__name__}: {exc}"[:300]
        except Exception:
            pass
    _write({
        "start": start_time.isoformat() if start_time else None,
        "end": end_time.isoformat() if end_time else None,
        "latency_s": latency,
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "completion_tokens": comp,
        "tok_s": (comp / latency) if (comp and latency and latency > 0) else None,
        "finish_reason": finish_reason,
        "ok": ok,
        "error": error,
        "model": (kwargs or {}).get("model"),
    })


def _install() -> None:
    try:
        import litellm
        from litellm.integrations.custom_logger import CustomLogger
    except Exception:
        return

    class _TimingLogger(CustomLogger):
        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            _record(kwargs, response_obj, start_time, end_time, ok=True)

        # async path (mini-swe-agent uses acompletion)
        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            _record(kwargs, response_obj, start_time, end_time, ok=True)

        def log_failure_event(self, kwargs, response_obj, start_time, end_time):
            _record(kwargs, response_obj, start_time, end_time, ok=False)

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
            _record(kwargs, response_obj, start_time, end_time, ok=False)

    try:
        litellm.callbacks = list(getattr(litellm, "callbacks", []) or []) + [_TimingLogger()]
    except Exception:
        pass


_install()
