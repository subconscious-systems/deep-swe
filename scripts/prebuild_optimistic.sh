#!/usr/bin/env bash
# Prebuild (task images + agent overlays) for just the optimistic subset —
# the task_names listed in full-spark-optimistic.yaml.
#   ./mini-swe-agent/prebuild_optimistic.sh
# Extra env knobs pass through (e.g. PREBUILD_JOBS=8).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
CONFIG="mini-swe-agent/full-spark-optimistic.yaml"

# Parse task_names with pier's python (guaranteed to have pyyaml).
pier_python="$(head -1 "$(command -v pier)" | sed 's/^#!//')"
tasks="$("$pier_python" - "$CONFIG" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))
for t in cfg["datasets"][0]["task_names"]:
    print(f"tasks/{t}")
PY
)"

echo "Prebuilding $(wc -l <<<"$tasks" | tr -d ' ') optimistic tasks (config: $CONFIG)"
# shellcheck disable=SC2086
PREBUILD_CONFIG="$CONFIG" exec ./scripts/prebuild.sh $tasks
