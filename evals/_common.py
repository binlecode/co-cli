"""Backward-compatible re-export barrel for the evals package.

All eval infrastructure has moved to focused sub-modules:
  _deps      — make_eval_deps, make_eval_settings, detect_model_tag
  _frontend  — SilentFrontend, CapturingFrontend
  _tools     — extract_tool_calls, extract_first_tool_call, is_ordered_subsequence, tool_names
  _checks    — EvalCase, load_cases, score_response, check_*
  _trace     — SpanRow, TurnTrace, bootstrap_telemetry, collect_spans_for_run,
               analyze_turn_spans, build_timeline, print_timeline, print_rca
  _fixtures  — seed_memory, single_user_turn, build_message_history, patch_dangling_tool_calls
  _report    — md_cell, check_display, check_result, check_match_detail

Import directly from sub-modules in new code. This file exists so that
existing evals that import from ``evals._common`` continue to work without
modification.
"""

from evals._deps import detect_model_tag, make_eval_deps, make_eval_settings
from evals._frontend import CapturingFrontend, SilentFrontend
from evals._tools import (
    extract_first_tool_call,
    extract_tool_calls,
    is_ordered_subsequence,
    tool_names,
)
from evals._checks import (
    EvalCase,
    count_sentences,
    check_forbidden,
    check_has_question,
    check_max_sentences,
    check_min_sentences,
    check_no_preamble,
    check_required_any,
    load_cases,
    score_response,
)
from evals._trace import (
    ModelRequestData,
    SpanRow,
    TimelineRow,
    ToolSpanData,
    TurnTrace,
    analyze_turn_spans,
    bootstrap_telemetry,
    build_timeline,
    collect_spans_for_run,
    extract_text,
    extract_thinking,
    extract_tool_calls_from_messages,
    find_root_span,
    print_rca,
    print_timeline,
)
from evals._fixtures import (
    build_message_history,
    patch_dangling_tool_calls,
    seed_memory,
    single_user_turn,
)
from evals._report import (
    check_display,
    check_match_detail,
    check_result,
    md_cell,
)

# Legacy underscore-prefixed aliases for callers that used the private names
_md_cell = md_cell
_check_display = check_display
_check_result = check_result
_check_match_detail = check_match_detail
