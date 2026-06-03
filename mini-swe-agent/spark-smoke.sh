#!/usr/bin/env bash
# End-to-end wiring check on ONE task before committing to a full run:
# preflight (mount sources exist) → prebuild (image + overlay) → one dev trial
# → verify overlay cache hit, TurnFailureModel, llm_timing.jsonl, cost
# tracking, and the tok/s report.
#
# Usage:
#   ./mini-swe-agent/spark-smoke.sh [tasks/<task-dir>]   # default: true-myth (short)
#
# Exits 0 only if the trial ran AND all wiring checks pass.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TASK_DIR="${1:-tasks/true-myth-iterable-collection-combinators}"
TASK="$(basename "$TASK_DIR")"
OVERLAY_DIR="${PREBUILD_OVERLAY_DIR:-/tmp/deepswe-agent-overlays}"

fail=0
check() { # check <name> <0|1> [detail]
  if [ "$2" -eq 0 ]; then
    echo "PASS  $1${3:+  — $3}"
  else
    echo "FAIL  $1${3:+  — $3}"
    fail=1
  fi
}

echo "=== [0/3] Preflight: bind-mount sources resolve on THIS machine ==="
# If a mounted file is missing, the agent dies at startup inside the container.
pf=0
# environment.mounts is silently IGNORED by pier < 0.2.1 (no
# docker-compose-mounts.json written) — the scripts/pricing mounts vanish and
# the agent dies on the turn_failure_model import.
pv="$(pier --version 2>/dev/null | head -1)"
if [ -n "$pv" ] && [ "$(printf '%s\n' "0.2.1" "$pv" | sort -V | head -1)" = "0.2.1" ]; then
  echo "PASS  pier $pv (>= 0.2.1, supports environment.mounts)"
else
  echo "FAIL  pier ${pv:-not found} — need >= 0.2.1, which is git-only (PyPI stops at 0.2.0):"
  echo "      uv tool install --force 'datacurve-pier @ git+https://github.com/datacurve-ai/pier'"
  pf=1
fi
for f in "$ROOT/pricing/model-registry.json" "$ROOT/scripts/sitecustomize.py" \
         "$ROOT/scripts/turn_failure_model.py"; do
  if [ -f "$f" ]; then
    echo "PASS  mount source exists: $f"
  else
    echo "FAIL  mount source MISSING: $f"
    pf=1
  fi
done
if [ -f .env ] && grep -q '^BASETEN_API_KEY=.' .env && grep -q '^BASETEN_BASE_URL=.' .env; then
  echo "PASS  BASETEN_API_KEY / BASETEN_BASE_URL set in .env"
else
  echo "FAIL  BASETEN_API_KEY / BASETEN_BASE_URL missing in .env"
  pf=1
fi
if [ "$pf" -ne 0 ]; then
  echo
  echo "Preflight failed — fix the FAILs above before burning a trial."
  exit 1
fi

echo
echo "=== [1/3] Prebuild: task image + agent overlay ($TASK) ==="
./scripts/prebuild.sh "$TASK_DIR" || { echo "prebuild failed — aborting"; exit 1; }

# In-container import check with the same PYTHONPATH + mount the trial uses,
# but without pier. PASS here + import failure in the trial = pier is not
# injecting the env/mount (check `pier --version` and the trial's
# docker-compose-mounts.json).
img="deepswe-agent/$TASK:local"
if docker run --rm -e PYTHONPATH=/opt/deep-swe/scripts \
     -v "$ROOT/scripts:/opt/deep-swe/scripts:ro" "$img" \
     bash -c '. "$HOME/.local/bin/env" 2>/dev/null; python_bin="$(head -1 "$(command -v mini-swe-agent)" | sed "s/^#!//")"; "$python_bin" -c "import turn_failure_model, sitecustomize"' >/dev/null 2>&1; then
  echo "PASS  turn_failure_model + sitecustomize import inside the agent container"
else
  echo "FAIL  turn_failure_model does NOT import inside the agent container ($img)"
  echo "      re-run without redirection to see why:"
  echo "      docker run --rm -e PYTHONPATH=/opt/deep-swe/scripts -v \"$ROOT/scripts:/opt/deep-swe/scripts:ro\" $img \\"
  echo "        bash -c '. \"\$HOME/.local/bin/env\"; python_bin=\"\$(head -1 \"\$(command -v mini-swe-agent)\" | sed \"s/^#!//\")\"; \"\$python_bin\" -c \"import turn_failure_model\"'"
  exit 1
