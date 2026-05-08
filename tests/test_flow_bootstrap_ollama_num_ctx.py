"""Ollama num_ctx floor enforcement at bootstrap.

The Modelfile's ``num_ctx`` must meet or exceed the configured ``max_ctx``;
otherwise compaction sizing assumes a budget the model can't actually serve.
``_check_ollama_num_ctx_floor`` enforces this invariant.
"""

import pytest

from co_cli.bootstrap.core import _check_ollama_num_ctx_floor


def test_ollama_num_ctx_floor_raises_when_undercut():
    """Floor check raises ValueError naming both values when num_ctx < max_ctx."""
    with pytest.raises(ValueError, match="num_ctx=32,768") as exc_info:
        _check_ollama_num_ctx_floor(32_768, "mymodel:7b", 65_536)
    assert "65,536" in str(exc_info.value)


def test_ollama_num_ctx_floor_passes_at_and_above_max_ctx():
    """Floor check does not raise when num_ctx equals or exceeds max_ctx."""
    _check_ollama_num_ctx_floor(65_536, "mymodel:7b", 65_536)
    _check_ollama_num_ctx_floor(131_072, "mymodel:7b", 65_536)
