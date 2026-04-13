#!/usr/bin/env bash
# Unified quality gate for co-cli.
#
# Usage:
#   scripts/quality-gate.sh lint          # ruff check + format (fast, per-task)
#   scripts/quality-gate.sh lint --fix    # ruff auto-fix + format
#   scripts/quality-gate.sh full          # lint + pytest (ship gate)
#
# Skill files and CI call this script — add new tools here, not in
# individual skill files. Pre-commit hook is self-contained in .git/hooks/.

set -euo pipefail

LEVEL="${1:-lint}"
FIX=""
if [[ "${2:-}" == "--fix" ]]; then
    FIX=1
fi

PASS="✓"
FAIL="✗"
START=$(date +%s)

step_status() {
    local label="$1"
    local status="$2"   # pass | fail
    local elapsed=$(( $(date +%s) - START ))
    if [[ "$status" == "pass" ]]; then
        echo "  $PASS $label  [${elapsed}s]"
    else
        echo "  $FAIL $label  [${elapsed}s]"
    fi
}

echo ""
echo "=== quality-gate: $LEVEL ==="
echo ""

# --- Lint (ruff) ---
echo "[1/2] lint"
if [[ -n "$FIX" ]]; then
    echo "  → ruff check --fix"
    uv run ruff check --fix
    echo "  → ruff format"
    uv run ruff format
else
    echo "  → ruff check"
    uv run ruff check
    echo "  → ruff format --check"
    uv run ruff format --check
fi
step_status "lint" pass

[[ "$LEVEL" == "lint" ]] && { echo ""; echo "=== PASS ==="; exit 0; }

# --- Full (pytest) ---
echo ""
echo "[2/2] tests"
PYTEST_ARGS=(-v)
if [[ "${CI:-}" == "true" ]]; then
    PYTEST_ARGS+=(-m "not local")
    echo "  → pytest -m 'not local'  (CI: skipping local-infrastructure tests)"
else
    echo "  → pytest"
fi
mkdir -p .pytest-logs
LOG=".pytest-logs/$(date +%Y%m%d-%H%M%S)-gate.log"
uv run pytest "${PYTEST_ARGS[@]}" 2>&1 | tee "$LOG"
PYTEST_EXIT=${PIPESTATUS[0]}

echo ""
if [[ $PYTEST_EXIT -eq 0 ]]; then
    SUMMARY=$(grep -E "^=+ [0-9]+ passed" "$LOG" | tail -1 || echo "see $LOG")
    step_status "tests — $SUMMARY" pass
    echo ""
    echo "=== PASS ==="
else
    SUMMARY=$(grep -E "^=+ [0-9]+ failed|FAILED " "$LOG" | tail -5 || echo "see $LOG")
    step_status "tests" fail
    echo ""
    echo "$SUMMARY"
    echo ""
    echo "=== FAIL — log: $LOG ==="
    exit 1
fi
