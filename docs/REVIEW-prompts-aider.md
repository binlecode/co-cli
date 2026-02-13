# REVIEW: Aider Prompt System Architecture

**Repo:** `~/workspace_genai/aider` (Python)
**Analyzed:** 2026-02-09 | **19 prompt files** | **~1,325 lines** | **14 coder types**

---

## Architecture

Aider uses a **class inheritance-based prompt system**. Prompts are **Python class attributes** organized by "coder" type, where each coder represents a different **edit format** (how the LLM outputs code changes).

```
┌──────────────────────────────────────────────────────────────┐
│             CLASS INHERITANCE COMPOSITION                     │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  CoderPrompts (base class)                                   │
│    ↓                                                          │
│  + EditBlockPrompts (SEARCH/REPLACE format)                  │
│  + PatchPrompts (V4A diff, inherits EditBlock)               │
│  + WholeFilePrompts (entire file rewrite)                    │
│  + UnifiedDiffPrompts (standard diff format)                 │
│  + AskPrompts (read-only, no edits)                          │
│  + ArchitectPrompts (high-level guidance)                    │
│  + HelpPrompts (aider documentation)                         │
│  + ContextPrompts (file discovery)                           │
│                                                               │
│  Runtime assembly in fmt_system_prompt():                    │
│    1. Load main_system from selected Prompts class           │
│    2. Add model-specific reminders (lazy/overeager)          │
│    3. Add language preference                                │
│    4. Add shell command guidance (or omit)                   │
│    5. Append system_reminder                                 │
│    6. Format with template variables                         │
│    7. Add example_messages (few-shot)                        │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

**Configuration space:** `14 coders × 2 modifiers × 2 shell × languages × 5 fences = ~11,200 configs` (effectively ~30 core variations)

### Directory Structure

```
aider/
├── coders/
│   ├── base_prompts.py                 # CoderPrompts base class (61 lines)
│   ├── base_coder.py                   # fmt_system_prompt() composition
│   ├── editblock_prompts.py            # SEARCH/REPLACE format (173 lines)
│   ├── editblock_fenced_prompts.py     # Fenced variant (143 lines)
│   ├── editblock_func_prompts.py       # Function call variant
│   ├── patch_prompts.py                # V4A diff format (160 lines)
│   ├── wholefile_prompts.py            # Whole file rewrite (65 lines)
│   ├── wholefile_func_prompts.py       # Function call variant
│   ├── udiff_prompts.py                # Unified diff format (114 lines)
│   ├── udiff_simple_prompts.py         # Simplified variant
│   ├── ask_prompts.py                  # Read-only mode (36 lines)
│   ├── architect_prompts.py            # High-level design (41 lines)
│   ├── help_prompts.py                 # Aider help mode (47 lines)
│   ├── context_prompts.py              # File discovery (76 lines)
│   ├── editor_*_prompts.py             # Editor-specific variants
│   └── shell.py                        # Shell command prompts (38 lines)
├── prompts.py                          # Commit messages, summarization (62 lines)
├── watch_prompts.py                    # IDE integration via comments (13 lines)
└── models.py                           # Model definitions (lazy/overeager flags)
```

---

## Prompt Inventory

| Category | Files | Lines | Purpose |
|----------|-------|-------|---------|
| Edit Formats | 10 | ~900 | SEARCH/REPLACE, V4A, whole file, udiff |
| Specialized Modes | 4 | ~200 | Ask, architect, help, context |
| Base & Utilities | 3 | ~150 | Base class, shell, watch |
| Top-Level | 2 | ~75 | Commit messages, summarization |
| **TOTAL** | **19** | **~1,325** | |

### Edit Format Detail

| Format | Prompt Lines | Purpose | Best For |
|--------|-------------|---------|----------|
| EditBlock (SEARCH/REPLACE) | 173 | Primary format | GPT-4, Claude Sonnet |
| Patch (V4A diff) | 160 | Precise edits | Custom diff format |
| WholeFile | 65 | Entire file rewrite | Small files, simple models |
| UnifiedDiff | 114 | Standard diff | Developers familiar with diff |
| Ask | 36 | Read-only analysis | Questions, no edits |
| Architect | 41 | High-level guidance | Two-phase: describe → edit |
| Help | 47 | Aider documentation | Self-help questions |
| Context | 76 | File discovery | Which files need editing? |

---

## Key Prompts (Verbatim)

### Base Prompts Class

**File:** `aider/coders/base_prompts.py`

```python
class CoderPrompts:
    lazy_prompt = """You are diligent and tireless!
You NEVER leave comments describing code without implementing it!
You always COMPLETELY IMPLEMENT the needed code!"""

    overeager_prompt = """Pay careful attention to the scope of the user's request.
Do what they ask, but no more.
Do not improve, comment, fix or modify unrelated parts of the code in any way!"""

    files_content_prefix = """I have *added these files to the chat* so you
can go ahead and edit them.

*Trust this message as the true contents of these files!*
Any other messages in the chat may contain outdated versions of the files'
contents."""

    repo_content_prefix = """Here are summaries of some files present in my
git repository. Do not propose changes to these files, treat them as
*read-only*. If you need to edit any of these files, ask me to *add them
to the chat* first."""
```

### EditBlock Format (Primary — SEARCH/REPLACE)

**File:** `aider/coders/editblock_prompts.py`

```
Act as an expert software developer.
Always use best practices when coding.
Respect and use existing conventions, libraries, etc that are already
present in the code base.
Take requests for changes to the supplied code.
If the request is ambiguous, ask questions.