fi

echo
echo "=== [2/3] Run dev trial ==="
./mini-swe-agent/run_dev.sh -p "$TASK_DIR"
run_rc=$?

echo
echo "=== [3/3] Verify wiring ==="
job="$(ls -td jobs/*/ 2>/dev/null | head -1)"
job="${job%/}"
trial="$(ls -td "$job"/*/ 2>/dev/null | grep -v egress | head -1)"
trial="${trial%/}"
echo "job:   $job"
echo "trial: $trial"
echo

check "pier run exited 0" "$run_rc"

# (2) overlay cache hit: trial Dockerfile byte-identical to the prebuilt one
if [ -f "$trial/agent-build-context/Dockerfile" ] && [ -f "$OVERLAY_DIR/$TASK/Dockerfile" ]; then
  diff -q "$trial/agent-build-context/Dockerfile" "$OVERLAY_DIR/$TASK/Dockerfile" >/dev/null
  check "overlay Dockerfile identical (trial build = cache hit)" $?
else
  check "overlay Dockerfile identical (trial build = cache hit)" 1 "Dockerfile missing on one side"
fi

# (3) custom model class loaded
python3 - "$trial" <<'PY'
import json, sys
trial = sys.argv[1]
try:
    info = json.load(open(f"{trial}/agent/mini-swe-agent.trajectory.json")).get("info", {})
    mt = (info.get("config") or {}).get("model_type", "")  # LitellmModel.serialize()
    ok = mt.endswith("TurnFailureModel")
    print(f"{'PASS' if ok else 'FAIL'}  TurnFailureModel loaded  — model_type: {mt or 'missing'}")
    sys.exit(0 if ok else 1)
except Exception as e:
    print(f"FAIL  TurnFailureModel loaded  — {e}")
    sys.exit(1)
PY
[ $? -ne 0 ] && fail=1

# (4) timing probe wrote per-call records
if [ -s "$trial/agent/llm_timing.jsonl" ]; then
  n=$(wc -l < "$trial/agent/llm_timing.jsonl" | tr -d ' ')
  check "llm_timing.jsonl written" 0 "$n calls logged"
else
  check "llm_timing.jsonl written" 1 "missing or empty"
fi

# (5) cost tracking nonzero
python3 - "$trial" <<'PY'
import json, sys
trial = sys.argv[1]
try:
    ar = json.load(open(f"{trial}/result.json")).get("agent_result") or {}
    cost, out_toks = ar.get("cost_usd"), ar.get("n_output_tokens")
    ok = bool(cost and cost > 0)
    print(f"{'PASS' if ok else 'FAIL'}  cost tracked  — cost_usd: {cost}, output_tokens: {out_toks}")
    sys.exit(0 if ok else 1)
except Exception as e:
    print(f"FAIL  cost tracked  — {e}")
    sys.exit(1)
PY
[ $? -ne 0 ] && fail=1

# reward (informational — a failed task is NOT a wiring failure)
python3 - "$trial" <<'PY'
import json, sys
try:
    r = json.load(open(f"{sys.argv[1]}/result.json"))
    vr = r.get("verifier_result") or {}
    print(f"INFO  task reward: {vr.get('reward')}  exception: {(r.get('exception_info') or {}).get('exception_type')}")
except Exception as e:
    print(f"INFO  task reward: unavailable ({e})")
PY

# (6) tok/s report renders
echo
echo "--- tps_from_logs.py $job ---"
if python3 scripts/tps_from_logs.py "$job"; then
  check "tps_from_logs report" 0
else
  check "tps_from_logs report" 1
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "SMOKE TEST PASSED — wiring verified; safe to launch ./mini-swe-agent/run_spark.sh"
else
  echo "SMOKE TEST FAILED — fix the FAIL lines above before a full run"
  for f in "$trial/exception.txt" "$trial/agent/mini-swe-agent.txt" "$trial/trial.log"; do
    if [ -s "$f" ]; then
      echo
      echo "--- tail -25 $f ---"
      tail -25 "$f"
    fi
  done
fi
exit "$fail"
