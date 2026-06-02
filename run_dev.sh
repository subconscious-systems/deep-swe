#!/usr/bin/env bash
# One-liner for the dev smoke test:
#   ./run_dev.sh
# Wraps: ./scripts/pier-run.sh run -c mini-swe-agent-dev.yaml -y
# Extra args are passed through to `pier run` (e.g. ./run_dev.sh -p tasks/<task-id>).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/scripts/pier-run.sh" run -c mini-swe-agent-dev.yaml -y "$@"
