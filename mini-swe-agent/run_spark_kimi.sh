#!/usr/bin/env bash
# Full DGX Spark eval against tim-kimi2.6:
#   ./mini-swe-agent/run_spark_kimi.sh
# Wraps: ./scripts/pier-run.sh run -c mini-swe-agent/full-spark-kimi.yaml -y
# Extra args pass through to `pier run`.
# Prebuild first (model-agnostic, cheap if warmed): ./scripts/prebuild.sh
# Qwen equivalent: ./mini-swe-agent/run_spark.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/pier-run.sh" run -c mini-swe-agent/full-spark-kimi.yaml -y "$@"
