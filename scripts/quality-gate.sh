#!/usr/bin/env bash
# Unified quality gate for co-cli.
#
# Usage:
#   scripts/quality-gate.sh lint          # ruff check + format (fast, per-task)
#   scripts/quality-gate.sh lint --fix    # ruff auto-fix + format
#   scripts/quality-gate.sh types         # lint + pyright (integration)
#   scripts/quality-gate.sh full          # lint + pyright + pytest (ship gate)
#
# Skill files and CI call this script — add new tools here, not in
# individual skill files. Pre-commit hook is self-contained in .git/hooks/.

set -euo pipefail

LEVEL="${1:-lint}"
FIX=""
if [[ "${2:-}" == "--fix" ]]; then
    FIX=1
fi

# --- Lint (ruff) ---
# Use repo-root Ruff config discovery.
if [[ -n "$FIX" ]]; then
    echo "==> ruff check --fix"
    uv run ruff check --fix
    echo "==> ruff format"
    uv run ruff format
else
    echo "==> ruff check"
    uv run ruff check
    echo "==> ruff format --check"
    uv run ruff format --check
fi

[[ "$LEVEL" == "lint" ]] && { echo "==> lint OK"; exit 0; }

# --- Types (pyright) ---
echo "==> pyright"
uv run pyright

[[ "$LEVEL" == "types" ]] && { echo "==> types OK"; exit 0; }

# --- Full (pytest) ---
echo "==> pytest"
mkdir -p .pytest-logs
uv run pytest -v 2>&1 | tee ".pytest-logs/$(date +%Y%m%d-%H%M%S)-check.log"

echo "==> full OK"
