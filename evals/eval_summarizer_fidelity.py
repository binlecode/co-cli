"""Summarizer fidelity A/B harness — does the configured local model honor the
13-section compaction contract in ``_SUMMARIZE_PROMPT``?

Throwaway-grade, eval-layer only. Drives REAL compactions on the configured
Ollama model over three synthesized transcripts whose ground truth we control,
then scores deterministic compliance properties as **pass-rates over N samples**
(the model is stochastic — a single sample makes any delta indistinguishable
from noise).

A/B design (plan ``summarizer-fidelity-measure``):
- The summarizer call is reconstructed here from the production assembly
  functions (``serialize_messages`` + the ``_build_summarizer_prompt`` body +
  ``llm_call``), with the prompt **template** passed in as a parameter. The live
  ``_SUMMARIZE_PROMPT`` is variant A / baseline; a TASK-2 revision would be
  variant B. This is option (a): no prompt param added to ``summarize_messages``
  (that would be test-driven API), no monkeypatch of the module constant.
- ``_summarize_with_template`` mirrors ``summarize_messages`` (``summarization.py``)
  line-for-line — same system prompt, trusted prior-summary slot, budget, and
  ``cap_output_tokens`` — so the A/B measures the shipped path, not a strawman.

Run: ``uv run python evals/eval_summarizer_fidelity.py``
Output: ``evals/_outputs/summarizer-fidelity-<ts>-run.jsonl``
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from evals._deps import make_eval_deps
from evals._ollama import ensure_ollama_warm
from evals._timeouts import LLM_COMPACTION_SUMMARY_TIMEOUT_SECS
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from co_cli.config.llm import cap_output_tokens
from co_cli.config.observability import redact_text
from co_cli.config.tuning import (
    SUMMARY_CAP_OVERSHOOT_RATIO,
    SUMMARY_NOREASON_CEILING_FALLBACK,
)
from co_cli.context.summarization import (
    _PRIOR_SUMMARY_CLAUSE,
    _SUMMARIZE_PROMPT,
    _SUMMARIZER_SYSTEM_PROMPT,
    _length_priority_tail,
    resolve_summary_budget,
    serialize_messages,
)
from co_cli.deps import CoDeps
from co_cli.llm.call import llm_call

# Samples per transcript. The configured model is stochastic, so each scored
# property is a pass-rate over N generations, not a single pass/fail. 5 is the
# plan's floor (N>=5) — enough to separate a real compliance gap from one-off
# sampling noise without an intractable real-LLM run.
SAMPLES_PER_TRANSCRIPT = 5

# A property "passes" on the configured model when at least this fraction of the
# N samples satisfy it. Below the threshold is a measured failure mode that flips
# the run verdict to REVISE and is enumerated in the record. 0.8 = 4/5.
PASS_THRESHOLD = 0.8

# The prompt variant being measured this run. The live ``_SUMMARIZE_PROMPT`` is
# what the harness runs; this label records which contract that constant holds.
# "A" = original omit-empty/13-section baseline (frozen in the first JSONL run);
# "B" = the keep-every-section revision (TASK-2). Bump when the constant changes.
VARIANT_LABEL = "B"


# ---------------------------------------------------------------------------
# Production-mirrored summarizer call (option a — parameterized template)
# ---------------------------------------------------------------------------


def _assemble_task_prompt(
    template: str,
    budget: int,
    *,
    prior_summary: str | None,
) -> str:
    """Mirror ``_build_summarizer_prompt`` assembly with ``template`` swapped in.

    No focus / context / personality in this harness (personality default is None;
    compaction summarizer runs personality-off) — so the assembly reduces to:
    template -> prior-summary clause (iff prior) -> length tail. Byte-identical to
    the production order in ``summarization.py`` for that input shape.
    """
    parts = [template]
    if prior_summary:
        parts.append(_PRIOR_SUMMARY_CLAUSE)
    parts.append(_length_priority_tail(budget))
    return "".join(parts)


async def _summarize_with_template(
    deps: CoDeps,
    messages: list[ModelMessage],
    template: str,
    *,
    prior_summary: str | None = None,
) -> str:
    """Run one real summarizer call, mirroring ``summarize_messages`` exactly.

    The only divergence from production is that the section template is passed in
    (``_SUMMARIZE_PROMPT`` for variant A) instead of read from the module constant
    — the A/B lever.
    """
    budget = resolve_summary_budget(messages)
    base_ceiling = deps.model.settings_noreason.get(
        "max_tokens", SUMMARY_NOREASON_CEILING_FALLBACK
    )
    cap = min(math.ceil(budget * SUMMARY_CAP_OVERSHOOT_RATIO), base_ceiling)
    task_prompt = _assemble_task_prompt(template, budget, prior_summary=prior_summary)
    patterns = deps.config.observability.redact_patterns
    serialized = serialize_messages(messages, patterns)
    settings = cap_output_tokens(deps.model.settings_noreason, cap)
    if prior_summary:
        redacted_prior = redact_text(prior_summary, patterns)
        user_message = (
            "PRIOR SUMMARY (authoritative prior state — fold into a complete refreshed "
            "summary, do not copy unchanged):\n"
            f"{redacted_prior}\n\n"
            f"TURNS TO SUMMARIZE:\n{serialized}"
        )
    else:
        user_message = f"TURNS TO SUMMARIZE:\n{serialized}"
    return await llm_call(
        deps,
        user_message,
        instructions=f"{_SUMMARIZER_SYSTEM_PROMPT}\n\n{task_prompt}",
        model_settings=settings,
    )


# ---------------------------------------------------------------------------
# Deterministic scorers — ground truth comes from the synthesized transcripts
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_TOOL_ANNOTATION_RE = re.compile(r"\[tool:\s*([^\]]+?)\s*\]")

_MANDATORY_SECTIONS = ("Active Task", "Next Step")

# Variant B contract (keep-every-section): every section must be emitted, with
# (none) when empty. This REPLACES variant A's omit-empty SKIP RULE — the property
# measured is inverted, so the harness scores keep_every_section for B where it
# scored skip_rule for A.
_ALL_SECTIONS = (
    "Active Task",
    "Next Step",
    "Goal",
    "Constraints & Preferences",
    "Key Decisions",
    "User Corrections",
    "Errors & Fixes",
    "Completed Actions",
    "In Progress",
    "Remaining Work",
    "Working Set",
    "Pending User Asks",
    "Resolved Questions",
    "Critical Context",
)


def _sections_present(summary: str, required: tuple[str, ...]) -> bool:
    """True when every required ``## <name>`` header appears in the summary."""
    found = {h.strip().lower() for h in _HEADER_RE.findall(summary)}
    return all(name.lower() in found for name in required)


