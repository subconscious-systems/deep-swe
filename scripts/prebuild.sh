#!/usr/bin/env bash
# Pre-build all task environment images natively (DGX Spark / any aarch64 host).
#
# The prebuilt images in task.toml [environment].docker_image are amd64-only.
# mini-swe-agent/full-spark.yaml sets force_build: true, so Pier builds each
# task from environment/Dockerfile via `docker compose build`. Running this
# script first warms the shared BuildKit layer cache, making those per-trial
# builds near-instant cache hits during the eval.
#
# Phase 2 then prebuilds Pier's agent-install overlay on top of each task
# image (apt deps + uv + mini-swe-agent + LiteLLM cost-map refresh — the
# network-bound layers Pier otherwise builds per task DURING the eval).
# scripts/gen_agent_overlay.py emits the byte-identical Dockerfile via Pier's
# own code, so the per-trial `docker compose build` is a pure layer-cache hit.
# Re-run prebuild after changing the mini-swe-agent version pin in the job
# yaml (agents[].kwargs.version) — that invalidates the overlay layers.
#
# Usage:
#   ./scripts/prebuild.sh                                  # all tasks, 8 at a time
#   PREBUILD_JOBS=8 ./scripts/prebuild.sh                  # more parallel builds
#   PREBUILD_CONFIG=mini-swe-agent/dev.yaml ./scripts/prebuild.sh  # overlay from another job yaml
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
    # Also tag the native build with the task.toml prebuilt image name: Pier's
    # agent-install overlay always uses `FROM <docker_image>` (it ignores
    # force_build — pier/environments/docker/docker.py write_agent_dockerfile
    # call site), so on arm64 the overlay would otherwise build on the
    # amd64-only ECR image and die with `exec format error`. Local tags win
    # over registry pulls at build time, so this redirects the overlay to the
    # native image.
    local prebuilt
    prebuilt=$(sed -n 's/^docker_image = "\(.*\)"$/\1/p' "$dir/task.toml" 2>/dev/null | head -1)
    if [ -n "$prebuilt" ]; then
      docker tag "deepswe-env/$task:local" "$prebuilt" >>"$LOG_DIR/$task.log" 2>&1
    fi
    echo "OK    $task  ($(($(date +%s) - start))s)"
  else
    echo "FAIL  $task  ($(($(date +%s) - start))s)  log: $LOG_DIR/$task.log"
    return 1
  fi
}
export -f build_one

total=${#task_dirs[@]}
echo "Building $total task images, $JOBS in parallel — per-task build logs: $LOG_DIR"

printf '%s\n' "${task_dirs[@]}" \
  | xargs -P "$JOBS" -I{} bash -c 'build_one "$1"' _ {} \
  | awk -v t="$total" '{ printf "[%3d/%d] %s\n", ++n, t, $0; fflush() }' \
  | tee "$LOG_DIR/summary.txt"

ok=$(grep -c '] OK' "$LOG_DIR/summary.txt" 2>/dev/null || true)
fails=$(grep -c '] FAIL' "$LOG_DIR/summary.txt" 2>/dev/null || true)
echo
echo "Task images done: ${ok:-0} ok, ${fails:-0} failed. Logs: $LOG_DIR"

# ---- Phase 2: Pier agent-install overlay layers --------------------------
# Generated with Pier's own code (gen_agent_overlay.py) so the Dockerfile is
# byte-identical to what each trial generates -> guaranteed cache hit.
CONFIG="${PREBUILD_CONFIG:-mini-swe-agent/full-spark.yaml}"
OVERLAY_DIR="${PREBUILD_OVERLAY_DIR:-/tmp/deepswe-agent-overlays}"
overlay_fails=0
if ! command -v pier >/dev/null 2>&1; then
  echo "pier not on PATH — skipping agent-overlay prebuild (trials will build it inline)"
else
  pier_python="$(head -1 "$(command -v pier)" | sed 's/^#!//')"
  build_overlay() {
    local task="$1" dir="$2" start base
    base="$(sed -n 's/^FROM //p' "$dir/Dockerfile" | head -1)"
    # Don't let `docker build` pull the (amd64-only) registry image if the
    # native task image isn't built locally — skip instead.
    if ! docker image inspect "$base" >/dev/null 2>&1; then
      echo "SKIP  $task (base image not built locally)"
      return 0
    fi
    start=$(date +%s)
    if docker build -t "deepswe-agent/$task:local" "$dir" >"$LOG_DIR/overlay-$task.log" 2>&1; then
      echo "OK    $task  ($(($(date +%s) - start))s)"
    else
      echo "FAIL  $task  ($(($(date +%s) - start))s)  log: $LOG_DIR/overlay-$task.log"
      return 1
    fi
  }
  export -f build_overlay

  echo
  echo "Building agent overlays (config: $CONFIG), $JOBS in parallel"
  manifest="$("$pier_python" scripts/gen_agent_overlay.py --config "$CONFIG" \
              --out-dir "$OVERLAY_DIR" "${task_dirs[@]}")"
  if [ -n "$manifest" ]; then
    o_total=$(printf '%s\n' "$manifest" | wc -l | tr -d ' ')
    printf '%s\n' "$manifest" \
      | xargs -P "$JOBS" -L1 bash -c 'build_overlay "$1" "$2"' _ \
      | awk -v t="$o_total" '{ printf "[%3d/%d] %s\n", ++n, t, $0; fflush() }' \
      | tee "$LOG_DIR/overlay-summary.txt"
    o_ok=$(grep -c '] OK' "$LOG_DIR/overlay-summary.txt" 2>/dev/null || true)
    overlay_fails=$(grep -c '] FAIL' "$LOG_DIR/overlay-summary.txt" 2>/dev/null || true)
    echo
    echo "Agent overlays done: ${o_ok:-0} ok, ${overlay_fails:-0} failed. Logs: $LOG_DIR"
  else
    echo "No overlay Dockerfiles generated (see messages above)."
  fi
fi

[ "${fails:-0}" -eq 0 ] && [ "${overlay_fails:-0}" -eq 0 ]
