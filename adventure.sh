#!/usr/bin/env bash
# adventure.sh — run the Adventure harness.
# Thin wrapper: makes sure the venv exists, then hands off to harness.py.
# Any arguments are passed straight through, e.g.:
#   ./adventure.sh --selftest
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "No virtualenv found — run ./setup.sh first." >&2
  exit 1
fi

exec .venv/bin/python harness.py "$@"
