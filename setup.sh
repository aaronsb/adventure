#!/usr/bin/env bash
# setup.sh — create the virtualenv and install everything the harness needs.
# Safe to re-run: it just refreshes the venv and dependencies.
set -euo pipefail
cd "$(dirname "$0")"

echo "Setting up the Adventure harness..."

python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
echo "  venv ready (.venv/) — installed:"
.venv/bin/pip list 2>/dev/null | grep -iE '^(adventure|anthropic|rich) ' \
  | sed 's/^/    /'

if [ ! -f .env ]; then
  printf '# Your Anthropic API key — required to run the harness.\nANTHROPIC_API_KEY=\n' > .env
  echo "  created .env — add your Anthropic API key to it"
else
  echo "  .env already present (left untouched)"
fi

echo
echo "Next:"
echo "  1. put your key in .env :  ANTHROPIC_API_KEY=sk-..."
echo "  2. check the plumbing   :  ./adventure.sh --selftest"
echo "  3. play                 :  ./adventure.sh"
