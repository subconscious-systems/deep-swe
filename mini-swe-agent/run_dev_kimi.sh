#!/usr/bin/env bash
# Dev smoke test against tim-kimi2.6:
#   ./mini-swe-agent/run_dev_kimi.sh
# Wraps: ./scripts/pier-run.sh run -c mini-swe-agent/dev-kimi.yaml -y
# Extra args pass through to `pier run` (e.g. -p tasks/<task-id>).
# Qwen equivalent: ./mini-swe-agent/run_dev.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/pier-run.sh" run -c mini-swe-agent/dev-kimi.yaml -y "$@"
