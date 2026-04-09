#!/usr/bin/env bash
# Install git hooks for the co-cli repository.
# Run once after cloning: bash scripts/install-hooks.sh

set -euo pipefail

HOOKS_DIR="$(git rev-parse --show-toplevel)/.git/hooks"

cat > "$HOOKS_DIR/pre-commit" << 'HOOK'
#!/usr/bin/env bash
# Pre-commit hook: ruff lint + format check
# Blocks commit if any violation is found.

set -euo pipefail

# Skip if no Python files are staged
if ! git diff --cached --name-only | grep -q '\.py$'; then
    echo "pre-commit: no Python files staged, skipping lint"
    exit 0
fi

echo "pre-commit: running ruff check..."
uv run ruff check co_cli/

echo "pre-commit: running ruff format check..."
uv run ruff format --check co_cli/

echo "pre-commit: lint OK"
HOOK

chmod +x "$HOOKS_DIR/pre-commit"
echo "Installed pre-commit hook at $HOOKS_DIR/pre-commit"
