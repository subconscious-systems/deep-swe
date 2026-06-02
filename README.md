# [DeepSWE](https://deepswe.datacurve.ai/)

DeepSWE is a benchmark for measuring frontier coding agents on original, long-horizon software engineering tasks drawn from active open-source repositories. The benchmark includes 113 tasks across TypeScript, Go, Python, JavaScript, and Rust, with isolated environments and program-based verifiers.

## Task format

DeepSWE tasks use the [Harbor](https://www.harborframework.com/docs/tasks) task format:

```text
task.toml         Metadata: repository, base commit, language, prebuilt image, resource limits
instruction.md    The prompt the agent sees
environment/      Dockerfile that reproduces the prebuilt image (fallback if the image is unavailable)
tests/            Verifier: test.sh (entry point) + test.patch (test additions, applied at grading time)
solution/         Reference solution (held out from the agent; for human and AI reviewers)
```

The verifier exercises the behavior the prompt describes. It accepts any solution whose observable behavior is correct, regardless of internal symbol names or structure.
The reference patch in `solution/` is never used at grading time; it exists so reviewers can spot-check correctness offline.

## Quickstart

Use [Pier](https://github.com/datacurve-ai/pier) to run the benchmark:

```bash
git clone https://github.com/datacurve-ai/deep-swe
cd deep-swe
cp .env.example .env   # fill in BASETEN_* and DEEP_SWE_ROOT (absolute path to this repo)
uv tool install datacurve-pier

# Dev smoke test (1 task, 30 min timeout, 600 step cap, 1 worker)
./run_dev.sh

# Full 113-task eval (leaderboard-comparable; verification on; 4 workers)
./scripts/pier-run.sh run -c mini-swe-agent-full.yaml -y

# Full eval on the DGX Spark (8 workers)
./run_spark.sh

# Single task
pier run -c mini-swe-agent-dev.yaml --env-file .env -y -p tasks/<task-id>
```

Job configs: `mini-swe-agent-dev.yaml` (iteration), `mini-swe-agent-full.yaml` (full eval).

Set `DEEP_SWE_ROOT` in `.env` to the absolute path of this repository. Pier passes it to Docker Compose so the pricing bind mount (`pricing/subconscious-tim-qwen3.6-27b.json`) resolves correctly (relative paths are not supported).

### Token pricing

Both job configs mount a LiteLLM model registry at `pricing/subconscious-tim-qwen3.6-27b.json` for `subconscious/tim-qwen3.6-27b` (keep in sync with `BASETEN_MODEL` in `.env`):

| Kind | Rate |
|------|------|
| Input tokens | $0.50 / 1M |
| Cached input tokens | $0.05 / 1M |
| Output tokens | $3.50 / 1M |

Costs appear in `jobs/<job-dir>/result.json` as `stats.cost_usd` and per-trial `agent_result.cost_usd` when the run completes.

### Dev vs full limits

| | Dev (`mini-swe-agent-dev.yaml`) | Full (`mini-swe-agent-full.yaml`) |
|--|--------------------------------|-----------------------------------|
| Concurrent trials | 1 | 4 |
| Agent timeout | 30 min (`override_timeout_sec`) | 90 min (`task.toml` `[agent] timeout_sec`) |
| Step limit | 600 | Unlimited until timeout (mini-swe default) |
| Tasks | Pinned in YAML (edit `task_names`) | All 113 under `tasks/` |

## Pause, resume, and recover

1. **Pause:** Press `Ctrl+C` once in the terminal running `pier run`. Let Pier shut down gracefully before force-killing the process or closing the laptop.
2. **What is saved:** `jobs/<timestamp>/config.json`, `result.json`, `lock.json`, and per-trial directories with `result.json` for finished trials.
3. **Resume** (uses the frozen `config.json` saved in the job directory—not the current YAML on disk):

```bash
pier job resume -p jobs/<job-dir>
```

Or with `DEEP_SWE_ROOT` set: `./scripts/pier-run.sh job resume -p jobs/<job-dir>`

- Completed trials are skipped; only remaining trials run.
- By default, `pier job resume` removes trial dirs that ended with `CancelledError` so those tasks are retried.
- Trial folders without `result.json` but with partial artifacts are cleaned up and restarted from scratch.
- Resume does **not** continue a single agent mid-trajectory—only at **trial** granularity.
- If resume fails with a `lock.json` mismatch, Pier detected a change in job inputs (for example `DEEP_SWE_ROOT` or other `.env` values) since the job was created; restore the same environment or start a new job directory.

Inspect progress and costs with `pier view jobs`.

## What is Pier

[Pier](https://github.com/datacurve-ai/pier) is a [Harbor](https://www.harborframework.com/docs/tasks)-compatible framework for sandboxed coding-agent evals. It began as a fork of Harbor to support CLI agents in air-gapped tasks: Harbor blocks all outbound traffic in `allow_internet = false` tasks, including dependency installs and LLM API calls. Pier adds per-agent network allowlists, giving agents only the network access they need while keeping the task environment isolated.

Pier also adds more complete trajectory metadata, a better trajectory viewer, and `pier critique run` for analyzing agent trajectories. All leaderboard scores were produced with Pier running `mini-swe-agent` on Modal.

### Agents and models

`mini-swe-agent` is model-agnostic. Pier also drives `claude-code`, `codex`, `gemini-cli`, and `opencode` directly. Pass `--env modal` to run in parallel sandboxes on Modal.

### Subsets and single tasks

Deterministic random subset of the 113-task corpus:

```bash
pier run -p deep-swe/tasks --agent mini-swe-agent --n-tasks 10 --sample-seed 0
```

Single task:

```bash
pier run -p deep-swe/tasks/<task-id> --agent mini-swe-agent
```