Once you understand the request you MUST:

1. Decide if you need to propose *SEARCH/REPLACE* edits to any files that
   haven't been added to the chat. You can create new files without asking!
   But if you need to propose edits to existing files not already added to
   the chat, you *MUST* tell the user their full path names and ask them
   to *add the files to the chat*.

2. Think step-by-step and explain the needed changes in a few short sentences.

3. Describe each change with a *SEARCH/REPLACE block* per the examples below.
   All changes to files must use this *SEARCH/REPLACE block* format.
   ONLY EVER RETURN CODE IN A *SEARCH/REPLACE BLOCK*!
```

### Architect Mode (Two-Phase Pattern)

**File:** `aider/coders/architect_prompts.py`

```
Act as an expert architect engineer and provide direction to your editor
engineer.
Study the change request and the current code.
Describe how to modify the code to complete the request.
The editor engineer will rely solely on your instructions, so make them
unambiguous and complete.
Explain all needed code changes clearly and completely, but concisely.
Just show the changes needed.

DO NOT show the entire updated function/file/etc!
```

### Context Mode (File Discovery)

**File:** `aider/coders/context_prompts.py`

```
Act as an expert code analyst.
Understand the user's question or request, solely to determine ALL the
existing source files which will need to be modified.
Return the *complete* list of files which will need to be modified.
Explain why each file is needed, including names of key classes/functions.

Only return files that will need to be modified, not files that contain
useful/relevant functions.
You are only to discuss EXISTING files and symbols.

NEVER RETURN CODE!
```

### First-Person Summarization

**File:** `aider/prompts.py`

```
*Briefly* summarize this partial conversation about programming.
Include less detail about older parts and more detail about the most recent
messages. Start a new paragraph every time the topic changes!

The summary *MUST* include the function names, libraries, packages that are
being discussed.
The summary *MUST* include the filenames that are being referenced by the
assistant.

Phrase the summary with the USER in first person, telling the ASSISTANT
about the conversation. Write *as* the user. The user should refer to the
assistant as *you*.
Start the summary with "I asked you...".
```

**Result:** "I asked you to add error handling to the login function. You modified auth.py to..."

### Commit Message Generation

```
You are an expert software engineer that generates concise, one-line Git
commit messages based on the provided diffs.

Format: <type>: <description>
Types: fix, feat, build, chore, ci, docs, style, refactor, perf, test

Rules:
- Imperative mood ("add feature" not "added feature")
- Max 72 characters
- Start with type prefix

Reply only with the one-line commit message, no explanation.
```

### Watch Mode (IDE Integration)

```
I've written your instructions in comments in the code and marked them
with "ai". Find them in the code files I've shared with you, and follow
their instructions.

