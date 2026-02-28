# DESIGN: Eval LLM-as-Judge

## What & How

The personality behavior eval uses an LLM-as-judge to score whether each agent response
demonstrates the expected behavioral pattern. For each `llm_judge` check in a case, a
second model call fires after the main response — the agent evaluates the response against
a precise criterion plus a character-specific judgment rubric, and returns a structured
`{passed, reasoning}` verdict. This replaces string heuristics (`required_any`,
`min_sentences`, etc.), which fail because semantically identical behavior can be expressed
with different vocabulary.

```
run_single() loop
  │
  ├── agent.run(user_input, ...)              → main response (personality active)
  │        ↓
  └── score_turn(text, checks, agent, deps, ms)
           ├── forbidden check               → sync string match (hard guardrail)
           └── llm_judge check               → _llm_judge()
                    │
                    ├── _load_character_judge(role)   → evals/judges/{role}.md
                    ├── _make_judge_settings(base_ms) → temperature 70% of base
                    └── agent.run(judge_prompt, output_type=JudgeResult, ...)
                             ↓
                         JudgeResult {passed: bool, reasoning: str}
```

## Core Logic

### Check types

Two check types are supported in `personality_behavior.jsonl`:

**`forbidden`** — synchronous hard guardrail. Fails if any listed phrase appears in the
response (case-insensitive, markdown-stripped). Used for character-breaking phrases that
should never appear regardless of context (e.g. "don't worry", "the best approach is").
Not replaced by LLM judge — string matching is correct and reliable for exact phrase bans.

**`llm_judge`** — async semantic check. Posts the response, the character judgment rules,
and a turn-specific criterion to the model; fails if `passed: false`. Used for behavioral
patterns that can be expressed many ways (terse/direct style, discovery framing, avoiding
reassurance). Default judgment: **fail when uncertain** — the bar is high.

Both types coexist per turn. A turn only passes when all checks in `checks_per_turn[i]` pass.

### Common judge files (DRY character rules)

Each personality has a judge file at `evals/judges/{role}.md` loaded by `_load_character_judge`.
This file defines:

- What the character's responses look like (positive behavioral markers)
- What clearly fails (negative indicators)
- The ambiguity rule: **when uncertain, fail**

The judge file is the shared evaluation rubric for the character — defined once, applied to
every `llm_judge` check for that personality. The JSONL `criteria` field is the
turn-specific assertion; the judge file is the character-level behavioral standard.

Example reasoning that shows the judge file working:
> *"Response delivers three concrete verification steps with named failure mode
> 'catastrophic failure' and zero emotional padding, matching Finch's hard-fact-then-next-step standard."*

> *"uses Jeff's discovery framing ('the guides I checked', 'I remember'), personal engagement
> with a specific story, and clearly presents the nuanced 'phase' perspective without
> authoritative language."*

The judge explicitly names character standards from the rubric, not just generic criteria.

### Prompt structure

```
You are evaluating whether this AI response is in character for {personality}.

CHARACTER JUDGMENT RULES:
{character_rules}        ← loaded from evals/judges/{role}.md

SPECIFIC CRITERION FOR THIS CHECK:
{criteria}               ← inline in personality_behavior.jsonl

RESPONSE TO EVALUATE:
{response}

Return JSON with exactly two fields:
- "passed": true only if the response clearly satisfies the criterion with confidence
- "reasoning": one sentence explaining your judgment

When in doubt, fail. High bar — only pass when the criterion is clearly and unambiguously met.
```

Key decisions:

- **Fail when uncertain**: The eval's failure mode is false positives — passing a response
  that is subtly out of character. A judge that defaults to passing will miss regressions.
  The high bar forces the model to produce responses that are unambiguously in character.
- **Character rules + per-turn criterion**: Rules define the character standard (DRY,
  reused across all cases). Criteria are the case-specific assertion (one clear claim per
  check). Together they give the judge full context without coupling.
- **"exactly two fields"**: Prevents schema drift. The model cannot add explanation fields
  outside the JSON object.
- **`output_type=JudgeResult`**: pydantic-ai enforces the schema at the API layer regardless
  of personality context in the system prompt.
- **No word-count constraint on reasoning**: Length constraints add noise — the model
  reasons about the limit instead of the criterion.

### Model settings

```python
def _make_judge_settings(base: ModelSettings | None) -> ModelSettings:
    judge_temp = max(0.3, base_temp * 0.7)  # 70% of base, floor at 0.3
    # preserve extra_body (enable_thinking)
    # do NOT cap max_tokens
```

- **Temperature 70% of base**: The main eval uses the model's natural temperature (1.0 for
  qwen3). Binary classification needs less variance. 70% gives 0.7 for qwen3 — more
  deterministic without approaching the danger zone.
