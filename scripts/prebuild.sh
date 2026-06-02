#!/usr/bin/env bash
# Pre-build all task environment images natively (DGX Spark / any aarch64 host).
#
# The prebuilt images in task.toml [environment].docker_image are amd64-only.
# mini-swe-agent-full-spark.yaml sets force_build: true, so Pier builds each
# task from environment/Dockerfile via `docker compose build`. Running this
# script first warms the shared BuildKit layer cache, making those per-trial
# builds near-instant cache hits during the eval.
#
# Usage:
#   ./scripts/prebuild.sh                                  # all tasks, 8 at a time
#   PREBUILD_JOBS=8 ./scripts/prebuild.sh                  # more parallel builds
#   ./scripts/prebuild.sh tasks/eicrud-keyset-pagination-cursor ...  # subset
#
# Re-running is cheap: already-built layers are cache hits. Failures are
# summarized at the end with per-task logs in $PREBUILD_LOG_DIR.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

JOBS="${PREBUILD_JOBS:-8}"
export LOG_DIR="${PREBUILD_LOG_DIR:-/tmp/deepswe-prebuild-logs}"
mkdir -p "$LOG_DIR"

if [ "$#" -gt 0 ]; then
  task_dirs=("$@")
else
  task_dirs=(tasks/*/)
fi

build_one() {
  local dir="${1%/}"
  local task ctx start
  task="$(basename "$dir")"
  ctx="$dir/environment"
  if [ ! -f "$ctx/Dockerfile" ]; then
    echo "SKIP  $task (no environment/Dockerfile)"
    return 0
  fi
  start=$(date +%s)
  if docker build -t "deepswe-env/$task:local" "$ctx" >"$LOG_DIR/$task.log" 2>&1; then
    echo "OK    $task  ($(($(date +%s) - start))s)"
  else
    echo "FAIL  $task  ($(($(date +%s) - start))s)  log: $LOG_DIR/$task.log"
    return 1
  fi
}
export -f build_one

printf '%s\n' "${task_dirs[@]}" \
  | xargs -P "$JOBS" -I{} bash -c 'build_one "$1"' _ {} \
  | tee "$LOG_DIR/summary.txt"

ok=$(grep -c '^OK' "$LOG_DIR/summary.txt" 2>/dev/null || true)
fails=$(grep -c '^FAIL' "$LOG_DIR/summary.txt" 2>/dev/null || true)
echo
echo "Done: ${ok:-0} ok, ${fails:-0} failed. Logs: $LOG_DIR"
[ "${fails:-0}" -eq 0 ]
