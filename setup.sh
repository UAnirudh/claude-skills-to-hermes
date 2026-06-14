#!/usr/bin/env bash
# Skill Bridge — one-command setup (macOS / Linux)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "==> Skill Bridge setup"
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 is required. Install Python 3.10+ and retry."
  exit 1
fi

PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "Python: $PYVER"

if [ ! -d ".venv" ]; then
  echo "==> Creating virtual environment (.venv)"
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing skill-bridge with watch + dev extras"
python -m pip install --upgrade pip
python -m pip install -e ".[all]"

echo "==> Running tests"
pytest -q

if [ ! -f "skill-bridge.yaml" ]; then
  echo "==> Creating skill-bridge.yaml from example"
  cp skill-bridge.yaml.example skill-bridge.yaml
  echo "    Edit skill-bridge.yaml to match your agent paths, then run:"
  echo "    skill-bridge sync"
else
  echo "==> skill-bridge.yaml already exists (skipped)"
fi

echo
echo "Done! Activate the venv and run:"
echo "  source .venv/bin/activate"
echo "  skill-bridge --help"
echo "  skill-bridge detect"
echo "  skill-bridge init          # regenerate config"
echo "  skill-bridge sync          # one-shot sync"
echo "  skill-bridge watch         # live sync"
