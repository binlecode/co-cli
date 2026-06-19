"""Rule-compliance ablation harness — do co's load-bearing behavioral rules
actually steer the configured local model, or does the model behave the same
with them removed?

Throwaway-grade, eval-layer only. Drives the REAL agent (full orchestrator loop
with tools) on the configured Ollama model under two prompt arms per probe:

- **full**    — the production rules block (all of ``co_cli/context/rules/*.md``)
- **ablated** — the same block with ONE target ``##`` SECTION SPAN removed

This is the section-level successor to the original file-level harness. The
file-level pass validated whole rules ``03`` and ``07`` (one behavior slice
each); this pass drops a single ``##`` section at a time so the verdict is
attributable to that one paragraph of prose, not the whole file.

Two deliverables (plan ``behavioral-rules-audit`` TASK-2):

(a) **Section-observability inventory** — every ``##`` section across all 7 rules
    (28 total, after the ``04 ## Memory`` stub and the C3/C5/C6 merges in the
    behavioral-rules-consolidation-cleanup plan)
    classified OBSERVABLE (maps to a tool-call signal — names the
    target tool) or OUT-OF-REACH (steers response content/tone — no tool-call
    signal). Sections sharing one target tool are one **distinguishable signal**
    (they cannot be ablation-scored independently). Produced from inspection,
    NOT from eval budget. Run ``--inventory`` to emit it without any LLM calls.

(b) **Ablation run over the OBSERVABLE subset** — removes one section span at a
    time and records a per-section fire-rate verdict: STEERS / DEAD-WEIGHT /
    NON-DISCRIMINATING. Implicit discriminating probes only (commanding the
    behavior saturates both arms → NON-DISCRIMINATING).

The model is stochastic, so each arm runs N independent single-turn samples and
the scored signal is a **fire-rate** (fraction of samples where the section's
target behavior fired), not a single pass/fail. A section STEERS when its
presence raises the fire-rate by at least ``STEER_DELTA`` over the ablated arm.
DEAD-WEIGHT means no such delta — the model behaves the same with or without the
section, so the prose is not steering and is a consolidation candidate.

Scoring is deterministic (plan: deterministic-first, no LLM judge in the
verdict). One section is ablated at a time, all other content held fixed, so any
delta is attributable to the ablated section and not to a prompt-offset shift.

Run (long-form, ~6 probes at N samples per arm; tail the log, RCA-first on slow calls):
    ``uv run python evals/eval_rule_compliance.py``
Inventory only (no LLM, validates the span parser + emits the 28-section table):
    ``uv run python evals/eval_rule_compliance.py --inventory``
Output: ``evals/_outputs/rule-compliance-<ts>-run.jsonl``
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from evals._deps import make_eval_deps
from evals._fixtures import load_fixture
from evals._ollama import ensure_ollama_warm
from evals._settings import apply_eval_window
from evals._timeouts import CALL_TIMEOUT_S
from pydantic_ai.messages import ModelResponse, ToolCallPart

from co_cli.agent.build import build_orchestrator
from co_cli.agent.orchestrator import ORCHESTRATOR_SPEC
from co_cli.agent.spec import OrchestratorSpec
from co_cli.context import assembly
from co_cli.context.assembly import build_rules_block
from co_cli.context.guidance import build_toolset_guidance

# Independent samples per arm for the decisive probes. Full agent turns are heavy
# (each is a real orchestrator loop on the local model), so N is the floor that
# makes a fire-rate more than directional, per plan (N>=20 for decisive
# sections). Fire-rates are quantized to multiples of 1/N; raw rates are always
# recorded so the verdict is transparent regardless of N.
SAMPLES_PER_ARM = 20

# A section STEERS when (full fire-rate minus ablated fire-rate) >= this. 0.5
# means the section's presence must flip at least half the samples toward the
# demanded behavior to count as steering. Below it (including a negative delta)
# is DEAD-WEIGHT — the descriptive prose does not move this model.
STEER_DELTA = 0.5

# Target-tool sets per distinguishable signal. A turn that calls any tool in the
# set has exhibited the section's demanded behavior.
_RECALL_TOOLS = frozenset({"memory_search", "memory_view", "session_search", "session_view"})
_COMPUTE_TOOLS = frozenset({"shell_exec"})
_DISCOVERY_TOOLS = frozenset({"file_read", "file_search", "find", "shell_exec", "shell"})
_TOOL_VIEW = frozenset({"tool_view"})
_TODO_WRITE = frozenset({"todo_write"})
_SKILL_VIEW = frozenset({"skill_view"})

_RULES_DIR = Path(assembly.__file__).parent / "rules"


# ---------------------------------------------------------------------------
# Section span parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """One ``##`` section of a rule file and its exact span text in the assembled
    full rules block.

    ``span_text`` is the substring removed from ``build_rules_block()`` to ablate
    this section. It absorbs exactly one separator (trailing blank line for every
    section except the last in its file, which absorbs its leading blank line) so
    that ``full_block.replace(span_text, "")`` leaves the H1 title and every
    inter-section / inter-file join intact — no doubled blank lines, no dangling
    separators. The last-in-file rule is what keeps an EOF section removal clean.
    """

    rule_stem: str
    title: str
    core: str
    span_text: str
    is_last_in_file: bool


def _parse_file_sections(stem: str, content: str) -> list[Section]:
    """Parse one stripped rule-file's ``##`` sections into spans.

    Splits on ``^## `` boundaries and does NOT require a leading ``# `` H1 (the
    parser is robust to a file that opens directly on a ``## `` section). The H1
    and any preamble before the first ``## `` stay attached to the file, never to
    a span.
    """
    matches = list(re.finditer(r"(?m)^## .+$", content))
    sections: list[Section] = []
    for k, match in enumerate(matches):
        start = match.start()
        end = matches[k + 1].start() if k + 1 < len(matches) else len(content)
        core = content[start:end].rstrip()
        is_last = k == len(matches) - 1
        span_text = "\n\n" + core if is_last else core + "\n\n"
        title = match.group(0)[3:].strip()
        sections.append(
            Section(
                rule_stem=stem,
                title=title,
                core=core,
                span_text=span_text,
                is_last_in_file=is_last,
            )
        )
    return sections


def _all_sections() -> list[Section]:
    """Every ``##`` section across all rule files, in assembled order."""
    sections: list[Section] = []
    for path in sorted(_RULES_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        sections.extend(_parse_file_sections(path.stem, content))
    return sections


def _rules_block_drop_section(target: Section) -> str:
    """Independently reassemble the rules block with ``target`` removed.

    Mirrors ``build_rules_block`` (strip each file, join with blank lines) but
    drops one section from its file by rebuilding that file from its surviving
    spans. Independent of the ``span_text`` substring math, so asserting this
    equals ``full_block.replace(span_text, "")`` catches reassembly drift the
    full-arm byte-equal guard cannot (CD-m-1).
    """
    parts: list[str] = []
    for path in sorted(_RULES_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        if path.stem == target.rule_stem:
            sections = _parse_file_sections(path.stem, content)
            first_start = content.index(sections[0].core)
            header = content[:first_start].strip()
            kept = [s.core for s in sections if s.core != target.core]
            body = "\n\n".join(kept)
            content = f"{header}\n\n{body}".strip() if header else body
        if content:
            parts.append(content)
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Section-observability inventory (TASK-2 deliverable (a))
# ---------------------------------------------------------------------------

# Classification of every ``##`` section. ``signal`` names the target tool when a
# behavior is observable; ``status`` is one of:
#   PROBED              — observable AND single-turn discriminable; scored below.
#   OBSERVABLE-OUT-OF-HARNESS — maps to a tool, but the signal needs multi-turn
#                         state (todo_read, skill_create, skill_edit) or commanding
#                         it saturates both arms (explicit memory save); honestly
#                         out of this single-turn harness's reach, not scored.
#   OUT-OF-REACH        — steers response content/tone; no tool-call signal.
# Keyed (stem, title). Built from inspection per plan — NOT from eval budget.
_INVENTORY: tuple[tuple[str, str, str, str, str], ...] = (
    ("01_interaction", "Relationship", "OUT-OF-REACH", "-", "tone/continuity; no tool signal"),
    ("01_interaction", "Anti-sycophancy", "OUT-OF-REACH", "-", "response content; no tool signal"),
    (
        "01_interaction",
        "Thoroughness over speed",
        "OUT-OF-REACH",
        "-",
        "disposition (more tool calls in general); no single target tool",
    ),
    (
        "02_safety",
        "Credential protection",
        "OUT-OF-REACH",
        "-",
        "refusal/negative; no tool signal",
    ),
    (
        "02_safety",
        "Source control",
        "OUT-OF-REACH",
        "-",
        "negative (do-not stage/commit); unprompted commit saturates",
    ),
    ("02_safety", "Approval", "OUT-OF-REACH", "-", "system-handled confirmation; no tool signal"),
    ("02_safety", "Injected content", "OUT-OF-REACH", "-", "refusal/content; no tool signal"),
    (
        "03_reasoning",
        "Verification",
        "PROBED",
        "shell_exec",
        "arithmetic via compute tool, not head",
    ),
    (
        "03_reasoning",
        "Resolving contradictions",
        "OUT-OF-REACH",
        "-",
        "content/tone (trust tool vs user; surface tool-vs-tool conflict); no tool signal",
    ),
    (
        "03_reasoning",
        "Two kinds of unknowns",
        "PROBED",
        "discovery-set",
        "discover via tools before asking the user",
    ),
    (
        "04_tool_protocol",
        "Responsiveness",
        "OUT-OF-REACH",
        "-",
        "preamble text before tool calls; not a tool signal",
    ),
    (
        "04_tool_protocol",
        "Strategy",
        "OUT-OF-REACH",
        "-",
        "kitchen-sink (parallelism/depth/follow-through); no single isolable signal",
    ),
    (
        "04_tool_protocol",
        "Execute, don't promise",
        "OUT-OF-REACH",
        "-",
        "response shape (tool-call vs intent); no specific target tool",
    ),
    (
        "04_tool_protocol",
        "Error recovery",
        "OUT-OF-REACH",
        "-",
        "multi-turn retry behavior; not single-turn observable",
    ),
    (
        "04_tool_protocol",
        "Paths",
        "OUT-OF-REACH",
        "-",
        "argument shape (absolute paths); not tool presence",
    ),
    (
        "04_tool_protocol",
        "Deferred tools",
        "PROBED",
        "tool_view",
        "load a deferred tool via tool_view before use",
    ),
    (
        "05_workflow",
        "Intent classification",
        "OUT-OF-REACH",
        "-",
        "internal classification; no tool signal",
    ),
    (
        "05_workflow",
        "Execution",
        "PROBED",
        "todo_write",
        "decompose multi-step work into a todo list",
    ),
    (
        "05_workflow",
        "Completeness",
        "OBSERVABLE-OUT-OF-HARNESS",
        "todo_read",
        "needs a prior todo_write this session; multi-turn state, not single-turn",
    ),
    (
        "05_workflow",
        "When NOT to over-plan",
        "OUT-OF-REACH",
        "-",
        "response length/tone; no tool signal",
    ),
    (
        "06_skill_protocol",
        "Discovery",
        "PROBED",
        "skill_view",
        "load a matching skill via skill_view",
    ),
    (
        "06_skill_protocol",
        "Use",
        "OUT-OF-REACH",
        "-",
        "how to follow a loaded skill; multi-turn, no isolable signal",
    ),
    (
        "06_skill_protocol",
        "Drift",
        "OBSERVABLE-OUT-OF-HARNESS",
        "skill_edit",
        "fix a stale loaded skill; needs a loaded-then-stale skill, multi-turn",
    ),
    (
        "06_skill_protocol",
        "Create",
        "OBSERVABLE-OUT-OF-HARNESS",
        "skill_create",
        "promote a completed 3+ step procedure; needs a finished multi-turn task",
    ),
    (
        "07_memory_protocol",
        "Recall",
        "PROBED",
        "recall-set",
        "search memory/sessions before answering",
    ),
    (
        "07_memory_protocol",
        "Explicit saves",
        "OBSERVABLE-OUT-OF-HARNESS",
        "memory_create",
        "implicit-save probe is dream territory; commanding 'remember' saturates both arms",
    ),
    (
        "07_memory_protocol",
        "Curation",
        "OUT-OF-REACH",
        "-",
        "judgment/multi-turn (promote/replace/drift); not single-turn observable",
    ),
    (
        "07_memory_protocol",
        "Anti-patterns",
        "OUT-OF-REACH",
        "-",
        "negative (what NOT to save); no positive tool signal",
    ),
)


# ---------------------------------------------------------------------------
# Probes — the OBSERVABLE single-turn discriminable subset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionProbe:
    """One section under test: which (stem, title) span to ablate, the implicit
    discriminating task, and the target-tool set whose firing is the demanded
    behavior."""

    rule_stem: str
    section_title: str
    label: str
    user_input: str
    target_tools: frozenset[str]
    fixture: str | None


# Every prompt is deliberately *implicit*: it must NOT name the target tool or
# command the behavior, or the task cues the behavior by itself and both arms
# saturate (NON-DISCRIMINATING). The behavior has to be the *non-obvious* path so
# the ablated arm can plausibly skip it.
_PROBES: tuple[SectionProbe, ...] = (
    SectionProbe(
        rule_stem="03_reasoning",
        section_title="Verification",
        label="arithmetic via compute tool, not from head",
        user_input=(
            "Quick one — what's 73912 * 48571? Just need the number for a slide I'm finishing."
        ),
        target_tools=_COMPUTE_TOOLS,
        fixture=None,
    ),
    SectionProbe(
        rule_stem="03_reasoning",
        section_title="Two kinds of unknowns",
        label="discover via tools before asking the user",
        user_input="which Python version is this project supposed to run on?",
        target_tools=_DISCOVERY_TOOLS,
        fixture=None,
    ),
    SectionProbe(
        rule_stem="04_tool_protocol",
        section_title="Deferred tools",
        label="load a deferred tool via tool_view before use",
        user_input="what's the current stable release version of the ripgrep CLI?",
        target_tools=_TOOL_VIEW,
        fixture=None,
    ),
    SectionProbe(
        rule_stem="05_workflow",
        section_title="Execution",
        label="decompose multi-step work into a todo list",
        user_input=(
            "Add a new config flag end to end: define it, wire it into the loader, "
            "and add a test for it."
        ),
        target_tools=_TODO_WRITE,
        fixture=None,
    ),
    SectionProbe(
        rule_stem="06_skill_protocol",
        section_title="Discovery",
        label="load a matching skill via skill_view",
        user_input="ship the current changes",
        target_tools=_SKILL_VIEW,
        fixture=None,
    ),
    SectionProbe(
        rule_stem="07_memory_protocol",
        section_title="Recall",
        label="search memory/sessions before answering",
        user_input="draft me a quick standup update for tomorrow",
        target_tools=_RECALL_TOOLS,
        fixture="user_model_baseline",
    ),
)


def _resolve_section(stem: str, title: str) -> Section:
    """Find the parsed Section for a (stem, title); fail loud on a typo'd probe."""
    for section in _all_sections():
        if section.rule_stem == stem and section.title == title:
            return section
    raise KeyError(
        f"no section {title!r} in {stem!r} — probe references a section that does not exist"
    )


def _build_arm_agent(deps: Any, ablated_block: str | None) -> Any:
    """Build an orchestrator whose rules block is ``ablated_block`` (or the full
    production block when ``None``).

    Persona is off by default, so the production base layer is the rules block
    alone (``build_base_instructions`` reduces to ``build_rules_block``). The
    custom spec swaps that one builder and reuses the real toolset guidance,
    per-turn instructions, and history processors verbatim, so the only
    difference from the shipped prompt is the ablated section.
    """
    block = ablated_block if ablated_block is not None else build_rules_block()

    def _rules_builder(_deps: Any, _block: str = block) -> str | None:
        return _block

    def _toolset_builder(d: Any) -> str | None:
        return build_toolset_guidance(d.tool_catalog)

    spec = OrchestratorSpec(
        name="orchestrator-ablation",
        static_instruction_builders=(_rules_builder, _toolset_builder),
        per_turn_instructions=ORCHESTRATOR_SPEC.per_turn_instructions,
        history_processors=ORCHESTRATOR_SPEC.history_processors,
    )
    return build_orchestrator(spec, deps)


def _tool_calls_from(messages: list[Any]) -> list[ToolCallPart]:
    """Extract ToolCallParts in call order across the assistant messages."""
    calls: list[ToolCallPart] = []
    for msg in messages:
        if isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, ToolCallPart):
                    calls.append(part)
    return calls


def _target_fired(tool_calls: list[ToolCallPart], target_tools: frozenset[str]) -> bool:
    """True if any tool call in the turn is one of the section's target tools."""
    return any(tc.tool_name in target_tools for tc in tool_calls)


async def _run_turn(agent: Any, deps: Any, frontend: Any, user_input: str) -> list[ToolCallPart]:
    """Drive one fresh single-turn agent run; return the turn's tool calls."""
    from co_cli.agent.orchestrate import run_turn

    async with asyncio.timeout(CALL_TIMEOUT_S):
        result = await run_turn(
            agent=agent,
            user_input=user_input,
            deps=deps,
            message_history=[],
            frontend=frontend,
        )
    return _tool_calls_from(list(result.messages))


async def _arm_fire_rate(
    agent: Any, deps: Any, frontend: Any, probe: SectionProbe, arm: str, samples: int
) -> dict[str, Any]:
    """Run N independent samples for one arm; return the target-tool fire-rate."""
    fired = 0
    timeouts = 0
    for i in range(samples):
        try:
            tool_calls = await _run_turn(agent, deps, frontend, probe.user_input)
            hit = _target_fired(tool_calls, probe.target_tools)
            fired += 1 if hit else 0
            tag = f"{probe.rule_stem}/{probe.section_title}/{arm}"
            print(f"  [{tag}] sample {i + 1}/{samples}: fired={hit}")
        except TimeoutError:
            timeouts += 1
            tag = f"{probe.rule_stem}/{probe.section_title}/{arm}"
            print(f"  [{tag}] sample {i + 1}/{samples}: TIMEOUT")
    return {
        "arm": arm,
        "fired": fired,
        "samples": samples,
        "timeouts": timeouts,
        "fire_rate": fired / samples,
    }


async def _measure_section(
    deps: Any, frontend: Any, probe: SectionProbe, full_block: str, samples: int
) -> dict[str, Any]:
    """Measure one section: full vs ablated fire-rate, then STEERS/DEAD-WEIGHT."""
    print(f"\n[{probe.rule_stem} :: {probe.section_title}] {probe.label}")
    section = _resolve_section(probe.rule_stem, probe.section_title)

    ablated_block = _rules_block_drop_section(section)
    # Two assembly-fidelity guards (one ablated section at a time):
    #  - the full arm is byte-equal to the shipped prompt (checked once in main);
    #  - the ablated arm equals the full block with exactly this span removed.
    # The second is cross-checked against an independent reassembly so a parser
    # boundary slip (eating a neighbor or an H1) fails fast rather than measuring
    # noise.
    assert full_block.count(section.span_text) == 1, (
        f"span for {section.rule_stem}/{section.title} is not unique in the full block"
    )
    assert ablated_block == full_block.replace(section.span_text, ""), (
        f"ablated arm for {section.rule_stem}/{section.title} drifted from "
        "full_block.replace(span, '') — reassembly disturbed a neighbor span or H1"
    )
    assert "\n\n\n" not in ablated_block, (
        f"ablation of {section.rule_stem}/{section.title} left a doubled blank line"
    )

    if probe.fixture:
        load_fixture(probe.fixture, deps)

    full_agent = _build_arm_agent(deps, ablated_block=None)
    full = await _arm_fire_rate(full_agent, deps, frontend, probe, "full", samples)

    ablated_agent = _build_arm_agent(deps, ablated_block=ablated_block)
    ablated = await _arm_fire_rate(ablated_agent, deps, frontend, probe, "ablated", samples)

    delta = full["fire_rate"] - ablated["fire_rate"]
    # A zero delta at a saturated ceiling/floor (both arms 1.0 or both 0.0) is
    # NOT evidence the section is dead — the task gave the ablated arm no room to
    # fail (or succeed), so it cannot separate the section's effect. Only call
    # DEAD-WEIGHT when the ablated arm demonstrably *could* have differed.
    if full["fire_rate"] == ablated["fire_rate"] and full["fire_rate"] in (0.0, 1.0):
        verdict = "NON-DISCRIMINATING"
    elif delta >= STEER_DELTA:
        verdict = "STEERS"
    else:
        verdict = "DEAD-WEIGHT"
    return {
        "rule": probe.rule_stem,
        "section": probe.section_title,
        "label": probe.label,
        "target_tools": sorted(probe.target_tools),
        "full": full,
        "ablated": ablated,
        "delta": delta,
        "steer_delta_threshold": STEER_DELTA,
        "verdict": verdict,
    }


def _emit_inventory() -> list[dict[str, Any]]:
    """Build, print, and return the 28-section observability inventory.

    Validates the span parser end to end (count, uniqueness, clean reassembly)
    for every section so ``--inventory`` is also the parser's self-test.
    """
    sections = _all_sections()
    full_block = build_rules_block()
    assert full_block == build_rules_block(), "full block is not stable"

    by_key = {(s.rule_stem, s.title): s for s in sections}
    inventory: list[dict[str, Any]] = []
    probed_keys = {(p.rule_stem, p.section_title) for p in _PROBES}

    print(f"\n=== Section-observability inventory ({len(sections)} sections) ===")
    print(f"{'rule':<18} {'section':<24} {'status':<26} {'signal':<14} note")
    for stem, title, status, signal, note in _INVENTORY:
        key = (stem, title)
        assert key in by_key, f"inventory references missing section {key}"
        section = by_key[key]
        # Parser self-test: every span removes cleanly from the full block.
        assert full_block.count(section.span_text) == 1, f"span not unique: {key}"
        ablated = _rules_block_drop_section(section)
        assert ablated == full_block.replace(section.span_text, ""), f"reassembly drift: {key}"
        assert "\n\n\n" not in ablated, f"doubled blank line: {key}"
        if status == "PROBED":
            assert key in probed_keys, f"inventory marks {key} PROBED but no probe exists"
        print(f"{stem:<18} {title:<24} {status:<26} {signal:<14} {note}")
        inventory.append(
            {
                "rule": stem,
                "section": title,
                "status": status,
                "signal": signal,
                "note": note,
            }
        )

    assert len(_INVENTORY) == len(sections) == 28, (
        f"inventory ({len(_INVENTORY)}) / parser ({len(sections)}) disagree on 28 sections"
    )
    probed = [r for r in inventory if r["status"] == "PROBED"]
    out_of_harness = [r for r in inventory if r["status"] == "OBSERVABLE-OUT-OF-HARNESS"]
    out_of_reach = [r for r in inventory if r["status"] == "OUT-OF-REACH"]
    print(
        f"\nPROBED (distinguishable signals): {len(probed)} | "
        f"OBSERVABLE-OUT-OF-HARNESS: {len(out_of_harness)} | OUT-OF-REACH: {len(out_of_reach)}"
    )
    print(f"distinguishable signals scored: {sorted(r['signal'] for r in probed)}")
    return inventory


async def main(samples: int = SAMPLES_PER_ARM, section: str | None = None) -> None:
    full_block = build_rules_block()
    inventory = _emit_inventory()

    probes = tuple(p for p in _PROBES if p.section_title == section) if section else _PROBES
    if section and not probes:
        raise SystemExit(
            f"--section {section!r} matches no probe; choose one of: "
            f"{sorted(p.section_title for p in _PROBES)}"
        )

    await ensure_ollama_warm()
    deps, _agent, frontend, stack = await make_eval_deps()
    try:
        apply_eval_window(deps)
        print(
            f"\nSection-ablation — model {deps.model.model.model_name}, "
            f"{samples} samples/arm, steer_delta>={STEER_DELTA}, "
            f"{len(probes)} observable sections"
        )
        results = []
        for probe in probes:
            results.append(await _measure_section(deps, frontend, probe, full_block, samples))

        deadweight = [
            f"{r['rule']}/{r['section']}" for r in results if r["verdict"] == "DEAD-WEIGHT"
        ]
        nondiscriminating = [
            f"{r['rule']}/{r['section']}" for r in results if r["verdict"] == "NON-DISCRIMINATING"
        ]
        if deadweight:
            overall = "CONSOLIDATION-CANDIDATES"
        elif nondiscriminating:
            overall = "INCONCLUSIVE"
        else:
            overall = "ALL-STEER"

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = Path(__file__).parent / "_outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"rule-compliance-{ts}-run.jsonl"
        with out_path.open("w", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "record": "meta",
                        "model": deps.model.model.model_name,
                        "samples_per_arm": samples,
                        "steer_delta": STEER_DELTA,
                    }
                )
                + "\n"
            )
            for entry in inventory:
                fh.write(json.dumps({"record": "inventory", **entry}) + "\n")
            for entry in results:
                fh.write(json.dumps({"record": "section", **entry}) + "\n")
            fh.write(
                json.dumps(
                    {
                        "record": "verdict",
                        "overall": overall,
                        "deadweight_sections": deadweight,
                        "nondiscriminating_sections": nondiscriminating,
                    }
                )
                + "\n"
            )

        print("\n=== VERDICT ===")
        for entry in results:
            print(
                f"  {entry['rule']}/{entry['section']}: {entry['verdict']} "
                f"(full {entry['full']['fire_rate']:.2f} vs "
                f"ablated {entry['ablated']['fire_rate']:.2f}, Δ={entry['delta']:+.2f})"
            )
        print(f"\noverall: {overall}")
        if deadweight:
            print(f"DEAD-WEIGHT (consolidation candidates): {', '.join(deadweight)}")
        if nondiscriminating:
            print(
                "NON-DISCRIMINATING (task saturated — section effect unmeasurable, "
                f"needs a harder task): {', '.join(nondiscriminating)}"
            )
        print(f"run → {out_path}")
    finally:
        await stack.aclose()


def _arg_value(*flags: str) -> str | None:
    """Read the value following any of ``flags`` in argv (bare parsing, no argparse)."""
    for flag in flags:
        if flag in sys.argv:
            idx = sys.argv.index(flag)
            if idx + 1 < len(sys.argv):
                return sys.argv[idx + 1]
            raise SystemExit(f"{flag} requires a value")
    return None


if __name__ == "__main__":
    if "--inventory" in sys.argv:
        _emit_inventory()
    else:
        samples_arg = _arg_value("--samples", "-n")
        section_arg = _arg_value("--section")
        asyncio.run(
            main(
                samples=int(samples_arg) if samples_arg else SAMPLES_PER_ARM,
                section=section_arg,
            )
        )
