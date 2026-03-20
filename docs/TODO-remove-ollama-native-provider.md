# TODO: Remove `ollama-native` Provider and Standardize on `ollama-openai`

**Task type:** `code-refactor`

## Context

Co currently supports three top-level LLM provider modes:
- `ollama-openai`
- `ollama-native`
- `gemini`

The repo also supports a per-role `ModelEntry.provider` override, and uses that
to force the `summarization` role onto `ollama-native` even when the session
provider is `ollama-openai`.

Current source evidence:

- [evals/eval_ollama_native.py](/Users/binle/workspace_genai/co-cli/evals/eval_ollama_native.py) explicitly asserts:
  - `think=False` through Ollama `/api/chat` produces non-empty content
  - `ModelRegistry` must build `OllamaNativeModel` for the summarization role
  - if this breaks, summarization falls back to the OpenAI-compatible layer "where `think=False` is broken and content is always empty"
- [tests/test_ollama_native.py](/Users/binle/workspace_genai/co-cli/tests/test_ollama_native.py) keeps regression coverage for `OllamaNativeModel`
- [co_cli/config.py](/Users/binle/workspace_genai/co-cli/co_cli/config.py) injects `DEFAULT_OLLAMA_SUMMARIZATION_MODEL` with `provider: "ollama-native"` and `api_params: {"think": False}`
- [scripts/validate_ollama_models.py](/Users/binle/workspace_genai/co-cli/scripts/validate_ollama_models.py) already tracks dedicated non-thinking Ollama model variants such as:
  - `qwen3.5:35b-a3b-q4_k_m-nothink`
  - `qwen3.5:35b-a3b-q4_k_m-summarize`
  - `qwen3.5:35b-a3b-q4_k_m-research`

Conclusion from current repo state:

- The repo currently contains proof that **`ollama-native` works for non-thinking summarization**
- The repo does **not** currently contain proof that **`ollama-openai` can safely replace it** for Qwen 3.5 think-model summarization
- Therefore removal must be paired with a migration to a dedicated non-thinking Ollama model over the `ollama-openai` provider, or with a fresh eval proving the OpenAI-compatible path is now correct

**Workflow hygiene:** No orphaned DELIVERY file found for this scope. No existing TODO file for this exact cleanup scope.

## Problem & Outcome

**Problem:** `ollama-native` adds a second Ollama transport path, provider-specific branching in config/model factory, separate tests/evals, and documentation complexity. The current provider surface is larger than needed.

**Desired outcome:** `co` keeps exactly two top-level LLM providers:
- `ollama-openai`
- `gemini`

and removes:
- `ollama-native` as a session provider
- per-role `provider="ollama-native"` overrides
- `OllamaNativeModel` and its dedicated eval/test path

without regressing:
- `/compact`
- inline history compaction
- approval-resume turns using summarization role
- any non-thinking role that currently depends on `think=False`

## Non-Negotiable Constraint

Do **not** remove `ollama-native` until one replacement path is proven in-repo:

1. `ollama-openai` + dedicated non-thinking Ollama model tag works for summarization, or
2. `ollama-openai` + request-level config demonstrably suppresses reasoning and returns non-empty content for Qwen 3.5 think models

Today, the repo evidence supports option 1 more strongly than option 2.

## Scope

**In scope:**
- Remove `ollama-native` from config validation and defaults
- Remove per-role `provider` override usage for Ollama transport selection
- Remove `OllamaNativeModel` and native `/api/chat` branch from model factory
- Migrate summarization/research/other non-thinking roles to `ollama-openai`-compatible model entries
- Update evals, tests, validation scripts, and DESIGN docs

**Out of scope:**
- Removing `gemini`
- Redesigning role model architecture
- Changing non-Ollama provider behavior

## High-Level Design

```
Before
  llm_provider = ollama-openai | ollama-native | gemini
  role_models[*].provider can override to ollama-native
  summarization defaults to think-model + think=False via native /api/chat

After
  llm_provider = ollama-openai | gemini
  role_models[*].provider override removed or narrowed away from ollama-native
  summarization/research/non-thinking roles use explicit non-thinking model tags
  all Ollama traffic goes through one transport: OpenAI-compatible endpoint
```

Preferred migration target:

- reasoning: keep `qwen3.5:35b-a3b-think`
- summarization: move to a non-thinking Ollama tag already aligned with repo tooling
- research/analysis: keep instruct/non-thinking tags only

This path is cleaner than betting on transport-specific `think=False` behavior.

## Implementation Plan

### TASK-0 — Prove the replacement path before deletion
```
files:
  - evals/eval_ollama_native.py
  - scripts/validate_ollama_models.py
  - docs/REPORT-*.md (new if benchmarking/eval is run)
done_when: |
  A repo-tracked eval proves one supported replacement for current summarization behavior:
  either:
    A. ollama-openai + dedicated non-thinking model returns non-empty summarization content
  or:
    B. ollama-openai + request settings suppresses thinking and returns non-empty content.

  If A is chosen, the chosen replacement model is validated by script or eval and documented.
  If B is chosen, the old eval_ollama_native assertions are replaced by equivalent ollama-openai assertions.
```

