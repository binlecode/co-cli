"""Personality prompt debugger — diagnostic output for ``co debug-personality``.

Shows what personality content is injected at each layer of the prompt
assembly pipeline. Useful for verifying that a preset produces the
expected system prompt without starting a full chat session.
"""

from co_cli.display import console
from co_cli.prompts.personalities._registry import PRESETS
from co_cli.prompts.personalities._composer import get_soul_seed, compose_personality
from co_cli.prompts import assemble_prompt
from co_cli.prompts.model_quirks import get_counter_steering
from co_cli._history import _PERSONALITY_COMPACTION_ADDENDUM
from co_cli.config import settings


def run_debug_personality(preset: str | None = None) -> None:
    """Print personality diagnostic layers to the console.

    Args:
        preset: Override personality preset name. Defaults to the
            currently configured personality from settings.
    """
    name = preset or settings.personality
    if not name:
        console.print("[warning]No personality configured.[/warning]")
        return

    # 1. Preset metadata
    console.rule(f"[accent]Personality debugger: {name}[/accent]")

    if name not in PRESETS:
        console.print(f"[error]Unknown preset: {name}[/error]")
        console.print(f"[hint]Valid presets: {', '.join(PRESETS.keys())}[/hint]")
        return

    preset_def = PRESETS[name]
    console.print(f"\n[info]Preset:[/info]  {name}")
    console.print(f"[info]Character axis:[/info]  {preset_def['character'] or 'None'}")
    console.print(f"[info]Style axis:[/info]  {preset_def['style']}")

    # 2. Soul seed
    console.print()
    console.rule("[accent]Soul seed[/accent]")
    try:
        seed = get_soul_seed(name)
        console.print(seed)
    except FileNotFoundError as e:
        console.print(f"[warning]{e}[/warning]")

    # 3. Character axis
    console.print()
    console.rule("[accent]Character axis[/accent]")
    character = preset_def["character"]
    if character:
        from pathlib import Path
        char_file = Path(__file__).parent / "prompts" / "personalities" / "character" / f"{character}.md"
        if char_file.exists():
            console.print(char_file.read_text(encoding="utf-8").strip())
        else:
            console.print(f"[warning]Character file not found: {char_file}[/warning]")
    else:
        console.print("[dim]None (style-only preset)[/dim]")

    # 4. Style axis
    console.print()
    console.rule("[accent]Style axis[/accent]")
    style = preset_def["style"]
    from pathlib import Path
    style_file = Path(__file__).parent / "prompts" / "personalities" / "style" / f"{style}.md"
    if style_file.exists():
        console.print(style_file.read_text(encoding="utf-8").strip())
    else:
        console.print(f"[warning]Style file not found: {style_file}[/warning]")

    # 5. Compaction addendum
    console.print()
    console.rule("[accent]Compaction addendum[/accent]")
    console.print(_PERSONALITY_COMPACTION_ADDENDUM.strip())

    # 6. System prompt manifest
    console.print()
    console.rule("[accent]System prompt manifest[/accent]")
    provider = settings.llm_provider
    model_name = (
        settings.gemini_model if provider == "gemini"
        else settings.ollama_model.split(":")[0]
    )
    try:
        _prompt, manifest = assemble_prompt(provider, model_name, name)
        console.print(f"[info]Parts loaded:[/info]  {', '.join(manifest.parts_loaded)}")
        console.print(f"[info]Total chars:[/info]  {manifest.total_chars:,}")
        if manifest.warnings:
            for w in manifest.warnings:
                console.print(f"[warning]  {w}[/warning]")
    except Exception as e:
        console.print(f"[error]Assembly failed: {e}[/error]")

    # 7. Quirk counter-steering
    console.print()
    console.rule("[accent]Quirk counter-steering[/accent]")
    steering = get_counter_steering(provider, model_name)
    if steering:
        console.print(steering)
    else:
        console.print("[dim]None (no model-specific counter-steering)[/dim]")

    console.print()
