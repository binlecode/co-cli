"""Shared helpers for the eval suite.

Extracts duplicated patterns (model detection, deps construction, frontend
stubs, tool-call extraction) so individual evals stay focused on scoring logic.
"""

import json
import re
import sqlite3
from dataclasses import dataclass, field, replace as dataclass_replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.settings import ModelSettings

from co_cli._orchestrate import FrontendProtocol
from co_cli.config import settings, get_settings
from co_cli.deps import CoDeps
from co_cli.shell_backend import ShellBackend


# ---------------------------------------------------------------------------
# Model tag detection
# ---------------------------------------------------------------------------


def detect_model_tag() -> str:
    """Auto-detect a model tag from the current LLM config."""
    provider = settings.llm_provider.lower()
    if provider == "gemini":
        return f"gemini-{settings.gemini_model}"
    if provider == "ollama":
        return f"ollama-{settings.ollama_model}"
    return provider


# ---------------------------------------------------------------------------
# CoDeps factory
# ---------------------------------------------------------------------------


def make_eval_deps(**overrides: Any) -> CoDeps:
    """Build a CoDeps suitable for evals, pulling defaults from settings.

    Pass keyword overrides to customise any field, e.g.
    ``make_eval_deps(session_id="my-eval", brave_search_api_key=None)``.
    """
    s = get_settings()
    defaults: dict[str, Any] = {
        "shell": ShellBackend(),
        "session_id": "eval",
        "obsidian_vault_path": None,
        "google_credentials_path": None,
        "shell_safe_commands": [],
        "brave_search_api_key": s.brave_search_api_key,
        "web_policy": s.web_policy,
        "web_http_max_retries": s.web_http_max_retries,
        "web_http_backoff_base_seconds": s.web_http_backoff_base_seconds,
        "web_http_backoff_max_seconds": s.web_http_backoff_max_seconds,
        "web_http_jitter_ratio": s.web_http_jitter_ratio,
        "doom_loop_threshold": s.doom_loop_threshold,
        "max_reflections": s.max_reflections,
        "memory_max_count": s.memory_max_count,
        "memory_dedup_window_days": s.memory_dedup_window_days,
        "memory_dedup_threshold": s.memory_dedup_threshold,
        "memory_decay_strategy": s.memory_decay_strategy,
        "memory_decay_percentage": s.memory_decay_percentage,
        "max_history_messages": s.max_history_messages,
        "tool_output_trim_chars": s.tool_output_trim_chars,
        "summarization_model": s.summarization_model,
    }
    defaults.update(overrides)
    return CoDeps(**defaults)


# ---------------------------------------------------------------------------
# Model settings
# ---------------------------------------------------------------------------


def make_eval_settings(
    model_settings: ModelSettings | None = None,
    *,
    max_tokens: int | None = None,
) -> ModelSettings:
    """Build eval settings from real model configuration.

    All values are passed through as-is from the quirks database so evals run
    against the same parameters as live sessions. Both providers now supply
    model_settings via get_agent():
      - Ollama: temperature from quirks (e.g. 0.6 for qwen3). Never override
        to 0 — thinking models produce degenerate loops at temperature=0.
      - Gemini: temperature from quirks (typically 1.0 for thinking models).
        Google's guidance: setting below 1.0 causes looping in thinking models.

    Falls back to temperature=0 only when no model settings exist at all
    (e.g. unit tests / unknown providers).

    Args:
        model_settings: Settings from get_agent(), or None for fallback.
        max_tokens: Optional override for max_tokens. Omit to use the quirks default.
    """
    if model_settings is None:
        base: dict[str, Any] = {"temperature": 0}
        if max_tokens is not None:
            base["max_tokens"] = max_tokens
        return ModelSettings(**base)

    # ModelSettings is a TypedDict — plain dict at runtime, use .get() not getattr
    base = {}
    for key in ("temperature", "top_p", "max_tokens"):
        val = model_settings.get(key)
        if val is not None:
            base[key] = val
    extra_body = model_settings.get("extra_body")
    if extra_body:
        base["extra_body"] = extra_body
    if max_tokens is not None:
        base["max_tokens"] = max_tokens
    return ModelSettings(**base)


