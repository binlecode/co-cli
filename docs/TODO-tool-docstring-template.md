# TODO: Tool Docstring Template

Standard template for writing tool docstrings that help LLMs use tools effectively. Derived from patterns across Anthropic, OpenAI, Google ADK, Gemini CLI, Claude Code, and Codex.

## The Problem

LLMs choose tools and parameters based on the docstring. A weak docstring leads to wrong tool selection, missing parameters, and failure to use capabilities like pagination. Adding case-by-case agent prompt guidance is a band-aid that bloats the system prompt. The tool must be self-describing.

## The Template

Every tool docstring covers four dimensions, scaled by complexity. Simple tools may cover only dimensions 1-2 in a single line. Complex tools cover all four with structured subsections.

### Dimension 1 — What It Does

One action sentence. Verb + data source + return shape.

```
Search files in Google Drive by name or content. Returns up to 10 results per page.
```

```
Searches for a regular expression pattern within file contents. Max 100 matches.
```

Not: "A tool for searching." Not: "This function searches Google Drive."

### Dimension 2 — What It Returns

Explicit field list so the LLM knows which fields to read, relay, or act on.

```
Returns a dict with:
- display: pre-formatted results with clickable URLs — show directly to user
- page: current page number
- has_more: whether more results exist (paginate if you need complete data)
```

For tools returning plain strings, a one-liner suffices:

```
Returns the combined stdout and stderr output as a string.
```

### Dimension 3 — When / How to Use

Behavioral guidance. This is where most docstrings fall short. Use whichever sub-patterns apply:

**Cross-tool references** — tell the LLM what to combine with this tool:

```
To read a file's content, pass its id to read_drive_file.
For full page content, pass a result URL to web_fetch.
```

**When-to-use vs alternatives** — prevent wrong tool selection:

```
Use this for a quick inbox overview. For targeted queries (by sender,
date range, subject), use search_emails instead.
```

**Pagination / looping** — describe as a capability, not a user-driven action:

```
Keep paginating until has_more is false when the task requires complete
results (counts, summaries, exhaustive listings).
```

**Use-case enumeration** — for complex tools, list concrete scenarios as bullets (pattern from Gemini CLI ReadManyFiles, Memory):

```
Use this tool:
- When the user explicitly asks to remember something
- When the user states a clear preference or fact worth retaining

Do NOT use this tool:
- To remember conversational context only relevant for the current session
- To save long, rambling text — facts should be short and to the point
- If unsure whether the info is worth remembering — ask the user first
```

**Scope boundaries** — explicit "Does NOT" statements (pattern from Claude Code):

```
Creates a plain-text draft. Does NOT send — the user reviews and sends
manually from Gmail.
```

**Conditional behavior** — document how parameters change behavior (pattern from Gemini CLI edit):

```
By default, replaces a single occurrence, but can replace multiple
occurrences when expected_replacements is specified.
```

**Fallback guidance** — what to do when uncertain or when the tool fails:

```
If fetch returns 403 or is blocked, retry the same URL with
run_shell_command: curl -sL <url>.
```

### Dimension 4 — Caveats

Limits, failure modes, and things that silently go wrong.

```
Caveats:
- fullText search only works on Google Workspace docs, not PDFs or binaries
- Content is truncated at ~100K characters
- No interactive input — commands that prompt for stdin will hang and timeout
```

## Scaling by Complexity

Match docstring length to tool complexity. Over-documenting simple tools adds noise; under-documenting complex tools causes misuse.

| Tool type | Lines | What to cover | Reference |
|---|---|---|---|
| Trivial read-only | 1-2 | Dimension 1 only | Gemini CLI `grep`: 1 line |
| Simple list/read | 4-8 | Dims 1-2 + cross-tool ref | Gemini CLI `glob`, `read_file` |
| Medium search | 8-15 | All four, light caveats | co-cli `web_search`, `search_emails` |
| Complex stateful | 15-25 | All four, DO/DO NOT, use-case list | Gemini CLI `memory`: 30 lines |
| Dangerous side-effect | 15-25 | All four, strong caveats, fallback | Gemini CLI `shell`: 18 lines |
| Multi-mode (edit) | 25-40 | All four, numbered rules, emphasis | Gemini CLI `edit`: 40 lines |

## Parameter Descriptions

**Type context + example value** for every parameter:

```
query: Search keywords (e.g. "weekly meeting", "Q4 budget report").
page: Page number (1-based). Use 1 for first page, 2 for next, etc.
days_ahead: Days ahead to include (default 1 = today only). Use 7 for a week.
timeout: Max seconds to wait (default 120). Increase for builds (e.g. 300).
```

Not: `query: The query.` Not: `page: Page number.`

**Document parameter interactions** when one parameter changes another's behavior:

```
expected_replacements: Number of occurrences to replace. When set, the tool
    replaces ALL matches instead of just the first.
```

**Use explicit units** in parameter names when ambiguous (pattern from Codex):

```
timeout_ms: Max wait time in milliseconds.
```

## Emphasis Conventions

Use CAPS for hard constraints the LLM must never violate (pattern from Gemini CLI edit):

```
old_string MUST be the exact literal text to replace.
NEVER escape old_string or new_string.
CRITICAL: Must uniquely identify the single instance to change.
```

Reserve CAPS for safety-critical or data-loss scenarios. Overuse dilutes the signal.

## Response-Embedded Guidance

For paginated or truncated tools, embed next-action hints in the return value itself, not just the docstring (pattern from Gemini CLI ReadFile):

```
IMPORTANT: The file content has been truncated.
Status: Showing lines 1-100 of 500 total lines.
Action: To read more of the file, use offset: 100.
```

This catches the LLM even if it didn't fully internalize the docstring.

## Anti-Patterns

- **Passive pagination**: "When the user asks for more, call with page + 1" teaches the LLM to wait instead of loop. Describe pagination as a capability.
- **Missing cross-tool refs**: If tool A produces IDs that tool B consumes, both docstrings must say so. The LLM has no other way to discover the connection.
- **Undocumented limits**: If max_results is capped at 8 regardless of input, say so. Silent caps cause the LLM to believe it got complete results.
- **Over-documenting simple tools**: A grep tool does not need 15 lines. Match length to complexity — noise hurts tool selection as much as missing info.
- **`ctx` in Args**: Do not document the `ctx: RunContext[CoDeps]` parameter — it is framework-injected and invisible to the LLM.
- **Vague scope**: If a tool creates a draft but does not send it, say so explicitly. Omitting scope boundaries leads to incorrect assumptions.

## Sources

- Anthropic: "Provide extremely detailed descriptions. This is by far the most important factor in tool performance. Aim for at least 3-4 sentences, more if complex."
- Anthropic engineering: "Even small refinements to tool descriptions can yield dramatic improvements."
- Google ADK: "The docstring is the single most critical factor" in tool selection.
- Gemini CLI: DO/DO NOT lists, use-case enumeration, conditional behavior documentation, CAPS emphasis, 1-line to 40-line scaling range, response-embedded pagination hints.
- Claude Code: explicit scope boundaries ("Does NOT..."), trigger-phrase enumeration, progressive disclosure (summary → body → reference files).
- Codex: platform-specific examples, explicit units in parameter names, dual tool variants for different model capabilities.
- Aider: mandatory explanation parameter forces LLM to reason before acting.
