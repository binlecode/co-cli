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

cd "$(git rev-parse --show-toplevel)"
scripts/quality-gate.sh lint
HOOK

chmod +x "$HOOKS_DIR/pre-commit"
echo "Installed pre-commit hook at $HOOKS_DIR/pre-commit"