# ---------------------------------------------------------------------------
# SilentFrontend — minimal FrontendProtocol for E2E evals
# ---------------------------------------------------------------------------


class SilentFrontend:
    """Minimal frontend that captures status messages.

    Pass ``approval_response`` to control tool approval behaviour:
      - ``"y"`` (default): auto-approve everything
      - ``"n"``: deny everything
    """

    def __init__(self, *, approval_response: str = "y"):
        self.statuses: list[str] = []
        self.final_text: str | None = None
        self._approval_response = approval_response

    def on_text_delta(self, accumulated: str) -> None:
        pass

    def on_text_commit(self, final: str) -> None:
        pass

    def on_thinking_delta(self, accumulated: str) -> None:
        pass

    def on_thinking_commit(self, final: str) -> None:
        pass

    def on_tool_call(self, name: str, args_display: str) -> None:
        pass

    def on_tool_result(self, title: str, content: Any) -> None:
        pass

    def on_status(self, message: str) -> None:
        self.statuses.append(message)

    def on_final_output(self, text: str) -> None:
        self.final_text = text

    def prompt_approval(self, description: str) -> str:
        return self._approval_response

    def cleanup(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Tool-call extraction
# ---------------------------------------------------------------------------


def extract_first_tool_call(
    messages: list[Any],
) -> tuple[str | None, dict[str, Any] | None]:
    """Extract the first ToolCallPart from agent messages."""
    for msg in messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                return part.tool_name, part.args_as_dict()
    return None, None


def extract_tool_calls(messages: list[Any]) -> list[tuple[str, dict[str, Any]]]:
    """Extract all ToolCallParts from agent messages as (name, args) tuples."""
    calls: list[tuple[str, dict[str, Any]]] = []
    for msg in messages:
        if not hasattr(msg, "parts"):
            continue
        for part in msg.parts:
            if isinstance(part, ToolCallPart):
                calls.append((part.tool_name, part.args_as_dict()))
    return calls


# ---------------------------------------------------------------------------
# Eval case schema (shared by runner and trace report)
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    id: str
    personality: str
    turns: list[str]
    checks_per_turn: list[list[dict[str, Any]]]


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            cases.append(EvalCase(
                id=raw["id"],
                personality=raw["personality"],
                turns=raw["turns"],
                checks_per_turn=raw["checks_per_turn"],
            ))
    return cases


# ---------------------------------------------------------------------------
# Check engine (shared by runner and trace report)
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r'[.!?]+(?:\s|$)')


def count_sentences(text: str) -> int:
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)
    if _SENTENCE_END.search(text):
        parts = _SENTENCE_END.split(text)
        return sum(1 for p in parts if p.strip())
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return max(len(lines), 1) if lines else 0


def check_max_sentences(text: str, params: dict[str, Any]) -> str | None:
    n = params["n"]
    actual = count_sentences(text)
    if actual <= n:
        return None
    return f"max_sentences: got {actual}, expected <= {n}"


def check_min_sentences(text: str, params: dict[str, Any]) -> str | None:
    n = params["n"]
    actual = count_sentences(text)
    if actual >= n:
        return None
    return f"min_sentences: got {actual}, expected >= {n}"


def check_forbidden(text: str, params: dict[str, Any]) -> str | None:
    # Strip inline markdown emphasis (* and _) so formatted text like
    # "not *always* wrong" doesn't bypass forbidden checks on "always".
    clean = re.sub(r'[*_]', '', text).lower()
    for phrase in params["phrases"]:
        if phrase.lower() in clean:
            return f"forbidden: found '{phrase}'"
    return None


