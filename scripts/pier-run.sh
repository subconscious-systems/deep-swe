#!/usr/bin/env bash
# Run Pier with DEEP_SWE_ROOT set for pricing bind mounts.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DEEP_SWE_ROOT="${DEEP_SWE_ROOT:-$ROOT}"
cd "$ROOT"

# Default `pier run` to --no-delete so built task images stay cached between
# runs (Pier's default is `docker compose down --rmi all`, which deletes them).
# Pass --delete explicitly to restore Pier's cleanup behavior.
extra=""
if [[ "${1:-}" == "run" && " $* " != *" --delete "* && " $* " != *" --no-delete "* ]]; then
  extra="--no-delete"
fi

if [[ -f .env ]]; then
  exec pier "$@" ${extra:+$extra} --env-file .env
fi
exec pier "$@" ${extra:+$extra}
