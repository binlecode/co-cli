"""Eval: Ollama OpenAI noreason path — reasoning suppression + summarization pipeline.

Goal:
- validate that `qwen3.5:35b-a3b-think` with `reasoning_effort="none"` suppresses
  reasoning output and returns a direct final answer
- contrast with the same model on its default think path
- verify the production summarization pipeline (`build_model()` → `LlmModel`
  → `summarize_messages`) produces valid output over the same transport

Method:
- same OpenAI-compatible transport for all calls
- same deterministic request settings for both think vs noreason calls
- constrained prompts so default-vs-noreason behavior is measurable
- pipeline check uses the real registry and summarization function
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from time import perf_counter

import httpx
from evals._ollama import ensure_ollama_warm
from evals._timeouts import EVAL_BENCHMARK_TIMEOUT_SECS, EVAL_SUMMARIZATION_TIMEOUT_SECS
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

from co_cli._model_factory import LlmModel, build_model
from co_cli._model_settings import NOREASON_SETTINGS
from co_cli.config._core import settings as _settings
from co_cli.context.summarization import summarize_messages

_THINK_MODEL = "qwen3.5:35b-a3b-think"
_SYSTEM = (
    "You are a direct and helpful assistant. "
    "Respond immediately to the user without outputting internal thought processes, "
    "scratchpads, reasoning traces, or <think> tags. "
    "Return only the final answer in the requested format."
)

_BASE_EXTRA = {
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.5,
    "repeat_penalty": 1.0,
    "num_ctx": 8192,
    "num_predict": 4096,
}
_THINK_EXTRA = dict(_BASE_EXTRA) | {"reasoning_effort": "none"}
_COMMON_SETTINGS = ModelSettings(
    temperature=0.0,
    top_p=0.8,
    max_tokens=256,
)
_OLLAMA_WARM_TIMEOUT_S = 180
_THINK_CALL_TIMEOUT_S = EVAL_BENCHMARK_TIMEOUT_SECS
_NOREASON_CALL_TIMEOUT_S = 60


# ---------------------------------------------------------------------------
# Section 1: Think vs noreason contrast cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    check: str  # exact | json | similar
    similarity_floor: int = 90


_CASES: list[Case] = [
    Case(
        id="exact-token",
        prompt="Reply with exactly: HELLO",
        check="exact",
    ),
    Case(
        id="one-sentence-summary",
        prompt=(
            "Summarize in one sentence only: "
            "Docker packages software and its dependencies into portable containers."
        ),
        check="similar",
        similarity_floor=92,
    ),
    Case(
        id="json-extract",
        prompt=(
            'Return strict JSON only with keys "city" and "country" for this text: '
            '"I flew from Austin, United States to Tokyo."'
        ),
        check="json",
    ),
    Case(
        id="bullet-rewrite",
        prompt=(
            "Rewrite this into exactly two bullet points and nothing else: "
            "'The launch moved to Friday because QA found one regression. "
            "The team fixed it and reran the checks successfully.'"
        ),
        check="similar",
        similarity_floor=85,
    ),
]


def _normalize(text: str) -> str:
    return " ".join(text.strip().split())


async def _timed_await[T](label: str, awaitable: T) -> T:
    started = perf_counter()
    try:
        return await awaitable
    finally:
        elapsed = perf_counter() - started
        print(f"    {label}: {elapsed:.2f}s", flush=True)


async def _call_model(
    *,
    model_name: str,
    prompt: str,
    extra_body: dict | None = None,
) -> tuple[str, list[str]]:
    provider = OpenAIProvider(
        base_url=f"{_settings.llm.host}/v1",
        api_key="ollama",
        http_client=httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=180, write=30, pool=10)
        ),
    )
    model = OpenAIChatModel(model_name, provider=provider)
    response = await model.request(
        [ModelRequest(parts=[UserPromptPart(content=prompt)], instructions=_SYSTEM)],
        ModelSettings(
            temperature=_COMMON_SETTINGS["temperature"],
            top_p=_COMMON_SETTINGS["top_p"],
            max_tokens=_COMMON_SETTINGS["max_tokens"],
            extra_body=extra_body,
        ),
        ModelRequestParameters(),
    )
    try:
        text = "".join(
            getattr(part, "content", "")
            for part in response.parts
            if type(part).__name__ == "TextPart"
        ).strip()
        thinking_parts = [
            type(part).__name__ for part in response.parts if "Thinking" in type(part).__name__
        ]
        assert text, f"{model_name} returned empty final content"
        return text, thinking_parts
    finally:
        await provider.client.close()


def _assert_case(case: Case, baseline_text: str, noreason_text: str) -> None:
    if case.check == "exact":
        assert _normalize(noreason_text) == "HELLO", (
            f"{case.id}: expected exact HELLO\nnoreason={noreason_text!r}"
        )
        return

    if case.check == "json":
        noreason_obj = json.loads(noreason_text)
        assert set(noreason_obj.keys()) == {"city", "country"}, (
            f"{case.id}: wrong json keys\nnoreason={noreason_obj!r}"
        )
        return

    baseline_norm = _normalize(baseline_text)
    noreason_norm = _normalize(noreason_text)
    assert noreason_norm, f"{case.id}: noreason output empty"
    if baseline_norm:
        sim = SequenceMatcher(None, baseline_norm, noreason_norm).ratio() * 100
        assert sim >= case.similarity_floor, (
            f"{case.id}: similarity {sim} < {case.similarity_floor}\n"
            f"baseline={baseline_text!r}\nnoreason={noreason_text!r}"
        )
    else:
        assert "<think>" not in noreason_text.lower(), (
            f"{case.id}: noreason output leaked think tags\nnoreason={noreason_text!r}"
        )


async def _run_think_baseline() -> None:
    """Single think-mode call to prove the model emits reasoning parts by default.

    Run once before per-case noreason checks — avoids repeating expensive
    think calls (which can loop for minutes on constrained prompts).
    """
    async with asyncio.timeout(_OLLAMA_WARM_TIMEOUT_S):
        await _timed_await(
            f"warm {_THINK_MODEL}",
            ensure_ollama_warm(_THINK_MODEL, _settings.llm.host),
        )

    async with asyncio.timeout(_THINK_CALL_TIMEOUT_S):
        baseline_text, baseline_thinking_parts = await _timed_await(
            f"call {_THINK_MODEL} default",
            _call_model(
                model_name=_THINK_MODEL,
                prompt="Reply with exactly: HELLO",
                extra_body=_BASE_EXTRA,
            ),
        )

    assert baseline_thinking_parts, (
        "think baseline: default path emitted no reasoning parts; expected ThinkingPart"
    )
    assert baseline_text.strip() or baseline_thinking_parts, (
        "think baseline: returned neither final text nor reasoning parts"
    )


async def _run_noreason_case(case: Case) -> None:
    """Noreason call on a single case — assert suppression + output correctness."""
    case_started = perf_counter()

    async with asyncio.timeout(_NOREASON_CALL_TIMEOUT_S):
        noreason_text, noreason_thinking_parts = await _timed_await(
            f"call {_THINK_MODEL} noreason",
            _call_model(
                model_name=_THINK_MODEL,
                prompt=case.prompt,
                extra_body=_THINK_EXTRA,
            ),
        )

    assert not noreason_thinking_parts, (
        f"{case.id}: noreason override still emitted reasoning parts: {noreason_thinking_parts}"
    )
    assert noreason_text.strip(), f"{case.id}: noreason think path returned empty text"

    _assert_case(case, "", noreason_text)
    case_elapsed = perf_counter() - case_started
    print(f"    case total: {case_elapsed:.2f}s", flush=True)


# ---------------------------------------------------------------------------
# Section 2: Production summarization pipeline integration
# ---------------------------------------------------------------------------


def _build_llm_model() -> LlmModel:
    """Build an LlmModel from the real settings via build_model()."""
    return build_model(_settings.llm)


def _check_build_model_produces_openai_model() -> None:
    """build_model() must produce an OpenAIChatModel for an ollama-openai provider."""
    llm_model = _build_llm_model()
    assert llm_model.model is not None, "build_model() returned no model"
    assert type(llm_model.model).__name__ == "OpenAIChatModel", (
        f"Expected OpenAIChatModel, got {type(llm_model.model).__name__}"
    )


async def _check_summarization_pipeline() -> None:
    """Production summarization pipeline must return non-empty content."""
    await ensure_ollama_warm(_THINK_MODEL, _settings.llm.host)
    llm_model = _build_llm_model()
    assert llm_model.model is not None

    messages: list[ModelMessage] = [
        ModelRequest(
            parts=[UserPromptPart(content="Docker is a container runtime and packaging tool.")]
        ),
        ModelRequest(parts=[UserPromptPart(content="Summarize in one short sentence.")]),
    ]
    async with asyncio.timeout(EVAL_SUMMARIZATION_TIMEOUT_SECS):
        summary = await summarize_messages(messages, llm_model.model, NOREASON_SETTINGS)

    assert summary is not None, "Summarization pipeline returned None"
    assert summary.strip(), "Summarization pipeline returned empty content"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _require_ollama_provider() -> bool:
    if _settings.llm.provider != "ollama-openai":
        print("SKIP: Ollama not configured")
        return False
    return True


async def _main() -> int:
    print("=" * 60)
    print("  Eval: Ollama noreason suppression + summarization pipeline")
    print("=" * 60)

    if not _require_ollama_provider():
        return 0

    try:
        # Part 1: single think baseline proves reasoning is active
        total = len(_CASES) + 3
        print(f"\n[1/{total}] think baseline (proves reasoning active)...", flush=True)
        await _run_think_baseline()
        print("PASS", flush=True)

        # Part 2: noreason suppression per case
        for idx, case in enumerate(_CASES, start=2):
            print(f"\n[{idx}/{total}] noreason: {case.id}...", flush=True)
            await _run_noreason_case(case)
            print("PASS", flush=True)

        # Part 3: build_model + pipeline integration
        pipe_idx = len(_CASES) + 2
        print(f"\n[{pipe_idx}/{total}] build_model produces OpenAI model...", flush=True)
        _check_build_model_produces_openai_model()
        print("PASS", flush=True)

        print(f"\n[{total}/{total}] summarization pipeline returns content...", flush=True)
        await _check_summarization_pipeline()
        print("PASS", flush=True)

    except AssertionError as exc:
        print(f"FAIL: {exc}", flush=True)
        return 1
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", flush=True)
        return 1

    print("\nVERDICT: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