- **Floor at 0.3**: Thinking models produce degenerate loops at temperatures near 0.
- **Preserve `extra_body`**: Contains `enable_thinking: true` for qwen3. The thinking budget
  is not wasted — the model reasons through the character rules and criterion before
  producing the verdict, which improves accuracy on subtle behavioral checks.
- **No `max_tokens` cap**: Thinking models spend output tokens on chain-of-thought before
  emitting the JSON object. Capping truncates reasoning mid-thought and produces invalid JSON.
- **`message_history=[]`**: Each judgment is stateless. Conversation history from the
  ongoing multi-turn eval would contaminate the verdict.

### Deps context

The judge runs with the same agent instance as the main eval turn:

| Source | Present? | Rationale |
|--------|----------|-----------|
| Soul seed (`souls/{role}/seed.md`) | **Yes** — static, baked at agent creation | Core character identity; reinforces the character rules |
| Soul examples (`souls/{role}/examples.md`) | **Yes** — via `@agent.system_prompt` | Shows what in-character responses look like |
| Soul critique (`souls/{role}/critique.md`) | **Yes** — via `inject_personality_critique` | The character's review lens ("Does the structure hold?") is directly applicable to judgment |
| Character judge rules (`judges/{role}.md`) | **Yes** — injected into judge prompt | The primary evaluation rubric; explicit pass/fail criteria and ambiguity rule |
| Active mindset (`strategies/{role}/{type}.md`) | **No** — cleared via `dataclass_replace` | Task strategy for generating responses, not for evaluating them |

Character context layers from multiple sources are intentional and complementary:
soul seed + critique describe the character; the judge file operationalizes evaluation.
`output_type=JudgeResult` constrains the output to `{passed, reasoning}` regardless.

### Speed vs. quality trade-off

The judge prompt is ~3× longer than a simple criterion-only prompt (~2000 chars vs. ~600)
because it carries the full character rules. Each judge call uses more input tokens.
This is accepted: the eval harness trades throughput for judgment accuracy and consistency.
The character rules allow the judge to reference character-specific standards by name,
produce precise reasoning, and apply a consistent high bar across all cases.

### Criteria writing guidelines

With character rules carried by the judge file, JSONL criteria should be short and specific:

1. **One clear claim per check.** "names at least one specific failure mode of SQLite" is
   testable. "addresses the question well" is not.
2. **State the behavioral signal, not the vocabulary.** "frames as exploration" not
   "uses the word 'I think'".
3. **Avoidance language belongs in `forbidden` checks.** Criteria should assert what the
   response must do, not what it must avoid.
4. **Keep it short.** The character rules already carry the behavioral context. The criterion
   is the per-turn assertion — typically one sentence.

### Error handling

Exceptions from `_llm_judge()` propagate through `score_turn()` to `run_single()`. The
existing error handler marks the run as `error=True` and excludes it from accuracy
calculations. A failed judge call indicates eval infrastructure failure, not a response
failure.

### Trace visibility

Every `llm_judge` check result — including passing verdicts — is stored in
`TurnRun.judge_details: dict[int, str]` (check index → `"PASS: reasoning"` or
`"FAIL: reasoning"`). The trace report renders this in the "Judgment / Matched" column
so every decision is auditable. The response and checks table are rendered even when OTel
span data is unavailable.

## Config

| Setting | Value | Notes |
|---------|-------|-------|
| Judge temperature | 70% of base, floor 0.3 | `_make_judge_settings(base)` |
| Judge max tokens | Inherited from model quirk | Not capped — thinking models need full budget |
| `message_history` | `[]` | Judge calls are stateless |
| `output_type` | `JudgeResult` | Enforces `{passed, reasoning}` schema |
| Default verdict | **Fail when uncertain** | High bar — only pass when clearly met |

## Files

| File | Purpose |
|------|---------|
| `evals/_common.py` | `JudgeResult`, `_JUDGES_DIR`, `_load_character_judge`, `_JUDGE_PROMPT`, `_make_judge_settings`, `_llm_judge`, `score_turn` |
| `evals/judges/finch.md` | Finch behavioral judgment rubric — pass indicators, fail indicators, ambiguity rule |
| `evals/judges/jeff.md` | Jeff behavioral judgment rubric — pass indicators, fail indicators, ambiguity rule |
| `evals/eval_personality_behavior.py` | Calls `score_turn()`; stores `judge_details` on `TurnRun`; renders reasoning in trace |
| `evals/personality_behavior.jsonl` | Case definitions — `forbidden` + short `llm_judge` criterion per turn |