def check_required_any(text: str, params: dict[str, Any]) -> str | None:
    # Strip inline markdown emphasis (* and _) before matching so
    # "not *always* wrong" matches the phrase "not always wrong".
    clean = re.sub(r'[*_]', '', text).lower()
    for phrase in params["phrases"]:
        if phrase.lower() in clean:
            return None
    return f"required_any: none of {params['phrases']} found"


def check_no_preamble(text: str, params: dict[str, Any]) -> str | None:
    stripped = text.strip().lower()
    for phrase in params["phrases"]:
        if stripped.startswith(phrase.lower()):
            return f"no_preamble: starts with '{phrase}'"
    return None


def check_has_question(text: str, params: dict[str, Any]) -> str | None:
    if "?" in text:
        return None
    return "has_question: no '?' found"


_CHECK_DISPATCH: dict[str, Any] = {
    "max_sentences": check_max_sentences,
    "min_sentences": check_min_sentences,
    "forbidden": check_forbidden,
    "required_any": check_required_any,
    "no_preamble": check_no_preamble,
    "has_question": check_has_question,
}


def score_response(text: str, checks: list[dict[str, Any]]) -> list[str]:
    """Run synchronous checks. Returns list of failure descriptions.

    Skips ``llm_judge`` and other async-only check types silently.
    Use ``score_turn`` for full evaluation including LLM judge checks.
    """
    failures: list[str] = []
    for check in checks:
        check_type = check["type"]
        fn = _CHECK_DISPATCH.get(check_type)
        if fn is None:
            # Unknown or async-only type (e.g. llm_judge) — skip silently
            continue
        result = fn(text, check)
        if result is not None:
            failures.append(result)
    return failures


# ---------------------------------------------------------------------------
# LLM-as-judge (async)
# ---------------------------------------------------------------------------


class JudgeResult(BaseModel):
    passed: bool
    reasoning: str


_JUDGES_DIR = Path(__file__).parent / "judges"


def _load_character_judge(role: str) -> str:
    """Load character-specific judgment rules from ``evals/judges/{role}.md``.

    Returns empty string if no judge file exists for the role — the judge
    prompt will omit the character rules section gracefully.
    """
    if not role:
        return ""
    path = _JUDGES_DIR / f"{role}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


_JUDGE_PROMPT = (
    "You are evaluating whether this AI response is in character for {personality}.\n\n"
    "CHARACTER JUDGMENT RULES:\n{character_rules}\n\n"
    "SPECIFIC CRITERION FOR THIS CHECK:\n{criteria}\n\n"
    "RESPONSE TO EVALUATE:\n{response}\n\n"
    "Return JSON with exactly two fields:\n"
    '- "passed": true only if the response clearly satisfies the criterion with confidence\n'
    '- "reasoning": one sentence explaining your judgment\n\n'
    "When in doubt, fail. High bar — only pass when the criterion is clearly and unambiguously met."
)


def _make_judge_settings(base: ModelSettings | None) -> ModelSettings:
    """Build model settings for the LLM judge from base eval settings.

    Reduces temperature to 70% of the base value for more stable binary
    judgments while keeping it above 0.3 (thinking models loop at very low
    temperatures). Preserves ``extra_body`` (e.g. ``enable_thinking``) so
    the thinking budget is still available for reasoning through criteria.
    Max tokens is intentionally not capped — thinking models consume output
    tokens for chain-of-thought before emitting the JSON object.
    """
    if base is None:
        return ModelSettings(temperature=0.7)
    base_temp = base.get("temperature") or 1.0
    # Floor at 0.3 — thinking models produce degenerate loops at temperature=0
    judge_temp = max(0.3, base_temp * 0.7)
    kwargs: dict[str, Any] = {"temperature": judge_temp}
    extra_body = base.get("extra_body")
    if extra_body:
        kwargs["extra_body"] = extra_body
    return ModelSettings(**kwargs)


