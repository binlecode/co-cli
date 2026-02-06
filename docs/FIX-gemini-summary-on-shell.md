# Issue: Gemini Summarizes Shell Tool Output

**Status:** Open
**Severity:** UX (not a bug)
**Affected Provider:** Gemini

## Problem

When using Gemini as the LLM provider, shell command output is summarized instead of displayed directly.

**Example:**
```
Co > list files
Co is thinking...
Execute command: ls -la? [y/n]: y
Here are the files.   <-- Expected: actual file listing
```

## Root Cause

Gemini didn't technically fail - it executed the command correctly but **summarized the output** instead of displaying it.

### Model Behavior

1. **Gemini is conversational by design** - trained to be helpful and natural, not to act as a terminal proxy
2. **System prompts are suggestions, not commands** - models can ignore instructions
3. **Different model training objectives** - Gemini optimizes for "helpfulness" which it interprets as summarizing

### Evidence the Tool Works

```python
# Direct sandbox test - full output returned:
>>> from co_cli.sandbox import Sandbox
>>> s = Sandbox()
>>> s.run_command('ls -la')
'total 580\ndrwxr-xr-x 23 root root...'  # Full listing
```

The model receives this full output, but chooses to say "Here are the files." instead.

## Why Other Models May Behave Differently

Ollama models (e.g., GLM, Llama) might:
- Follow instructions more literally
- Have different training objectives
- Have less "personality" overlay

## Potential Fixes

### Option 1: Streaming with `agent.iter()` (Recommended)

Print tool output directly as it executes, bypassing model summarization.

```python
async def chat_loop():
    async with agent.iter(user_input, deps=deps) as agent_run:
        async for node in agent_run:
            if isinstance(node, CallToolsNode):
                # Print tool output directly
                for part in node.model_response.parts:
                    if isinstance(part, ToolReturnPart):
                        console.print(part.content)

        # Then print model's final response
        console.print(agent_run.result.output)
```

**Pros:** User sees raw output immediately
**Cons:** More complex implementation

### Option 2: Use a Different Model

Test with Ollama or other providers that follow instructions more strictly.

```json
{
  "llm_provider": "ollama",
  "ollama_model": "glm-4.7-flash:q8_0"
}
```

### Option 3: Post-Process Tool Results

Extract tool results from the agent run and display them separately.

```python
result = await agent.run(user_input, deps=deps)
# Access tool results from result.all_messages()
# Display them before the model's response
```

### Option 4: Accept Model Behavior

The command executed successfully. The model summarizing is annoying but not broken.

## Decision

**Deferred to Batch 5+** - Implement streaming with `agent.iter()` for direct tool output display.

For now, users can:
1. Switch to Ollama for more literal output
2. Accept Gemini's summarization behavior
