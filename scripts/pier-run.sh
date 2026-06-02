#!/usr/bin/env bash
# Run Pier with DEEP_SWE_ROOT set for pricing bind mounts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DEEP_SWE_ROOT="${DEEP_SWE_ROOT:-$ROOT}"
cd "$ROOT"
if [[ -f .env ]]; then
  exec pier "$@" --env-file .env
fi
exec pier "$@"