async def _llm_judge(
    text: str,
    criteria: str,
    agent: Any,
    deps: CoDeps,
    model_settings: ModelSettings | None,
) -> JudgeResult:
    """Run one LLM judge check. Returns the full JudgeResult (passed + reasoning).

    Loads the character-specific judgment rules from ``evals/judges/{role}.md``
    so the judge applies consistent behavioral standards across all cases for
    that personality. The JSONL criterion is the per-check assertion; the judge
    file is the shared character evaluation rubric.

    Soul seed and soul critique (from the agent's system prompt and deps) provide
    additional character context. Active mindset is stripped — it is the task
    mindset for generating responses, not for evaluating them.
    """
    role = deps.personality or ""
    character_rules = _load_character_judge(role)
    personality_label = role.capitalize() if role else "this character"
    judge_deps = dataclass_replace(
        deps,
        active_mindset_content="",
        active_mindset_types=[],
    )
    judge_ms = _make_judge_settings(model_settings)
    prompt = _JUDGE_PROMPT.format(
        personality=personality_label,
        character_rules=character_rules or "(no character rules file found)",
        criteria=criteria,
        response=text,
    )
    result = await agent.run(
        prompt,
        output_type=JudgeResult,
        message_history=[],
        deps=judge_deps,
        model_settings=judge_ms,
    )
    return result.output


async def score_turn(
    text: str,
    checks: list[dict[str, Any]],
    agent: Any,
    deps: CoDeps,
    model_settings: ModelSettings | None,
) -> tuple[list[str], dict[int, str]]:
    """Run all checks for one turn.

    Returns ``(failures, judge_details)`` where:
    - ``failures``: list of failure description strings (empty = all pass)
    - ``judge_details``: dict of check_index → "PASS: reasoning" or "FAIL: reasoning"
      for every ``llm_judge`` check, so reasoning is visible for both outcomes

    Handles async ``llm_judge`` checks via LLM call and falls back to
    synchronous dispatch for all other check types (``forbidden``, etc.).
    """
    failures: list[str] = []
    judge_details: dict[int, str] = {}
    for i, check in enumerate(checks):
        check_type = check["type"]
        if check_type == "llm_judge":
            jr = await _llm_judge(
                text, check["criteria"], agent, deps, model_settings
            )
            prefix = "PASS" if jr.passed else "FAIL"
            judge_details[i] = f"{prefix}: {jr.reasoning}"
            if not jr.passed:
                failures.append(f"llm_judge: {jr.reasoning}")
        else:
            fn = _CHECK_DISPATCH.get(check_type)
            if fn is None:
                continue
            result = fn(text, check)
            if result is not None:
                failures.append(result)
    return failures, judge_details


# ---------------------------------------------------------------------------
# Span row types
# ---------------------------------------------------------------------------


@dataclass
class SpanRow:
    id: str
    trace_id: str
    parent_id: str | None
    name: str
    kind: str | None
    start_time: int
    end_time: int | None
    duration_ms: float | None
    status_code: str | None
    attributes: dict[str, Any]
    events: list[dict[str, Any]]


def _parse_span_row(row: tuple) -> SpanRow:
    (
        span_id,
        trace_id,
        parent_id,
        name,
        kind,
        start_time,
        end_time,
        duration_ms,
        status_code,
        attributes_json,
        events_json,
    ) = row
    attrs: dict[str, Any] = json.loads(attributes_json) if attributes_json else {}
    events: list[dict[str, Any]] = json.loads(events_json) if events_json else []
    return SpanRow(
        id=span_id,
        trace_id=trace_id,
        parent_id=parent_id,
        name=name,
        kind=kind,
        start_time=start_time,
        end_time=end_time,
        duration_ms=duration_ms,
        status_code=status_code,
        attributes=attrs,
        events=events,
    )


def _get_attr(span: SpanRow, key: str, default: Any = None) -> Any:
    return span.attributes.get(key, default)


def _parse_messages_attr(span: SpanRow, key: str) -> list[dict[str, Any]]:
    raw = _get_attr(span, key)
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(raw, list):
        return raw
    return []


