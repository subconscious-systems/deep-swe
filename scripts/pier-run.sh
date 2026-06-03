#!/usr/bin/env bash
# Run Pier for this repo. DEEP_SWE_ROOT (used by the pricing/ and scripts/
# bind mounts in the job yamls) is always derived from this checkout — any
# value in .env is stripped and overridden via the generated env file.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DEEP_SWE_ROOT="$ROOT"
cd "$ROOT"

ENVFILE="$ROOT/.pier-env.generated"
{
  if [[ -f .env ]]; then
    grep -v '^DEEP_SWE_ROOT=' .env || true
  fi
  echo "DEEP_SWE_ROOT=$ROOT"
} > "$ENVFILE"

# Default `pier run` to --no-delete so built task images stay cached between
# runs; pass --delete to restore Pier's cleanup behavior.
extra=""
if [[ "${1:-}" == "run" && " $* " != *" --delete "* && " $* " != *" --no-delete "* ]]; then
  extra="--no-delete"
fi

exec pier "$@" ${extra:+$extra} --env-file "$ENVFILE"
