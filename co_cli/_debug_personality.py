"""Personality prompt debugger — diagnostic output for ``co debug-personality``.

Shows what personality content is injected at each layer of the prompt
assembly pipeline. Useful for verifying that a role produces the
expected system prompt without starting a full chat session.
"""

from co_cli.display import console
from co_cli.prompts.personalities._composer import (
    VALID_PERSONALITIES,
    load_soul,
    load_traits,
    compose_personality,
)
from co_cli.prompts import assemble_prompt
from co_cli.prompts.model_quirks import get_counter_steering
from co_cli._history import _PERSONALITY_COMPACTION_ADDENDUM
from co_cli.config import settings


def run_debug_personality(preset: str | None = None, depth: str = "normal") -> None:
    """Print personality diagnostic layers to the console.

    Args:
        preset: Override personality role name. Defaults to the
            currently configured personality from settings.
        depth: Reasoning depth to use for composition diagnostics.
            Defaults to ``"normal"`` (no overrides). Pass the session's
            ``CoDeps.reasoning_depth`` to see the assembled block as it
            would appear at runtime.
    """
    name = preset or settings.personality
    if not name:
        console.print("[warning]No personality configured.[/warning]")
        return

    console.rule(f"[accent]Personality debugger: {name} (depth={depth})[/accent]")

    if name not in VALID_PERSONALITIES:
        console.print(f"[error]Unknown role: {name}[/error]")
        console.print(f"[hint]Valid roles: {', '.join(VALID_PERSONALITIES)}[/hint]")
        return

    # 1. Traits wiring (show overrides applied by depth)
    from co_cli.prompts._reasoning_depth_override import _DEPTH_OVERRIDES, VALID_DEPTHS
    console.print(f"\n[info]Role:[/info]  {name}")
    console.print(f"[info]Depth:[/info]  {depth}")
    traits = load_traits(name)
    depth_overrides = _DEPTH_OVERRIDES.get(depth, {})
    for trait_name, trait_value in traits.items():
        label = trait_name.replace("_", " ").title()
        effective = depth_overrides.get(trait_name, trait_value)
        if effective != trait_value:
            console.print(f"[info]{label}:[/info]  {trait_value} → [accent]{effective}[/accent] (depth override)")
        else:
            console.print(f"[info]{label}:[/info]  {effective}")

    # 2. Soul
    console.print()
    console.rule("[accent]Soul[/accent]")
    try:
        soul = load_soul(name)
        console.print(soul)
    except FileNotFoundError as e:
        console.print(f"[warning]{e}[/warning]")

    # 3. Behavior files (using effective trait values after depth overrides)
    console.print()
    console.rule("[accent]Behaviors[/accent]")
    from pathlib import Path
    behaviors_dir = Path(__file__).parent / "prompts" / "personalities" / "behaviors"
    effective_traits = dict(traits)
    effective_traits.update(depth_overrides)
    for trait_name, trait_value in effective_traits.items():
        behavior_file = behaviors_dir / f"{trait_name}-{trait_value}.md"
        if behavior_file.exists():
            content = behavior_file.read_text(encoding="utf-8").strip()
            console.print(f"\n{content}")
        else:
            console.print(f"[warning]Missing: {behavior_file.name}[/warning]")

    # 4. Composed personality block
    console.print()
    console.rule("[accent]Composed personality (full ## Soul block)[/accent]")
    composed = compose_personality(name, depth)
    console.print(f"[dim]{len(composed)} chars[/dim]")

    # 5. Compaction addendum
    console.print()
    console.rule("[accent]Compaction addendum[/accent]")
    console.print(_PERSONALITY_COMPACTION_ADDENDUM.strip())

    # 6. Static system prompt manifest
    console.print()
    console.rule("[accent]Static system prompt manifest[/accent]")
    provider = settings.llm_provider
    model_name = (
        settings.gemini_model if provider == "gemini"
        else settings.ollama_model.split(":")[0]
    )
    try:
        _prompt, manifest = assemble_prompt(provider, model_name)
        console.print(f"[info]Parts loaded:[/info]  {', '.join(manifest.parts_loaded)}")
        console.print(f"[info]Static chars:[/info]  {manifest.total_chars:,}")
        console.print(f"[info]Personality chars:[/info]  {len(composed):,}")
        console.print(f"[info]Total chars:[/info]  {manifest.total_chars + len(composed):,}")
        if manifest.warnings:
            for w in manifest.warnings:
                console.print(f"[warning]  {w}[/warning]")
    except Exception as e:
        console.print(f"[error]Assembly failed: {e}[/error]")

    # 7. Delivery model
    console.print()
    console.rule("[accent]Delivery model[/accent]")
    console.print(
        "[info]Delivery:[/info]  Structural per-turn (@agent.system_prompt)\n"
        "[info]Static prompt:[/info]  instructions + rules + quirks\n"
        "[info]Per-turn:[/info]  personality (soul + behaviors + mandate)"
    )

    # 8. Quirk counter-steering
    console.print()
    console.rule("[accent]Quirk counter-steering[/accent]")
    steering = get_counter_steering(provider, model_name)
    if steering:
        console.print(steering)
    else:
        console.print("[dim]None (no model-specific counter-steering)[/dim]")

    console.print()