def extract_thinking(messages: list[dict[str, Any]]) -> str:
    """Extract the first thinking content from output messages."""
    for msg in messages:
        parts = msg.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "thinking":
                content = part.get("content", "") or part.get("thinking", "")
                if content:
                    return content
    return ""


def extract_text(messages: list[dict[str, Any]]) -> str:
    """Extract the last text content from output messages."""
    text = ""
    for msg in messages:
        parts = msg.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                content = part.get("content", "") or part.get("text", "")
                if content:
                    text = content
    return text


def extract_tool_calls_from_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract tool-call parts from output messages.

    pydantic-ai OTel spans use type="tool_call" (underscore) in output messages.
    """
    calls: list[dict[str, Any]] = []
    for msg in messages:
        parts = msg.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            # pydantic-ai v3 spans use "tool_call" (underscore)
            if part.get("type") in ("tool_call", "tool-call"):
                calls.append(part)
    return calls


# ---------------------------------------------------------------------------
# Structured span data types
# ---------------------------------------------------------------------------


@dataclass
class ModelRequestData:
    span: SpanRow
    request_index: int
    input_tokens: int
    output_tokens: int
    finish_reason: str
    # thinking_excerpt: first 200 chars for timeline summary
    thinking_excerpt: str
    # thinking_full: untruncated thinking content
    thinking_full: str
    text_response: str
    tool_calls: list[dict[str, Any]]
    request_model: str = ""
    response_model: str = ""
    response_id: str = ""
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    server_address: str = ""
    server_port: int | None = None
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    input_messages: list[dict[str, Any]] = field(default_factory=list)
    system_instructions: list[dict[str, Any]] = field(default_factory=list)
    tool_definitions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolSpanData:
    span: SpanRow
    tool_name: str
    arguments: dict[str, Any]
    duration_ms: float | None
    result_preview: str
    tool_call_id: str = ""
    result_full: str = ""
    exception_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TimelineRow:
    elapsed_ms: int
    duration_ms: str
    span_name: str
    detail: str


@dataclass
class TurnTrace:
    """Per-turn trace from a single agent.run() call."""
    spans: list[SpanRow]
    root_span: SpanRow | None
    model_requests: list[ModelRequestData]
    tool_spans: list[ToolSpanData]
    timeline: list[TimelineRow]
    wall_time_s: float
    response_text: str
    failed_checks: list[str]
    error: str | None = None
    system_instructions: list[dict[str, Any]] = field(default_factory=list)
    all_messages_raw: list[dict[str, Any]] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0


# ---------------------------------------------------------------------------
# Telemetry bootstrap and span collection
# ---------------------------------------------------------------------------


def bootstrap_telemetry(db_path: str) -> Any:
    """Set up OTel TracerProvider with SQLite exporter and instrument pydantic-ai.

    Returns the TracerProvider. Uses lazy imports so OTel is only required when
    trace collection is actually used.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from pydantic_ai import Agent
    from pydantic_ai.agent import InstrumentationSettings
    from co_cli._telemetry import SQLiteSpanExporter

    exporter = SQLiteSpanExporter(db_path=db_path)
    resource = Resource.create({"service.name": "co-cli", "service.version": "eval"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    Agent.instrument_all(InstrumentationSettings(tracer_provider=provider, version=3))
    return provider


def collect_spans_for_run(start_ns: int, db_path: str) -> list[SpanRow]:
    """Query spans written after start_ns, find the root trace, return its subtree."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        rows = conn.execute(
            """
            SELECT id, trace_id, parent_id, name, kind, start_time, end_time,
                   duration_ms, status_code, attributes, events
            FROM spans
            WHERE start_time >= ?
            ORDER BY start_time ASC
            """,
            (start_ns,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    spans = [_parse_span_row(r) for r in rows]

    # Prefer a root span with no parent and "agent" in name; fall back to earliest
    root_trace_id: str | None = None
    for s in spans:
        if s.parent_id is None and "agent" in s.name.lower():
            root_trace_id = s.trace_id
            break
    if root_trace_id is None:
        root_trace_id = spans[0].trace_id

    return [s for s in spans if s.trace_id == root_trace_id]


def find_root_span(spans: list[SpanRow]) -> SpanRow | None:
    """Return the span with no parent_id, or the one whose parent is outside the set."""
    ids = {s.id for s in spans}
    for s in spans:
        if s.parent_id is None or s.parent_id not in ids:
            return s
    return spans[0] if spans else None


# ---------------------------------------------------------------------------
# Timeline builder
# ---------------------------------------------------------------------------


def build_timeline(spans: list[SpanRow], root: SpanRow | None) -> list[TimelineRow]:
    """Build a timeline table from sorted spans."""
    if not spans:
        return []

    base_ns = root.start_time if root else spans[0].start_time
    rows: list[TimelineRow] = []

    for s in spans:
        elapsed_ms = int((s.start_time - base_ns) / 1_000_000)
        dur_str = f"{int(s.duration_ms):,}" if s.duration_ms is not None else "—"

        detail_parts: list[str] = []

        in_tok = _get_attr(s, "gen_ai.usage.input_tokens")
        out_tok = _get_attr(s, "gen_ai.usage.output_tokens")
        if in_tok is not None and out_tok is not None:
            detail_parts.append(f"tokens in={in_tok} out={out_tok}")

        finish = _get_attr(s, "gen_ai.response.finish_reasons")
        if finish:
            if isinstance(finish, list) and finish:
                detail_parts.append(f"finish={finish[0]}")
            elif isinstance(finish, str):
                detail_parts.append(f"finish={finish}")

        tool_name = _get_attr(s, "gen_ai.tool.name") or _get_attr(s, "tool_name")
        if tool_name is None and s.name.startswith("execute_tool "):
            tool_name = s.name.replace("execute_tool ", "").strip()
        if tool_name:
            args_raw = (
                _get_attr(s, "gen_ai.tool.call.arguments")
                or _get_attr(s, "tool_arguments")
                or _get_attr(s, "gen_ai.tool.arguments")
                or "{}"
            )
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                    first_key = next(iter(args), None)
                    if first_key:
                        first_val = str(args[first_key])[:50]
                        detail_parts.append(f'{first_key}="{first_val}"')
                except (json.JSONDecodeError, TypeError):
                    pass

        detail = "  ".join(detail_parts) if detail_parts else "—"

        rows.append(TimelineRow(
            elapsed_ms=elapsed_ms,
            duration_ms=dur_str,
            span_name=s.name,
            detail=detail,
        ))

    return rows


# ---------------------------------------------------------------------------
# Span analysis
# ---------------------------------------------------------------------------


def analyze_turn_spans(
    prompt: str,
    checks: list[dict[str, Any]],
    spans: list[SpanRow],
    wall_time_s: float,
) -> TurnTrace:
    """Parse span tree for one agent.run() turn into structured trace data."""
    root = find_root_span(spans)
    sorted_spans = sorted(spans, key=lambda s: s.start_time)

    model_requests: list[ModelRequestData] = []
    tool_spans_list: list[ToolSpanData] = []
    turn_system_instructions: list[dict[str, Any]] = []
    all_messages_raw: list[dict[str, Any]] = []
    response_text = ""

    req_idx = 0
    for s in sorted_spans:
        name_lower = s.name.lower()

        # invoke_agent span — collect all messages and final result
        # Note: gen_ai.system_instructions does not exist in pydantic-ai OTel spans;
        # the system prompt lives in gen_ai.input.messages[role=system] on chat spans.
        if "invoke_agent" in name_lower or name_lower == "agent":
            all_msgs = _parse_messages_attr(s, "pydantic_ai.all_messages")
            if all_msgs:
                all_messages_raw = all_msgs
            final_result = _get_attr(s, "final_result")
            if final_result and isinstance(final_result, str):
                response_text = final_result
            continue

        # Model request spans
        if any(kw in name_lower for kw in ("chat", "request", "completions", "generate")):
            out_msgs = _parse_messages_attr(s, "gen_ai.output.messages")
            thinking_raw = extract_thinking(out_msgs)
            text_raw = extract_text(out_msgs)
            tool_calls = extract_tool_calls_from_messages(out_msgs)

            input_tokens = int(_get_attr(s, "gen_ai.usage.input_tokens", 0) or 0)
            output_tokens = int(_get_attr(s, "gen_ai.usage.output_tokens", 0) or 0)

            finish_reasons = _get_attr(s, "gen_ai.response.finish_reasons")
            if isinstance(finish_reasons, list) and finish_reasons:
                finish_reason = str(finish_reasons[0])
            elif isinstance(finish_reasons, str):
                finish_reason = finish_reasons
            else:
                finish_reason = "unknown"

            # Cache tokens from gen_ai.usage.details
            cache_read = 0
            cache_write = 0
            usage_details_raw = _get_attr(s, "gen_ai.usage.details")
            if usage_details_raw:
                if isinstance(usage_details_raw, str):
                    try:
                        usage_details: dict[str, Any] = json.loads(usage_details_raw)
                    except (json.JSONDecodeError, TypeError):
                        usage_details = {}
                elif isinstance(usage_details_raw, dict):
                    usage_details = usage_details_raw
                else:
                    usage_details = {}
                cache_read = int(usage_details.get("cache_read_tokens", 0) or 0)
                cache_write = int(usage_details.get("cache_write_tokens", 0) or 0)

            # Server info
            server_address = str(_get_attr(s, "server.address") or "")
            server_port_raw = _get_attr(s, "server.port")
            server_port = int(server_port_raw) if server_port_raw is not None else None

            # Request settings
            temp_raw = _get_attr(s, "gen_ai.request.temperature")
            temperature = float(temp_raw) if temp_raw is not None else None
            top_p_raw = _get_attr(s, "gen_ai.request.top_p")
            top_p = float(top_p_raw) if top_p_raw is not None else None
            max_tokens_raw = _get_attr(s, "gen_ai.request.max_tokens")
            max_tokens_val = int(max_tokens_raw) if max_tokens_raw is not None else None

            req_idx += 1
            model_requests.append(ModelRequestData(
                span=s,
                request_index=req_idx,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=finish_reason,
                thinking_excerpt=thinking_raw[:200] if thinking_raw else "",
                thinking_full=thinking_raw,
                text_response=text_raw,
                tool_calls=tool_calls,
                request_model=str(_get_attr(s, "gen_ai.request.model") or ""),
                response_model=str(_get_attr(s, "gen_ai.response.model") or ""),
                response_id=str(_get_attr(s, "gen_ai.response.id") or ""),
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens_val,
                server_address=server_address,
                server_port=server_port,
                cache_read_tokens=cache_read,
                cache_write_tokens=cache_write,
                input_messages=_parse_messages_attr(s, "gen_ai.input.messages"),
                # System prompt lives in input_messages[role=system] — no separate attribute
                system_instructions=[
                    m for m in _parse_messages_attr(s, "gen_ai.input.messages")
                    if isinstance(m, dict) and m.get("role") == "system"
                ],
                tool_definitions=_parse_messages_attr(s, "gen_ai.tool.definitions"),
            ))
            continue

        # Tool execution spans
        tool_name = (
            _get_attr(s, "gen_ai.tool.name")
            or _get_attr(s, "tool_name")
        )
        is_execute_tool = s.name.startswith("execute_tool ")
        if tool_name or is_execute_tool:
            if tool_name is None:
                tool_name = s.name.replace("execute_tool ", "").strip()

            args_raw = (
                _get_attr(s, "gen_ai.tool.call.arguments")
                or _get_attr(s, "tool_arguments")
                or _get_attr(s, "gen_ai.tool.arguments")
                or "{}"
            )
            if isinstance(args_raw, str):
                try:
                    args_dict = json.loads(args_raw)
                except (json.JSONDecodeError, TypeError):
                    args_dict = {"raw": args_raw}
            elif isinstance(args_raw, dict):
                args_dict = args_raw
            else:
                args_dict = {}

            result_raw = (
                _get_attr(s, "gen_ai.tool.call.result")
                or _get_attr(s, "tool_response")
                or _get_attr(s, "gen_ai.tool.result")
                or ""
            )
            if not isinstance(result_raw, str):
                result_raw = json.dumps(result_raw)

            exception_events = [
                e for e in s.events
                if isinstance(e, dict) and e.get("name") == "exception"
            ]

            tool_spans_list.append(ToolSpanData(
                span=s,
                tool_name=str(tool_name),
                arguments=args_dict,
                duration_ms=s.duration_ms,
                result_preview=result_raw[:300],
                tool_call_id=str(_get_attr(s, "gen_ai.tool.call.id") or ""),
                result_full=result_raw,
                exception_events=exception_events,
            ))

    # Fallback: if no final_result from invoke_agent, use last model request text
    if not response_text and model_requests:
        response_text = model_requests[-1].text_response

    failed_checks = score_response(response_text, checks)
    timeline = build_timeline(sorted_spans, root)
    total_input_tokens = sum(r.input_tokens for r in model_requests)
    total_output_tokens = sum(r.output_tokens for r in model_requests)

    return TurnTrace(
        spans=sorted_spans,
        root_span=root,
        model_requests=model_requests,
        tool_spans=tool_spans_list,
        timeline=timeline,
        wall_time_s=wall_time_s,
        response_text=response_text,
        failed_checks=failed_checks,
        error=None,
        system_instructions=turn_system_instructions,
        all_messages_raw=all_messages_raw,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
    )


# ---------------------------------------------------------------------------
# Markdown report helpers
# ---------------------------------------------------------------------------


def _md_cell(text: str) -> str:
    """Escape markdown table cell metacharacters."""
    return text.replace("|", r"\|").replace("\n", "<br>")


def _check_display(check: dict[str, Any]) -> str:
    t = check.get("type", "")
    if t == "max_sentences":
        return f"max_sentences: ≤ {check.get('n')}"
    if t == "min_sentences":
        return f"min_sentences: ≥ {check.get('n')}"
    if t == "forbidden":
        phrases = check.get("phrases", [])
        return f"forbidden: {phrases}"
    if t == "required_any":
        phrases = check.get("phrases", [])
        return f"required_any: {phrases}"
    if t == "no_preamble":
        phrases = check.get("phrases", [])
        return f"no_preamble: {phrases}"
    if t == "has_question":
        return "has_question"
    if t == "llm_judge":
        criteria = check.get("criteria", "")
        return f"llm_judge: {criteria[:80]}{'...' if len(criteria) > 80 else ''}"
    return t


def _check_result(check: dict[str, Any], failures: list[str]) -> str:
    check_type = check.get("type", "")
    for f in failures:
        if f.startswith(check_type):
            return f"FAIL — {f}"
    return "PASS"


def _check_match_detail(check: dict[str, Any], text: str) -> str:
    """Return what was matched (or not) for a check — used in the Matched column."""
    t = check.get("type", "")
    clean = re.sub(r'[*_]', '', text).lower()
    if t == "required_any":
        for phrase in check.get("phrases", []):
            if phrase.lower() in clean:
                return f'"{phrase}"'
        return "none found"
    if t == "forbidden":
        for phrase in check.get("phrases", []):
            if phrase.lower() in clean:
                return f'"{phrase}"'
        return "—"
    if t in ("max_sentences", "min_sentences"):
        return f"actual={count_sentences(text)}"
    if t == "no_preamble":
        stripped = text.strip().lower()
        for phrase in check.get("phrases", []):
            if stripped.startswith(phrase.lower()):
                return f'"{phrase}"'
        return "—"
    if t == "has_question":
        return '"?" found' if "?" in text else "no ? found"
    if t == "llm_judge":
        return "(LLM evaluated)"
    return "—"
