#!/usr/bin/env python3
"""Quick mid-run status for a Pier job: pass rate, exceptions, cost, ETA.

Usage:
  scripts/job-status.py                 # latest job in jobs/
  scripts/job-status.py jobs/<job-dir>  # specific job
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_job_dir() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    jobs = sorted(
        (d for d in (ROOT / "jobs").iterdir() if (d / "config.json").exists()),
        key=lambda d: d.name,
    )
    if not jobs:
        sys.exit("No job directories found under jobs/")
    return jobs[-1]


def count_dataset_tasks(config: dict) -> int:
    n = 0
    for ds in config.get("datasets", []):
        names = ds.get("task_names")
        if names:
            n += len(names)
        elif ds.get("path"):
            path = ROOT / ds["path"]
            n += sum(1 for d in path.iterdir() if (d / "task.toml").exists())
    n += len(config.get("tasks", []))
    return n


def parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main() -> None:
    job_dir = find_job_dir()
    config = json.loads((job_dir / "config.json").read_text())
    expected = (
        count_dataset_tasks(config)
        * config.get("n_attempts", 1)
        * max(len(config.get("agents", [])), 1)
    )

    trial_dirs = [d for d in job_dir.iterdir() if d.is_dir()]
    done, running = [], []
    for d in trial_dirs:
        rp = d / "result.json"
        if rp.exists():
            done.append(json.loads(rp.read_text()))
        else:
            running.append(d.name)

    rewards, costs, steps, durations = [], [], [], []
    exceptions: dict[str, int] = {}
    for t in done:
        reward = ((t.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        rewards.append(reward if reward is not None else 0.0)
        ar = t.get("agent_result") or {}
        if ar.get("cost_usd") is not None:
            costs.append(ar["cost_usd"])
        if t.get("n_agent_steps") is not None:
            steps.append(t["n_agent_steps"])
        ei = t.get("exception_info")
        if ei:
            exc = ei.get("exception_type", "Unknown")
            exceptions[exc] = exceptions.get(exc, 0) + 1
        if t.get("started_at") and t.get("finished_at"):
            durations.append(
                (parse_ts(t["finished_at"]) - parse_ts(t["started_at"])).total_seconds()
            )

    passed = sum(1 for r in rewards if r >= 1)
    n_done = len(done)

    print(f"Job: {job_dir.name}")
    print(f"  Trials:     {n_done}/{expected} done, {len(running)} in flight")
    if n_done:
        print(f"  Pass rate:  {passed}/{n_done} ({passed / n_done:.1%})")
        print(f"  Mean reward: {sum(rewards) / n_done:.3f}")
        print(f"  Cost so far: ${sum(costs):.2f}", end="")
        if costs:
            print(f"  (avg ${sum(costs) / len(costs):.2f}/trial,"
                  f" projected ${sum(costs) / len(costs) * expected:.2f} total)")
        else:
            print()
        if steps:
            print(f"  Avg steps:  {sum(steps) / len(steps):.0f}")
        if durations:
            avg = sum(durations) / len(durations)
            print(f"  Avg trial:  {avg / 60:.0f}m {avg % 60:.0f}s")
            n_conc = config.get("n_concurrent_trials", 4)
            remaining = max(expected - n_done, 0)
            print(f"  Rough ETA:  {remaining * avg / n_conc / 3600:.1f}h"
                  f" ({remaining} trials left at {n_conc} concurrent)")
    if exceptions:
        print("  Exceptions:")
        for exc, count in sorted(exceptions.items(), key=lambda kv: -kv[1]):
            print(f"    {exc}: {count}")
    if running:
        print(f"  In flight:  {', '.join(sorted(running)[:8])}"
              + (" …" if len(running) > 8 else ""))


if __name__ == "__main__":
    main()
