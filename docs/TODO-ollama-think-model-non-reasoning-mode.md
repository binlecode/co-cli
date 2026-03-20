# TODO: Normalize Non-Reason Calls Around `qwen3.5:35b-a3b-think`

## Current Understanding
The deployed Ollama path should be treated as single-model:

- hosted model: `qwen3.5:35b-a3b-think`
- transport: Ollama OpenAI-compatible `/v1/chat/completions`
- non-reason toggle: `extra_body={"reasoning_effort": "none"}`

The earlier `think` vs `instruct` framing was misleading for this repo. The local Ollama host keeps only `qwen3.5:35b-a3b-think` resident, and warming `qwen3.5:35b-a3b-instruct` does not result in a persistent second resident model in `/api/ps`.

## Confirmed Low-Level Behavior
Direct raw calls against `http://localhost:11434/v1/chat/completions` show:

- same model: `qwen3.5:35b-a3b-think`
- same prompt: strict JSON extraction
- same decoding knobs except for `reasoning_effort`

Observed results:

1. Default `qwen3.5:35b-a3b-think`
   - latency: about `6.09s`
   - response shape: empty `content`, populated `reasoning`, `finish_reason="length"`
   - effect: the model spends the budget on reasoning and may not emit a final answer

2. `qwen3.5:35b-a3b-think` with `reasoning_effort="none"`
   - latency: about `0.66s`
   - response shape: direct final `content`, no reasoning field in the observed response
   - effect: the model behaves like a non-reason path on the same resident weights

Concrete raw output seen on the non-reason path:

```json
{"city": "Tokyo", "country": "United States"}
```

## What Changed In Repo
- [`co_cli/config.py`](/Users/binle/workspace_genai/co-cli/co_cli/config.py) now uses `qwen3.5:35b-a3b-think` plus `reasoning_effort: "none"` for summarization, analysis, and research over `ollama-openai`.
- [`evals/eval_ollama_openai_noreason_equivalence.py`](/Users/binle/workspace_genai/co-cli/evals/eval_ollama_openai_noreason_equivalence.py) now validates the real deployment shape:
  - `qwen3.5:35b-a3b-think` default
  - versus `qwen3.5:35b-a3b-think` with `reasoning_effort="none"`
  - with explicit per-call timing and `60s` per-model-call timeout
- [`ollama/Modelfile.qwen3.5-35b-a3b-instruct`](/Users/binle/workspace_genai/co-cli/ollama/Modelfile.qwen3.5-35b-a3b-instruct) was deleted to reduce confusion around a second Ollama model path.

## Open Questions
1. Do we want to remove `ollama-native` entirely, or keep it temporarily for compatibility while standardizing on `ollama-openai`?
2. Should the eval be renamed now that it is no longer an instruct-equivalence check?
3. Do we want to keep historical instruct benchmark artifacts as-is, or archive/retitle them to make their historical status explicit?

## Next Steps
1. Decide whether `ollama-native` is still needed anywhere real, then either delete it or explicitly scope its remaining use.
2. Keep validating non-reason behavior on the resident `qwen3.5:35b-a3b-think` model only; do not reintroduce `-instruct` as a comparison baseline unless there is a separate deployment reason.
3. Clean or archive remaining historical instruct references if they are likely to be mistaken for live configuration.