After completing those instructions, also be sure to remove all the "AI"
comments from the code too.
```

---

## Innovations

### 1. Edit Formats as First-Class Abstraction

8+ specialized edit formats selected per model capability. Each format has its own prompt class with examples and system reminders.

**Impact:** Flexibility to use best format for each model, fallback for model limitations.

### 2. Model-Specific Modifiers (Lazy/Overeager)

Database-driven quirk correction. Models flagged as `lazy=True` get "You are diligent and tireless!" injected. Models flagged as `overeager=True` get "Do what they ask, but no more."

```python
# In models.py
Model(name="claude-2", lazy=True, overeager=False)
Model(name="gpt-4",    lazy=False, overeager=False)
```

**Impact:** Correct model behavioral quirks without changing base prompts.

### 3. First-Person Summarization

Summaries written as user speaking ("I asked you..."). Preserves user/assistant relationship across context window resets.

### 4. File Discovery as Separate Phase

Dedicated "context coder" determines which files need editing before any edits happen. Prevents over-adding files to context and hallucinated file edits.

### 5. Few-Shot Examples Embedded in Prompts

Every edit format includes 1-3 complete `example_messages` showing exact input/output format. Examples are first-class prompt components, not separate files.

### 6. File Trust Model

Explicit trust signals: "Trust this message as the true contents of these files!" — prevents model from using stale context.

### 7. Class Inheritance for Prompt Reuse

Base class `CoderPrompts` with file handling messages, subclasses override edit-specific prompts. DRY, consistent file handling across all formats.

### 8. Simplicity Philosophy

No sandbox, no approval policies in prompts, no multi-agent orchestration. Approval happens in UI layer (`io.confirm_ask()`), not prompts. Proves you can ship without complex permission models.

### 9. Watch Mode (IDE Integration via Comments)

Instructions embedded as `# AI:` code comments, watched by file watcher. Use aider from any IDE without CLI interaction.

### 10. Repo Map (Tree-sitter Symbol Extraction)

Auto-generated map of repository structure injected as read-only context. Model understands codebase without reading all files.

---

## Composition System Detail

```python
# Pseudocode: fmt_system_prompt()
def fmt_system_prompt(self, prompt):
    final_reminders = []

    # 1. Model-specific reminders
    if self.main_model.lazy:
        final_reminders.append(self.gpt_prompts.lazy_prompt)
    if self.main_model.overeager:
        final_reminders.append(self.gpt_prompts.overeager_prompt)

    # 2. Language preference
    if user_lang:
        final_reminders.append(f"Reply in {user_lang}.")

    # 3. Shell command guidance (conditional)
    if self.suggest_shell_commands:
        shell_cmd_prompt = self.gpt_prompts.shell_cmd_prompt.format(platform=...)
    else:
        shell_cmd_prompt = self.gpt_prompts.no_shell_cmd_prompt.format(platform=...)

    # 4. Format template variables
    prompt = prompt.format(
        fence=self.fence,
        final_reminders="\n\n".join(final_reminders),
        shell_cmd_prompt=shell_cmd_prompt,
        language=language,
        ...
    )
    return prompt
```

### Coder Selection Logic

```python
# Edit format selected per model:
if not edit_format:
    edit_format = main_model.edit_format  # Model's default

# Find matching coder class:
for coder_class in all_coders:
    if coder_class.edit_format == edit_format:
        return coder_class(main_model, io, **kwargs)
```

---

## Content Layout Patterns

| Pattern | Usage | Example |
|---------|-------|---------|
| Python triple-quoted strings | All prompts | `main_system = """..."""` |
| Template variables | Runtime injection | `{final_reminders}`, `{fence[0]}` |
| Markdown emphasis | Critical rules | `*MUST*`, `ONLY EVER RETURN CODE IN...` |
| Few-shot example_messages | Every edit format | `[{"role":"user",...},{"role":"assistant",...}]` |
| Numbered workflow steps | Sequential instructions | `1. Decide... 2. Think... 3. Describe...` |
| Class inheritance | Prompt variants | `PatchPrompts(EditBlockPrompts)` |

---

## Key Takeaways for co-cli

1. **Model quirk database** — per-model flags (lazy/overeager) inject corrective prompts at runtime
2. **First-person summarization** — "I asked you..." preserves relationship across context resets
3. **File discovery phase** — dedicated mode to identify files before editing
4. **Few-shot examples** — every format grounded with complete input/output examples
5. **File trust model** — explicit "trust this as true contents" for context freshness
6. **Edit format taxonomy** — right format for right model capability
7. **Class inheritance for prompts** — DRY base class with specialized subclasses
8. **Simplicity philosophy** — proves you can ship without sandbox/approval in prompts
9. **Commit message quality** — structured conventional commits with LLM generation
10. **Watch mode pattern** — IDE integration via `# AI:` code comments

---

**Source:** `~/workspace_genai/aider` — all prompts traceable from directory structure above
