#!/usr/bin/env bash
# One-liner for the DGX Spark full eval:
#   ./run_spark.sh
# Wraps: ./scripts/pier-run.sh run -c mini-swe-agent-full-spark.yaml -y
# Extra args are passed through to `pier run` (e.g. ./run_spark.sh --delete).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/scripts/pier-run.sh" run -c mini-swe-agent-full-spark.yaml -y "$@"