def _quote_is_verbatim(summary: str, expected_quote: str) -> bool:
    """True when the expected user phrase appears byte-verbatim in the summary
    (the drift-anchor mandate — paraphrase fails)."""
    return expected_quote in summary


def _tool_names_grounded(summary: str, actual_tools: set[str]) -> bool:
    """True when every ``[tool: name]`` annotation names a tool actually called —
    no hallucinated names. Empty annotation set fails (the contract requires them
    in ## Completed Actions for a tool-heavy transcript)."""
    annotated = {m.strip().lower() for m in _TOOL_ANNOTATION_RE.findall(summary)}
    if not annotated:
        return False
    return annotated <= {t.lower() for t in actual_tools}


def _all_sections_present(summary: str) -> bool:
    """True when every section in the keep-every-section contract is emitted
    (variant B). ``(none)`` bodies are valid here — the failure is a MISSING
    section, not a placeholder."""
    found = {h.strip().lower() for h in _HEADER_RE.findall(summary)}
    return all(name.lower() in found for name in _ALL_SECTIONS)


def _paths_survive(summary: str, paths: tuple[str, ...]) -> bool:
    """True when every known file path/line ref survives into the summary."""
    return all(p in summary for p in paths)


def _carry_forward_resolved(summary: str, marker_phrase: str) -> bool:
    """True when a previously-pending marker question now appears under
    ## Resolved Questions and NOT under ## Pending User Asks."""
    sections = _split_sections(summary)
    pending = sections.get("pending user asks", "")
    resolved = sections.get("resolved questions", "")
    return marker_phrase in resolved and marker_phrase not in pending


def _split_sections(summary: str) -> dict[str, str]:
    """Map lowercased section name -> body text, for membership checks."""
    out: dict[str, str] = {}
    matches = list(_HEADER_RE.finditer(summary))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(summary)
        out[m.group(1).strip().lower()] = summary[start:end]
    return out


# ---------------------------------------------------------------------------
# Synthesized transcripts (controlled ground truth)
# ---------------------------------------------------------------------------