### TASK-1 — Redefine Ollama defaults to use one transport
```
files:
  - co_cli/config.py
prerequisites: [TASK-0]
done_when: |
  DEFAULT_OLLAMA_SUMMARIZATION_MODEL no longer sets provider="ollama-native".
  No default role model injects provider="ollama-native".
  fill_from_env() accepts only "ollama-openai" and "gemini" as top-level providers.
  The comment block above DEFAULT_OLLAMA_SUMMARIZATION_MODEL no longer claims /api/chat native routing is required.
```

### TASK-2 — Remove native provider override from model entries
```
files:
  - co_cli/config.py
  - tests/test_config.py
prerequisites: [TASK-1]
done_when: |
  ModelEntry provider semantics no longer advertise ollama-native transport override.
  Config validation rejects llm_provider="ollama-native".
  Tests asserting ollama-native acceptance are removed or rewritten to assert rejection.
```

### TASK-3 — Delete `OllamaNativeModel` and native build path
```
files:
  - co_cli/_model_factory.py
prerequisites: [TASK-0, TASK-2]
done_when: |
  OllamaNativeModel class is removed.
  build_model() has no "ollama-native" branch.
  build_model() return type contains only OpenAIChatModel or GoogleModel.
  No code in the repo imports OllamaNativeModel.
```

### TASK-4 — Migrate summarization and non-thinking role behavior
```
files:
  - co_cli/config.py
  - co_cli/context/_history.py
  - tests/test_history.py
  - tests/test_commands.py
prerequisites: [TASK-0, TASK-3]
done_when: |
  /compact and history compaction use an ollama-openai-backed summarization model path.
  Approval-resume turns still work with the summarization role configured.
  No role depends on transport-level think=False through ollama-native.
```

### TASK-5 — Remove native-only evals/tests and replace with single-path coverage
```
files:
  - evals/eval_ollama_native.py
  - tests/test_ollama_native.py
  - tests/test_commands.py
  - tests/test_history.py
prerequisites: [TASK-3, TASK-4]
done_when: |
  Native-only test/eval files are deleted or renamed to transport-agnostic coverage.
  Replacement tests cover the actual supported ollama-openai summarization path.
  No test name or docstring claims ollama-native is required.
```

### TASK-6 — Update scripts and model validation assumptions
```
files:
  - scripts/validate_ollama_models.py
  - scripts/warmup_ollama.py
prerequisites: [TASK-0, TASK-4]
done_when: |
  Validation/warmup scripts reflect the supported non-thinking model strategy.
  Script comments no longer imply a native transport requirement.
  If summarize/nothink tags are now the official path, script role labels and comments match that reality.
```

### TASK-7 — Update all DESIGN docs and references
```
files:
  - docs/DESIGN-llm-models.md
  - docs/DESIGN-system.md
  - docs/DESIGN-index.md
  - docs/DESIGN-core-loop.md
  - docs/DESIGN-system-bootstrap.md
  - co_cli/prompts/__init__.py
  - co_cli/prompts/model_quirks/_loader.py
prerequisites: [TASK-1, TASK-3, TASK-4]
done_when: |
  No DESIGN doc lists ollama-native as a supported provider.
  Config tables list only ollama-openai and gemini.
  Provider-specific examples no longer use provider="ollama-native".
  Prompt/model-quirk docs no longer mention ollama-native as a first-class transport.
```

### TASK-8 — Final inverse-coverage cleanup
```
files:
  - repo-wide
prerequisites: [TASK-1, TASK-7]
done_when: |
  rg -n "ollama-native|OllamaNativeModel" co_cli tests docs scripts evals returns no live references
  except historical changelog/research material if intentionally preserved.
```

## Testing

Required validation before merge:

1. Config validation
   `uv run pytest tests/test_config.py`

2. Summarization and command flows
   `uv run pytest tests/test_history.py tests/test_commands.py`

3. Model/provider health
   `uv run pytest tests/test_model_check.py`

4. Replacement-path eval
   live eval proving the supported non-thinking Ollama path works for summarization

5. Repo grep audit
   confirm no production references to `ollama-native` remain

All pytest runs must continue to be logged under `.pytest-logs/`.

## Risks

- Highest risk: silent regression in `/compact` where summaries become empty or low quality
- Config migration risk: existing user/project settings may still specify `llm_provider: "ollama-native"`
- Documentation drift risk: this provider is referenced across multiple DESIGN docs and tests
- Benchmark drift risk: current comments assume no model-swap eviction by reusing the think weights with `think=False`; moving to a dedicated non-thinking tag changes that tradeoff

## Open Questions

1. Which explicit non-thinking Ollama tag should become the supported summarization default?
   Candidates already present in repo tooling:
   - `qwen3.5:35b-a3b-q4_k_m-nothink`
   - `qwen3.5:35b-a3b-q4_k_m-summarize`

2. Should `ModelEntry.provider` be removed entirely, or kept only for future non-Ollama per-role overrides?

3. Do we need a one-time compatibility shim that maps existing `ollama-native` settings to `ollama-openai` plus replacement role defaults, or is hard rejection acceptable?

## Recommendation

Do this as a two-phase change:

1. Prove and switch summarization to a single supported `ollama-openai` path using an explicit non-thinking model tag.
2. Only then remove `ollama-native` code, config acceptance, tests, and docs.

Anything faster risks breaking compaction, and the current repo evidence says that risk is real.
