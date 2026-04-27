# Testing Rules

> **These rules are enforced repository policy, not guidance.** Any test or test change that violates them must be fixed or removed before regression testing or merge.

## Evals (`evals/`)

- **Evals are separate from tests**: evals run as standalone programs (`uv run python evals/eval_<name>.py`), not pytest. Pass/fail gates live inside the runner. Shared helpers belong in `evals/_*.py`, not in `co_cli/`.
- **Evals run against the real configured system**: never override config with `_ENV_DEFAULTS`, `os.environ`, or fallback defaults. If a prerequisite is missing, skip gracefully.
- **Evals never create their own model or agent settings**: all LLM call parameters (model, temperature, context window, reasoning mode) must come from the project's config and factory functions — never hardcoded inline. Use the production code paths as-is; do not substitute simplified settings to "speed up" or "simplify" the eval.
- **Evals must seek corners**: every eval must include at least one failure mode, degradation path, or boundary condition.

## Tests (`tests/`)

- **Only pytest files in `tests/`**: `test_*.py` or `*_test.py`, using `pytest` + `pytest-asyncio`. Non-test scripts go in `scripts/`, evaluations in `evals/`.
- **Real dependencies only — no fakes**: never use `monkeypatch`, `unittest.mock`, `pytest-mock`, or hand-assembled domain objects that bypass production code paths. Use real `CoDeps` with real services, real SQLite, real filesystem, real FTS5. If a behavior cannot be tested without fakes, the production API is wrong — fix the API. `conftest.py` must be limited to neutral pytest plumbing (e.g. session-scoped markers, asyncio mode) — never shadow config or inject substitutes.
- **Behavior over structure**: tests must drive a production code path and assert on observable outcomes — return values, persisted state, emitted events, raised errors, side effects. Do not assert on facts Python or the import system already enforce: file/directory layout, module importability, class or attribute presence, registration tables, type annotations, or docstrings. If a test would still pass after gutting the function body to `pass` (or `return None`), it is structural — rewrite it to exercise behavior or delete it. The test name should describe *what the code does*, not *how it is arranged*.
- **IO-bound timeouts**: wrap each individual `await` to external services (LLMs, network, subprocess) with `asyncio.timeout(N)` — including warmup and preflight awaits. Never wrap multiple sequential awaits in one block. Import constants from the test timeouts module — never hardcode inline. Never increase a timeout to make a test pass — a timeout violation means wrong role, wrong agent context, or wrong model config. Diagnose the root cause, fix the test or config.
- **Suite hygiene**: every test must target a real failure mode — ask "if deleted, would a regression go undetected?" Tests must pass or fail (no skips except credential-gated external integrations via `pytest.mark.skipif`). Remove or update stale tests when changing public APIs — do not skip them. Any policy violation blocks the full run. `pyproject.toml` enforces `-x --durations=0`. Known anti-patterns that pass the deletion question but add no coverage:
  - *Fixture not wired*: `tmp_path` (or any injected fixture) in the signature but never passed to a production function — assertion trivially passes.
  - *Duplicate with trivial delta*: two tests dispatch the same function/command and assert the same invariant; the extra test adds only a trivially-true assertion (e.g. `result.flag is False` where False is the default).
  - *Truthy-only assertion*: `assert result.version` instead of `assert re.fullmatch(r"\d+\.\d+\.\d+", result.version)` — passes even if the value is wrong.
  - *Subsumed file*: an entire test file whose every test is a strict subset of tests in another file covering the same module.
- **Test data isolation**: use `tmp_path` for all filesystem writes. For shared stores, use `test-` prefix identifiers and delete in `try/finally` — cleanup failure must fail the test.
- **Scope pytest during implementation**: run only affected test files during dev (`uv run pytest tests/test_foo.py`). Run the full suite only before shipping. Never dismiss a failure as flaky — stop, diagnose, then fix.
- **Production config only — no overrides**: do not pass `model=` or `model_settings=` to `agent.run()` — use the production orchestration path or invoke the agent with no override. Do not strip personality in tests. Use non-thinking model settings for tool-calling, signal-detection, and orchestration tests. Cache module-level agents rather than rebuilding per call.
- **Never copy inline logic into tests**: do not replicate display formatting or string construction in assertions.
- **Google credentials**: never configure or inject — they resolve automatically via settings, `~/.co-cli/google_token.json`, or ADC.
- **No pytest markers**: do not add markers (e.g. `integration`, `slow`) unless explicitly requested.