@dataclass
class Transcript:
    """A synthesized transcript plus the properties it is built to exercise."""

    name: str
    messages: list[ModelMessage]
    prior_summary: str | None = None
    # property key -> callable(summary) -> bool
    scorers: dict[str, object] = field(default_factory=dict)


_CORRECTION = Transcript(
    name="user_correction",
    messages=[
        ModelRequest(
            parts=[
                UserPromptPart(
                    content="Add password hashing to the signup flow. Use bcrypt for the hash."
                )
            ]
        ),
        ModelResponse(
            parts=[TextPart(content="I'll add bcrypt-based hashing to the signup handler.")]
        ),
        ModelRequest(
            parts=[
                UserPromptPart(
                    content="No, use Argon2 not bcrypt — bcrypt's 72-byte truncation is unacceptable here."
                )
            ]
        ),
        ModelResponse(
            parts=[
                TextPart(
                    content="Understood — switching to Argon2id via argon2-cffi for the signup hash."
                )
            ]
        ),
    ],
    scorers={
        "sections_present": lambda s: _sections_present(s, _MANDATORY_SECTIONS),
        "verbatim_quote": lambda s: _quote_is_verbatim(s, "use Argon2 not bcrypt"),
        "keep_every_section": _all_sections_present,
    },
)


_TOOL_HEAVY = Transcript(
    name="tool_heavy",
    messages=[
        ModelRequest(
            parts=[
                UserPromptPart(
                    content="The token check in co_cli/auth.py is inverted — find and fix it."
                )
            ]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="file_read", args={"path": "co_cli/auth.py"}, tool_call_id="t1"
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="file_read",
                    content=(
                        "def is_valid(token):\n"
                        "    payload = decode(token)\n"
                        "    if payload.exp == time.time():\n"
                        "        return True\n"
                        "    return False\n"
                    ),
                    tool_call_id="t1",
                )
            ]
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="file_edit",
                    args={"path": "co_cli/auth.py", "line": 3},
                    tool_call_id="t2",
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="file_edit",
                    content="Edited co_cli/auth.py:3 — changed `==` to `>`.",
                    tool_call_id="t2",
                )
            ]
        ),
        ModelResponse(
            parts=[ToolCallPart(tool_name="shell", args={"cmd": "pytest -x"}, tool_call_id="t3")]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="shell",
                    content="2 passed in 0.41s",
                    tool_call_id="t3",
                )
            ]
        ),
        ModelResponse(
            parts=[
                TextPart(content="Fixed the inverted check at co_cli/auth.py:3 and tests pass.")
            ]
        ),
    ],
    scorers={
        "sections_present": lambda s: _sections_present(s, _MANDATORY_SECTIONS),
        "verbatim_quote": lambda s: _quote_is_verbatim(
            s, "The token check in co_cli/auth.py is inverted"
        ),
        "tool_names_grounded": lambda s: _tool_names_grounded(
            s, {"file_read", "file_edit", "shell"}
        ),
        "keep_every_section": _all_sections_present,
        "paths_survive": lambda s: _paths_survive(s, ("co_cli/auth.py",)),
    },
)


_CARRY_FORWARD = Transcript(
    name="carry_forward",
    prior_summary=(
        "## Active Task\n"
        'User asked: "Wire up the /metrics endpoint and confirm the auth header name."\n\n'
        "## Next Step\n"
        "Implement the /metrics route. Verbatim: 'expose request counts and latency'\n\n"
        "## Goal\n"
        "Add an observability endpoint to the service.\n\n"
        "## Pending User Asks\n"
        "- What auth header name should /metrics expect?\n\n"
        "## Resolved Questions\n"
        "Q: Which port? → A: 8080.\n"
    ),
    messages=[
        ModelRequest(
            parts=[
                UserPromptPart(
                    content="The /metrics endpoint should expect the X-Metrics-Token auth header."
                )
            ]
        ),
        ModelResponse(
            parts=[
                TextPart(
                    content="Got it — /metrics will read the X-Metrics-Token header for auth."
                )
            ]
        ),
    ],
    scorers={
        "sections_present": lambda s: _sections_present(s, _MANDATORY_SECTIONS),
        "verbatim_quote": lambda s: _quote_is_verbatim(s, "X-Metrics-Token"),
        "keep_every_section": _all_sections_present,
        "carry_forward": lambda s: _carry_forward_resolved(s, "auth header"),
    },
)


