"""Spill-bypass removal: every tool-call output flows through tool_output()/tool_error().

toolgap-b1 removed `tool_output_raw`, the one path by which a tool-call output
reached context unbounded. These guards prevent regression: the helper must
return raw data or an error string, and both ctx-bearing web entrypoints must
wrap the helper-error case via `tool_error` so the spill path always fires.
"""

import inspect

from co_cli.tools import tool_io
from co_cli.tools.web import fetch, search


def test_tool_output_raw_removed() -> None:
    """`tool_output_raw` was the spill bypass — it must not exist."""
    assert not hasattr(tool_io, "tool_output_raw"), (
        "tool_output_raw is still exposed — spill bypass not removed."
    )


def test_http_get_with_retries_never_returns_toolreturn() -> None:
    """The shared HTTP helper must return raw data or an error string, never a ToolReturn."""
    annotation = inspect.signature(search._http_get_with_retries).return_annotation
    annotation_str = annotation if isinstance(annotation, str) else str(annotation)
    assert "ToolReturn" not in annotation_str, (
        f"_http_get_with_retries must not return ToolReturn — got: {annotation_str!r}"
    )
    assert "str" in annotation_str, (
        f"expected 'httpx.Response | str' return — got: {annotation_str!r}"
    )


def test_web_entrypoints_wrap_helper_error_via_tool_error() -> None:
    """Both web entrypoints must route the helper-error case through tool_error()."""
    fetch_src = inspect.getsource(fetch.web_fetch)
    search_src = inspect.getsource(search.web_search)
    for name, src in (("web_fetch", fetch_src), ("web_search", search_src)):
        assert "tool_error(resp_or_error" in src, (
            f"{name} no longer wraps the helper-error case via tool_error — "
            "spill-bypass regression."
        )
