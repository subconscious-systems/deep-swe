#!/usr/bin/env bash
# Optimistic DGX Spark run against tim-kimi2.6: the 22 tasks with >=50% average
# pass rate (measured with qwen), ONE attempt each — fast signal run.
#   ./mini-swe-agent/run_spark_optimistic_kimi.sh
# Wraps: ./scripts/pier-run.sh run -c mini-swe-agent/full-spark-optimistic-kimi.yaml -y
# Qwen equivalent: ./mini-swe-agent/run_spark_optimistic.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/pier-run.sh" run -c mini-swe-agent/full-spark-optimistic-kimi.yaml -y "$@"