_TRANSCRIPTS = [_CORRECTION, _TOOL_HEAVY, _CARRY_FORWARD]


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


async def _score_transcript(deps: CoDeps, transcript: Transcript, template: str) -> dict:
    """Generate N summaries for one transcript; return per-property pass-rates.

    A sample that exceeds the production compaction budget
    (``LLM_COMPACTION_SUMMARY_TIMEOUT_SECS``) is a REAL fidelity failure on this
    model — in production that call raises and forces a fallback — so it is
    recorded as an empty summary (fails every property) and the run continues
    rather than aborting. The N-sample pass-rate design exists to absorb exactly
    this stochastic slow-decode tail; one slow sample must not crash the verdict.
    """
    summaries: list[str] = []
    timeouts = 0
    for i in range(SAMPLES_PER_TRANSCRIPT):
        try:
            async with asyncio.timeout(LLM_COMPACTION_SUMMARY_TIMEOUT_SECS):
                summary = await _summarize_with_template(
                    deps, transcript.messages, template, prior_summary=transcript.prior_summary
                )
            summaries.append(summary)
            print(
                f"  [{transcript.name}] sample {i + 1}/{SAMPLES_PER_TRANSCRIPT} ({len(summary)} chars)"
            )
        except TimeoutError:
            timeouts += 1
            summaries.append("")
            print(
                f"  [{transcript.name}] sample {i + 1}/{SAMPLES_PER_TRANSCRIPT} "
                f"TIMEOUT >{LLM_COMPACTION_SUMMARY_TIMEOUT_SECS}s — scored as failed sample"
            )

    rates: dict[str, float] = {}
    for prop, scorer in transcript.scorers.items():
        passes = sum(1 for s in summaries if s and scorer(s))
        rates[prop] = passes / len(summaries)
    return {
        "transcript": transcript.name,
        "samples": len(summaries),
        "timeouts": timeouts,
        "pass_rates": rates,
        "summaries": summaries,
    }


def _verdict(per_transcript: list[dict]) -> dict:
    """COMPLIANT iff every measured property clears PASS_THRESHOLD; else REVISE
    with the failing (transcript, property, rate) enumerated."""
    failures = []
    for entry in per_transcript:
        for prop, rate in entry["pass_rates"].items():
            if rate < PASS_THRESHOLD:
                failures.append(
                    {"transcript": entry["transcript"], "property": prop, "pass_rate": rate}
                )
    return {
        "verdict": "REVISE" if failures else "COMPLIANT",
        "threshold": PASS_THRESHOLD,
        "failure_modes": failures,
    }


async def main() -> None:
    await ensure_ollama_warm()
    deps, _agent, _frontend, stack = await make_eval_deps()
    try:
        print(
            f"Summarizer fidelity A/B — variant {VARIANT_LABEL} (live _SUMMARIZE_PROMPT), "
            f"{SAMPLES_PER_TRANSCRIPT} samples/transcript"
        )
        per_transcript = []
        for transcript in _TRANSCRIPTS:
            print(f"[{transcript.name}] generating...")
            per_transcript.append(await _score_transcript(deps, transcript, _SUMMARIZE_PROMPT))

        verdict = _verdict(per_transcript)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path(__file__).parent / "_outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"summarizer-fidelity-{ts}-run.jsonl"
        with out_path.open("w") as fh:
            fh.write(
                json.dumps(
                    {
                        "record": "meta",
                        "variant": VARIANT_LABEL,
                        "model": deps.model.model.model_name,
                    }
                )
                + "\n"
            )
            for entry in per_transcript:
                fh.write(json.dumps({"record": "transcript", **entry}) + "\n")
            fh.write(json.dumps({"record": "verdict", **verdict}) + "\n")

        print("\n=== VERDICT ===")
        print(f"{verdict['verdict']} (threshold {PASS_THRESHOLD})")
        for entry in per_transcript:
            print(f"  {entry['transcript']}: {entry['pass_rates']}")
        if verdict["failure_modes"]:
            print("  failure modes:")
            for fm in verdict["failure_modes"]:
                print(f"    - {fm['transcript']}.{fm['property']} = {fm['pass_rate']:.2f}")
        print(f"\nrun → {out_path}")
    finally:
        await stack.aclose()


if __name__ == "__main__":
    asyncio.run(main())
