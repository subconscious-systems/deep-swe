#!/usr/bin/env bash
# Optimistic DGX Spark run: the 22 tasks with >=50% average pass rate on prior
# benchmarks, ONE attempt each — fast high-signal pass, not leaderboard-comparable.
#   ./mini-swe-agent/run_spark_optimistic.sh
# Wraps: ./scripts/pier-run.sh run -c mini-swe-agent/full-spark-optimistic.yaml -y
# Extra args are passed through to `pier run`.
#
# Prebuild first (cheap if already warmed): ./scripts/prebuild.sh
# Full 113-task eval: ./mini-swe-agent/run_spark.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/pier-run.sh" run -c mini-swe-agent/full-spark-optimistic.yaml -y "$@"
