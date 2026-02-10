# REVIEW: Aider Prompt System Architecture

**Repository:** `~/workspace_genai/aider` (Python)
**Analysis Date:** 2026-02-09

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Prompt Structure & Modularization](#prompt-structure--modularization)
3. [Content Layout Patterns](#content-layout-patterns)
4. [Dynamic Composition System](#dynamic-composition-system)
5. [Complete Prompt Inventory](#complete-prompt-inventory)
6. [Design Principles & Innovations](#design-principles--innovations)
7. [Key Takeaways for co-cli](#key-takeaways-for-co-cli)

---

## Architecture Overview

### High-Level Design

Aider uses a **class inheritance-based prompt system** implemented in Python. Unlike Codex (multi-file markdown) or Gemini CLI (conditional TypeScript), Aider embeds prompts as **string attributes in Python classes**, organized by "coder" type (edit format).

**Key Differentiator:** Prompts are **Python class attributes**, not external files. Each "coder" represents a different **edit format** (how the LLM outputs code changes).

```
┌─────────────────────────────────────────────────────────────┐
│             CLASS INHERITANCE COMPOSITION                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  CoderPrompts (base class)                                  │
│    ↓                                                         │
│  + EditBlockPrompts (SEARCH/REPLACE edit format)            │
│    ↓                                                         │
│  + PatchPrompts (V4A diff format, inherits EditBlock)       │
│                                                              │
│  CoderPrompts (base class)                                  │
│    ↓                                                         │
│  + WholeFilePrompts (return entire file)                    │
│    ↓                                                         │
│  + UnifiedDiffPrompts (unified diff format)                 │
│                                                              │
│  CoderPrompts (base class)                                  │
│    ↓                                                         │
│  + AskPrompts (read-only, no edits)                         │
│  + ArchitectPrompts (high-level guidance)                   │
│  + HelpPrompts (aider documentation)                        │
│  + ContextPrompts (file discovery)                          │
│                                                              │
│  Runtime assembly in fmt_system_prompt():                   │
│    1. Load main_system from selected Prompts class          │
│    2. Add model-specific reminders (lazy/overeager)         │
│    3. Add language preference                               │
│    4. Add shell command guidance (or omit)                  │
│    5. Append system_reminder                                │
│    6. Format with template variables                        │
│    7. Add example_messages (few-shot)                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Directory Structure

```
aider/
├── coders/
│   ├── base_prompts.py                 # CoderPrompts base class
│   ├── base_coder.py                   # Coder class with fmt_system_prompt()
│   │
│   ├── editblock_prompts.py            # SEARCH/REPLACE format
│   ├── editblock_coder.py              # EditBlockCoder implementation
│   ├── editblock_fenced_prompts.py     # Fenced variant
│   ├── editblock_func_prompts.py       # Function call variant
│   │
│   ├── patch_prompts.py                # V4A diff format
│   ├── patch_coder.py                  # PatchCoder implementation
│   │
│   ├── wholefile_prompts.py            # Whole file rewrite
│   ├── wholefile_coder.py              # WholeFileCoder implementation
│   ├── wholefile_func_prompts.py       # Function call variant
│   │
│   ├── udiff_prompts.py                # Unified diff format
│   ├── udiff_coder.py                  # UnifiedDiffCoder implementation
│   ├── udiff_simple_prompts.py         # Simplified variant
│   │
│   ├── ask_prompts.py                  # Read-only mode
│   ├── ask_coder.py                    # AskCoder implementation
│   │
│   ├── architect_prompts.py            # High-level design mode
│   ├── architect_coder.py              # ArchitectCoder implementation
│   │
│   ├── help_prompts.py                 # Aider help mode
│   ├── help_coder.py                   # HelpCoder implementation
│   │
│   ├── context_prompts.py              # File discovery mode
│   ├── context_coder.py                # ContextCoder implementation
│   │
│   ├── editor_*_prompts.py             # Editor-specific variants
│   ├── editor_*_coder.py               # (EditorEditBlock, EditorWhole, etc.)
│   │
│   └── shell.py                        # Shell command prompts
│
├── prompts.py                          # Top-level prompts (commit, summarize)
├── watch_prompts.py                    # Watch mode (IDE integration)
├── models.py                           # Model definitions (lazy, overeager flags)
└── io.py                               # User confirmation (no prompt-level approval)
```

**Total Prompt Files:** 19 `*_prompts.py` files
**Total Coder Types:** 14 (HelpCoder, AskCoder, EditBlockCoder, PatchCoder, WholeFileCoder, etc.)

---

## Prompt Structure & Modularization

### 1. Base Prompts Class (`base_prompts.py`)

**File:** `aider/coders/base_prompts.py`
**Size:** 61 lines
**Pattern:** Python class with string attributes

```python
class CoderPrompts:
    system_reminder = ""

    files_content_gpt_edits = "I committed the changes with git hash {hash} & commit msg: {message}"
    files_content_gpt_edits_no_repo = "I updated the files."
    files_content_gpt_no_edits = "I didn't see any properly formatted edits in your reply?!"
    files_content_local_edits = "I edited the files myself."

    lazy_prompt = """You are diligent and tireless!
You NEVER leave comments describing code without implementing it!
You always COMPLETELY IMPLEMENT the needed code!
"""

    overeager_prompt = """Pay careful attention to the scope of the user's request.
Do what they ask, but no more.
Do not improve, comment, fix or modify unrelated parts of the code in any way!
"""

    example_messages = []

    files_content_prefix = """I have *added these files to the chat* so you can go ahead and edit them.

*Trust this message as the true contents of these files!*
Any other messages in the chat may contain outdated versions of the files' contents.
"""

    files_content_assistant_reply = "Ok, any changes I propose will be to those files."

    files_no_full_files = "I am not sharing any files that you can edit yet."

    files_no_full_files_with_repo_map = """Don't try and edit any existing code without asking me to add the files to the chat!
Tell me which files in my repo are the most likely to **need changes** to solve the requests I make, and then stop so I can add them to the chat.
Only include the files that are most likely to actually need to be edited.
Don't include files that might contain relevant context, just files that will need to be changed.
"""

    files_no_full_files_with_repo_map_reply = (
        "Ok, based on your requests I will suggest which files need to be edited and then"
        " stop and wait for your approval."
    )

    repo_content_prefix = """Here are summaries of some files present in my git repository.
Do not propose changes to these files, treat them as *read-only*.
If you need to edit any of these files, ask me to *add them to the chat* first.
"""

    read_only_files_prefix = """Here are some READ ONLY files, provided for your reference.
Do not edit these files!
"""

    shell_cmd_prompt = ""
    shell_cmd_reminder = ""
    no_shell_cmd_prompt = ""
    no_shell_cmd_reminder = ""

    rename_with_shell = ""
    go_ahead_tip = ""
```

**Design:** Minimal base class, all subclasses override specific attributes

### 2. Edit Format Prompts (Primary Coders)

#### EditBlock Format (`editblock_prompts.py`)

**File:** `aider/coders/editblock_prompts.py`
**Size:** 173 lines
**Edit Format:** SEARCH/REPLACE blocks

**Main System Prompt:**
```python
main_system = """Act as an expert software developer.
Always use best practices when coding.
Respect and use existing conventions, libraries, etc that are already present in the code base.
{final_reminders}
Take requests for changes to the supplied code.
If the request is ambiguous, ask questions.

Once you understand the request you MUST:

1. Decide if you need to propose *SEARCH/REPLACE* edits to any files that haven't been added to the chat. You can create new files without asking!

But if you need to propose edits to existing files not already added to the chat, you *MUST* tell the user their full path names and ask them to *add the files to the chat*.
End your reply and wait for their approval.
You can keep asking if you then decide you need to edit more files.

2. Think step-by-step and explain the needed changes in a few short sentences.

3. Describe each change with a *SEARCH/REPLACE block* per the examples below.

All changes to files must use this *SEARCH/REPLACE block* format.
ONLY EVER RETURN CODE IN A *SEARCH/REPLACE BLOCK*!
{shell_cmd_prompt}
"""
```

**Example Messages:** 2 detailed examples with SEARCH/REPLACE blocks

**System Reminder:** 40+ lines of detailed rules
- Full file path on first line
- `<<<<<<< SEARCH` / `=======` / `>>>>>>> REPLACE` structure
- Exact character-for-character matching required
- Concise blocks, break large changes into series
- Create new files with empty SEARCH section

**Innovation:** Git merge-conflict-style syntax for code edits

#### Patch Format (`patch_prompts.py`)

**File:** `aider/coders/patch_prompts.py`
**Size:** 160 lines
**Edit Format:** V4A diff format
**Inherits:** EditBlockPrompts (shares base structure)

**Main System Prompt:**
```python
main_system = """Act as an expert software developer.
Always use best practices when coding.
Respect and use existing conventions, libraries, etc that are already present in the code base.
{final_reminders}
Take requests for changes to the supplied code.
If the request is ambiguous, ask questions.

Once you understand the request you MUST:

1. Decide if you need to propose edits to any files that haven't been added to the chat. You can create new files without asking!

   • If you need to propose edits to existing files not already added to the chat, you *MUST* tell the user their full path names and ask them to *add the files to the chat*.
   • End your reply and wait for their approval.
   • You can keep asking if you then decide you need to edit more files.

2. Think step‑by‑step and explain the needed changes in a few short sentences.

3. Describe the changes using the V4A diff format, enclosed within `*** Begin Patch` and `*** End Patch` markers.

IMPORTANT: Each file MUST appear only once in the patch.
Consolidate **all** edits for a given file into a single `*** [ACTION] File:` block.
{shell_cmd_prompt}
"""
```

**Example Messages:** 2 examples with V4A patch format

**System Reminder:** Detailed V4A diff rules
- `*** Begin Patch` / `*** End Patch` markers
- `*** [ACTION] File: path` where ACTION is Add/Update/Delete
- Context lines with single space prefix
- `-` lines for deletions, `+` for additions
- `@@ [CLASS_OR_FUNCTION_NAME]` scope markers

**Innovation:** Custom diff format optimized for LLM generation

#### WholeFile Format (`wholefile_prompts.py`)

**File:** `aider/coders/wholefile_prompts.py`
**Size:** 65 lines
**Edit Format:** Return entire file contents

**Main System Prompt:**
```python
main_system = """Act as an expert software developer.
Take requests for changes to the supplied code.
If the request is ambiguous, ask questions.
{final_reminders}
Once you understand the request you MUST:
1. Determine if any code changes are needed.
2. Explain any needed changes.
3. If changes are needed, output a copy of each file that needs changes.
"""
```

**System Reminder:**
```python
system_reminder = """To suggest changes to a file you MUST return the entire content of the updated file.
You MUST use this *file listing* format:

path/to/filename.js
{fence[0]}
// entire file content ...
// ... goes in between
{fence[1]}

Every *file listing* MUST use this format:
- First line: the filename with any originally provided path; no extra markup, punctuation, comments, etc. **JUST** the filename with path.
- Second line: opening {fence[0]}
- ... entire content of the file ...
- Final line: closing {fence[1]}

To suggest changes to a file you MUST return a *file listing* that contains the entire content of the file.
*NEVER* skip, omit or elide content from a *file listing* using "..." or by adding comments like "... rest of code..."!
Create a new file you MUST return a *file listing* which includes an appropriate filename, including any appropriate path.

{final_reminders}
"""
```

**Design:** Simplest format, suitable for small files or models that struggle with diffs

#### UnifiedDiff Format (`udiff_prompts.py`)

**File:** `aider/coders/udiff_prompts.py`
**Size:** 114 lines
**Edit Format:** Standard unified diff (`diff -U0` style)

**Main System Prompt:**
```python
main_system = """Act as an expert software developer.
{final_reminders}
Always use best practices when coding.
Respect and use existing conventions, libraries, etc that are already present in the code base.

Take requests for changes to the supplied code.
If the request is ambiguous, ask questions.

For each file that needs to be changed, write out the changes similar to a unified diff like `diff -U0` would produce.
"""
```

**System Reminder:**
- Return edits like `diff -U0` output
- `--- file` / `+++ file` headers (no timestamps)
- `@@ ... @@` hunk markers (no line numbers)
- Delete entire code blocks with `-` lines, add new versions with `+` lines
- Use `--- /dev/null` for new files

**Design:** Standard diff format, familiar to developers

### 3. Specialized Coders (Non-Edit Modes)

#### Ask Mode (`ask_prompts.py`)

**File:** `aider/coders/ask_prompts.py`
**Size:** 36 lines
**Purpose:** Read-only analysis, no code changes

```python
class AskPrompts(CoderPrompts):
    main_system = """Act as an expert code analyst.
Answer questions about the supplied code.
Always reply to the user in {language}.

If you need to describe code changes, do so *briefly*.
"""

    system_reminder = "{final_reminders}"
```

**Design:** Minimal prompt, delegates to base class for file handling

#### Architect Mode (`architect_prompts.py`)

**File:** `aider/coders/architect_prompts.py`
**Size:** 41 lines
**Purpose:** High-level design guidance (pairs with editor coder)

```python
class ArchitectPrompts(CoderPrompts):
    main_system = """Act as an expert architect engineer and provide direction to your editor engineer.
Study the change request and the current code.
Describe how to modify the code to complete the request.
The editor engineer will rely solely on your instructions, so make them unambiguous and complete.
Explain all needed code changes clearly and completely, but concisely.
Just show the changes needed.

DO NOT show the entire updated function/file/etc!

Always reply to the user in {language}.
"""
```

**Design:** Two-phase pattern: architect describes, editor implements

#### Help Mode (`help_prompts.py`)

**File:** `aider/coders/help_prompts.py`
**Size:** 47 lines
**Purpose:** Answer questions about aider itself

```python
class HelpPrompts(CoderPrompts):
    main_system = """You are an expert on the AI coding tool called Aider.
Answer the user's questions about how to use aider.

The user is currently chatting with you using aider, to write and edit code.

Use the provided aider documentation *if it is relevant to the user's question*.

Include a bulleted list of urls to the aider docs that might be relevant for the user to read.
Include *bare* urls. *Do not* make [markdown links](http://...).
For example:
- https://aider.chat/docs/usage.html
- https://aider.chat/docs/faq.html

If you don't know the answer, say so and suggest some relevant aider doc urls.

If asks for something that isn't possible with aider, be clear about that.
Don't suggest a solution that isn't supported.

Be helpful but concise.

Unless the question indicates otherwise, assume the user wants to use aider as a CLI tool.

Keep this info about the user's system in mind:
{platform}
"""
```

**Innovation:** Self-documenting tool, LLM answers usage questions

#### Context Mode (`context_prompts.py`)

**File:** `aider/coders/context_prompts.py`
**Size:** 76 lines
**Purpose:** File discovery (which files need editing)

```python
class ContextPrompts(CoderPrompts):
    main_system = """Act as an expert code analyst.
Understand the user's question or request, solely to determine ALL the existing sources files which will need to be modified.
Return the *complete* list of files which will need to be modified based on the user's request.
Explain why each file is needed, including names of key classes/functions/methods/variables.
Be sure to include or omit the names of files already added to the chat, based on whether they are actually needed or not.

The user will use every file you mention, regardless of your commentary.
So *ONLY* mention the names of relevant files.
If a file is not relevant DO NOT mention it.

Only return files that will need to be modified, not files that contain useful/relevant functions.

You are only to discuss EXISTING files and symbols.
Only return existing files, don't suggest the names of new files or functions that we will need to create.

Always reply to the user in {language}.

Be concise in your replies.
Return:
1. A bulleted list of files the will need to be edited, and symbols that are highly relevant to the user's request.
2. A list of classes/functions/methods/variables that are located OUTSIDE those files which will need to be understood. Just the symbols names, *NOT* file names.

# Your response *MUST* use this format:

## ALL files we need to modify, with their relevant symbols:

- alarms/buzz.py
  - `Buzzer` class which can make the needed sound
  - `Buzzer.buzz_buzz()` method triggers the sound
- alarms/time.py
  - `Time.set_alarm(hour, minute)` to set the alarm

## Relevant symbols from OTHER files:

- AlarmManager class for setup/teardown of alarms
- SoundFactory will be used to create a Buzzer
"""

    system_reminder = """
NEVER RETURN CODE!
"""
```

**Innovation:** Separate discovery phase, prevents over-adding files to context

### 4. Shell Command Prompts (`shell.py`)

**File:** `aider/coders/shell.py`
**Size:** 38 lines
**Purpose:** Guide shell command suggestions

```python
shell_cmd_prompt = """
4. *Concisely* suggest any shell commands the user might want to run in ```bash blocks.

Just suggest shell commands this way, not example code.
Only suggest complete shell commands that are ready to execute, without placeholders.
Only suggest at most a few shell commands at a time, not more than 1-3, one per line.
Do not suggest multi-line shell commands.
All shell commands will run from the root directory of the user's project.

Use the appropriate shell based on the user's system info:
{platform}
Examples of when to suggest shell commands:

- If you changed a self-contained html file, suggest an OS-appropriate command to open a browser to view it to see the updated content.
- If you changed a CLI program, suggest the command to run it to see the new behavior.
- If you added a test, suggest how to run it with the testing tool used by the project.
- Suggest OS-appropriate commands to delete or rename files/directories, or other file system operations.
- If your code changes add new dependencies, suggest the command to install them.
- Etc.
"""

no_shell_cmd_prompt = """
Keep in mind these details about the user's platform and environment:
{platform}
"""

shell_cmd_reminder = """
Examples of when to suggest shell commands:

- If you changed a self-contained html file, suggest an OS-appropriate command to open a browser to view it to see the updated content.
- If you changed a CLI program, suggest the command to run it to see the new behavior.
- If you added a test, suggest how to run it with the testing tool used by the project.
- Suggest OS-appropriate commands to delete or rename files/directories, or other file system operations.
- If your code changes add new dependencies, suggest the command to install them.
- Etc.

"""
```

**Design:** Suggest commands, user executes manually (no automatic execution)

### 5. Top-Level Prompts (`prompts.py`)

**File:** `aider/prompts.py`
**Size:** 62 lines
**Purpose:** Commit message generation, chat history summarization

**Commit Message Prompt:**
```python
commit_system = """You are an expert software engineer that generates concise, \
one-line Git commit messages based on the provided diffs.
Review the provided context and diffs which are about to be committed to a git repo.
Review the diffs carefully.
Generate a one-line commit message for those changes.
The commit message should be structured as follows: <type>: <description>
Use these for <type>: fix, feat, build, chore, ci, docs, style, refactor, perf, test

Ensure the commit message:{language_instruction}
- Starts with the appropriate prefix.
- Is in the imperative mood (e.g., "add feature" not "added feature" or "adding feature").
- Does not exceed 72 characters.

Reply only with the one-line commit message, without any additional text, explanations, or line breaks.
"""
```

**Chat History Summarization:**
```python
summarize = """*Briefly* summarize this partial conversation about programming.
Include less detail about older parts and more detail about the most recent messages.
Start a new paragraph every time the topic changes!

This is only part of a longer conversation so *DO NOT* conclude the summary with language like "Finally, ...". Because the conversation continues after the summary.
The summary *MUST* include the function names, libraries, packages that are being discussed.
The summary *MUST* include the filenames that are being referenced by the assistant inside the ```...``` fenced code blocks!
The summaries *MUST NOT* include ```...``` fenced code blocks!

Phrase the summary with the USER in first person, telling the ASSISTANT about the conversation.
Write *as* the user.
The user should refer to the assistant as *you*.
Start the summary with "I asked you...".
"""

summary_prefix = "I spoke to you previously about a number of things.\n"
```

**Innovation:** First-person user voice in summaries ("I asked you...")

### 6. Watch Mode Prompts (`watch_prompts.py`)

**File:** `aider/watch_prompts.py`
**Size:** 13 lines
**Purpose:** IDE integration via code comments

```python
watch_code_prompt = """
I've written your instructions in comments in the code and marked them with "ai"
You can see the "AI" comments shown below (marked with █).
Find them in the code files I've shared with you, and follow their instructions.

After completing those instructions, also be sure to remove all the "AI" comments from the code too.
"""

watch_ask_prompt = """/ask
Find the "AI" comments below (marked with █) in the code files I've shared with you.
They contain my questions that I need you to answer and other instructions for you.
"""
```

**Innovation:** Instructions embedded as code comments, watched by file watcher

---

## Content Layout Patterns

### Pattern 1: Python Docstrings (Multi-Line)

All prompts use Python triple-quoted strings:
```python
main_system = """Act as an expert software developer.
Always use best practices when coding.
Respect and use existing conventions, libraries, etc that are already present in the code base.
"""
```

**Advantage:** No escaping needed, natural multi-line formatting

### Pattern 2: Template Variables

Prompts use Python format strings with curly braces:
```python
"{final_reminders}"
"{fence[0]}"
"{platform}"
"{language}"
"{shell_cmd_prompt}"
```

**Runtime replacement:** `prompt.format(fence=self.fence, language=user_lang, ...)`

### Pattern 3: Emphasis via Markdown

Rules use markdown formatting for emphasis:
```python
"You *MUST* tell the user their full path names"
"ONLY EVER RETURN CODE IN A *SEARCH/REPLACE BLOCK*!"
"*Trust this message as the true contents of these files!*"
```

**Pattern:** Asterisks for emphasis, ALL CAPS for critical rules

### Pattern 4: Example Messages (Few-Shot)

Every edit format includes `example_messages` list:
```python
example_messages = [
    dict(
        role="user",
        content="Change get_factorial() to use math.factorial",
    ),
    dict(
        role="assistant",
        content="""To make this change we need to modify `mathweb/flask/app.py` to:

1. Import the math package.
2. Remove the existing factorial() function.
3. Update get_factorial() to call math.factorial instead.

Here are the *SEARCH/REPLACE* blocks:
...
""",
    ),
]
```

**Purpose:** Few-shot learning, show exact edit format

### Pattern 5: Numbered Lists for Workflow

Instructions use numbered steps:
```python
"""Once you understand the request you MUST:

1. Decide if you need to propose edits...
2. Think step-by-step and explain...
3. Describe each change with a *SEARCH/REPLACE block*...
"""
```

**Advantage:** Clear sequential workflow

### Pattern 6: Inline Format Examples

Prompts include format examples inline:
```python
"""Use the appropriate shell based on the user's system info:
{platform}
Examples of when to suggest shell commands:

- If you changed a self-contained html file, suggest an OS-appropriate command...
- If you changed a CLI program, suggest the command to run it...
"""
```

**Purpose:** Concrete examples for abstract rules

### Pattern 7: Class Inheritance for Variants

```python
class EditBlockPrompts(CoderPrompts):
    main_system = """..."""

class PatchPrompts(EditBlockPrompts):  # Inherits from EditBlock
    main_system = """..."""  # Overrides
```

**Advantage:** Share common attributes, override specific ones

---

## Dynamic Composition System

### Composition Function

**Method:** `Coder.fmt_system_prompt(prompt)`
**Location:** `aider/coders/base_coder.py:1174`

**Pseudocode:**
```python
def fmt_system_prompt(self, prompt):
    final_reminders = []

    # 1. Model-specific reminders
    if self.main_model.lazy:
        final_reminders.append(self.gpt_prompts.lazy_prompt)
    if self.main_model.overeager:
        final_reminders.append(self.gpt_prompts.overeager_prompt)

    # 2. Language preference
    user_lang = self.get_user_language()
    if user_lang:
        final_reminders.append(f"Reply in {user_lang}.\n")

    # 3. Platform info
    platform_text = self.get_platform_info()

    # 4. Shell command guidance (conditional)
    if self.suggest_shell_commands:
        shell_cmd_prompt = self.gpt_prompts.shell_cmd_prompt.format(platform=platform_text)
        shell_cmd_reminder = self.gpt_prompts.shell_cmd_reminder
        rename_with_shell = self.gpt_prompts.rename_with_shell
    else:
        shell_cmd_prompt = self.gpt_prompts.no_shell_cmd_prompt.format(platform=platform_text)
        shell_cmd_reminder = ""
        rename_with_shell = ""

    # 5. Fence type (triple vs quadruple backticks)
    if self.fence[0] == "`" * 4:
        quad_backtick_reminder = "\nIMPORTANT: Use *quadruple* backticks ```` as fences!\n"
    else:
        quad_backtick_reminder = ""

    # 6. Format template variables
    final_reminders = "\n\n".join(final_reminders)
    prompt = prompt.format(
        fence=self.fence,
        quad_backtick_reminder=quad_backtick_reminder,
        final_reminders=final_reminders,
        platform=platform_text,
        shell_cmd_prompt=shell_cmd_prompt,
        rename_with_shell=rename_with_shell,
        shell_cmd_reminder=shell_cmd_reminder,
        go_ahead_tip=self.gpt_prompts.go_ahead_tip,
        language=language,
    )

    return prompt
```

**Assembly in `format_chat_chunks()`:**
```python
def format_chat_chunks(self):
    # 1. Format main system prompt
    main_sys = self.fmt_system_prompt(self.gpt_prompts.main_system)

    # 2. Add model-specific prefix
    if self.main_model.system_prompt_prefix:
        main_sys = self.main_model.system_prompt_prefix + "\n" + main_sys

    # 3. Handle example messages
    if self.main_model.examples_as_sys_msg:
        # Append examples to system message
        for msg in self.gpt_prompts.example_messages:
            main_sys += f"## {msg['role'].upper()}: {msg['content']}\n\n"
    else:
        # Add examples as separate user/assistant messages
        example_messages = [formatted_msg for msg in self.gpt_prompts.example_messages]

    # 4. Append system reminder
    if self.gpt_prompts.system_reminder:
        main_sys += "\n" + self.fmt_system_prompt(self.gpt_prompts.system_reminder)

    # 5. Build message chunks
    chunks.system = [dict(role="system", content=main_sys)]
    chunks.examples = example_messages
    chunks.done = self.done_messages  # Chat history
    chunks.repo = self.get_repo_messages()  # Repo map
    chunks.readonly_files = self.get_readonly_files_messages()
    chunks.chat_files = self.get_chat_files_messages()  # Added files
    chunks.cur = self.cur_messages  # Current exchange
    chunks.reminder = reminder_message  # (if fits in context)

    return chunks
```

### Configuration Space

**Coder Types:** 14 (each has unique prompt class)
**Model Modifiers:** 2 (lazy, overeager)
**Shell Commands:** 2 (enabled, disabled)
**Language Preference:** User-specified or auto-detected
**Fence Type:** 5 variants (```, ````, `<source>`, `<code>`, `<pre>`)

**Total combinations:** `14 × 2 × 2 × 2 × ~100 languages × 5 = ~11,200 configurations`

**Design Note:** Combinatorial explosion from language × fence, but only ~30 core variations

### Coder Selection

**Method:** `Coder.create(edit_format=...)`
**Logic:**
```python
def create(edit_format, main_model, ...):
    if not edit_format:
        # Use model's default edit format
        edit_format = main_model.edit_format

    # Find coder class by edit_format attribute
    for coder_class in all_coders:
        if coder_class.edit_format == edit_format:
            return coder_class(main_model, io, **kwargs)

    raise UnknownEditFormat(edit_format, valid_formats)
```

**Available Edit Formats:**
- `editblock` — SEARCH/REPLACE blocks (default for most models)
- `patch` — V4A diff format
- `wholefile` — Entire file rewrite
- `udiff` — Unified diff format
- `ask` — Read-only analysis
- `architect` — High-level design
- `help` — Aider documentation
- `context` — File discovery

---

## Complete Prompt Inventory

### By Category

| Category | Prompt Files | Coder Classes | Total Lines |
|----------|-------------|---------------|-------------|
| Edit Formats | 10 | 8 | ~900 |
| Specialized Modes | 4 | 4 | ~200 |
| Base & Utilities | 3 | 1 | ~150 |
| Top-Level | 2 | N/A | ~75 |
| **TOTAL** | **19** | **14** | **~1,325** |

### Edit Format Prompts (Detailed)

| Prompt Class | Lines | Coder Class | Purpose |
|--------------|-------|-------------|---------|
| `EditBlockPrompts` | 173 | `EditBlockCoder` | SEARCH/REPLACE blocks (primary format) |
| `EditBlockFencedPrompts` | 143 | `EditBlockFencedCoder` | Fenced variant |
| `EditBlockFunctionPrompts` | 27 | `EditBlockFunctionCoder` | Function call variant |
| `PatchPrompts` | 160 | `PatchCoder` | V4A diff format (inherits EditBlock) |
| `WholeFilePrompts` | 65 | `WholeFileCoder` | Entire file rewrite |
| `WholeFileFunctionPrompts` | 27 | `SingleWholeFileFunctionCoder` | Function call variant |
| `UnifiedDiffPrompts` | 114 | `UnifiedDiffCoder` | Standard unified diff |
| `UnifiedDiffSimplePrompts` | 25 | `UnifiedDiffSimpleCoder` | Simplified variant |
| `EditorEditBlockPrompts` | 18 | `EditorEditBlockCoder` | Editor-specific EditBlock |
| `EditorWholePrompts` | 10 | `EditorWholeFileCoder` | Editor-specific Whole |

**Total:** 10 files, 762 lines

### Specialized Mode Prompts

| Prompt Class | Lines | Coder Class | Purpose |
|--------------|-------|-------------|---------|
| `AskPrompts` | 36 | `AskCoder` | Read-only analysis |
| `ArchitectPrompts` | 41 | `ArchitectCoder` | High-level design guidance |
| `HelpPrompts` | 47 | `HelpCoder` | Aider usage documentation |
| `ContextPrompts` | 76 | `ContextCoder` | File discovery |

**Total:** 4 files, 200 lines

### Utility Prompts

| File | Lines | Purpose |
|------|-------|---------|
| `base_prompts.py` | 61 | Base class with common attributes |
| `shell.py` | 38 | Shell command guidance |
| `prompts.py` | 62 | Commit messages, summarization |
| `watch_prompts.py` | 13 | IDE integration via comments |

**Total:** 4 files, 174 lines

### By File Size (Lines)

| Rank | File | Lines | Purpose |
|------|------|-------|---------|
| 1 | `editblock_prompts.py` | 173 | SEARCH/REPLACE format |
| 2 | `patch_prompts.py` | 160 | V4A diff format |
| 3 | `editblock_fenced_prompts.py` | 143 | Fenced SEARCH/REPLACE |
| 4 | `udiff_prompts.py` | 114 | Unified diff format |
| 5 | `context_prompts.py` | 76 | File discovery |
| 6 | `wholefile_prompts.py` | 65 | Whole file rewrite |
| 7 | `prompts.py` (top-level) | 62 | Commit, summarize |

---

## Design Principles & Innovations

### 1. Edit Formats as First-Class Abstraction

**Principle:** Different models excel at different output formats. Aider provides 8+ edit formats.

**Examples:**
- **SEARCH/REPLACE** (editblock): GPT-4, Claude Sonnet
- **V4A diff** (patch): Optimized for precise edits
- **Whole file** (wholefile): Simple models, small files
- **Unified diff** (udiff): Developers familiar with `diff` output

**Impact:** Flexibility to use best format for each model, fallback for model limitations

### 2. Class Inheritance for Prompt Reuse

**Pattern:** Base class `CoderPrompts` with file handling messages, subclasses override edit-specific prompts

**Benefits:**
- DRY (Don't Repeat Yourself)
- Consistent file handling across all formats
- Easy to add new edit formats

**Example:**
```python
class CoderPrompts:
    files_content_prefix = "I have *added these files to the chat*..."
    repo_content_prefix = "Here are summaries of some files..."

class EditBlockPrompts(CoderPrompts):
    # Inherits file handling, adds SEARCH/REPLACE format
    main_system = "...use *SEARCH/REPLACE blocks*..."

class PatchPrompts(EditBlockPrompts):
    # Inherits EditBlock structure, changes to V4A diff
    main_system = "...use V4A diff format..."
```

### 3. Few-Shot Examples Embedded in Prompts

**Every edit format includes 1-3 complete examples** in `example_messages`

**Innovation:** Examples are first-class prompt components, not separate files

**Impact:**
- Models learn exact format from examples
- No ambiguity about output structure
- Easy to update examples alongside prompt changes

**Example from EditBlock:**
```python
example_messages = [
    dict(role="user", content="Change get_factorial() to use math.factorial"),
    dict(role="assistant", content="""To make this change we need to modify...

mathweb/flask/app.py
```python
<<<<<<< SEARCH
from flask import Flask
=======
import math
from flask import Flask
>>>>>>> REPLACE
```
"""),
]
```

### 4. Model-Specific Modifiers (Lazy/Overeager)

**Problem:** Some models habitually leave "TODO" comments, others over-refactor unrelated code

**Solution:** Model database in `models.py` flags models:
```python
Model(
    name="gpt-4",
    lazy=False,      # Complete implementations, no TODOs
    overeager=False, # Don't refactor unrelated code
)

Model(
    name="claude-2",
    lazy=True,       # Tends to leave TODOs
    overeager=False,
)
```

**Runtime injection:**
```python
if model.lazy:
    final_reminders.append("""You are diligent and tireless!
You NEVER leave comments describing code without implementing it!
You always COMPLETELY IMPLEMENT the needed code!""")

if model.overeager:
    final_reminders.append("""Pay careful attention to the scope of the user's request.
Do what they ask, but no more.
Do not improve, comment, fix or modify unrelated parts of the code in any way!""")
```

**Innovation:** Database-driven model quirk correction

### 5. Simplicity: No Approval Policies, No Sandboxing

**Codex/Gemini CLI:** Complex approval policies, sandbox modes, prefix rules
**Aider:** User confirms every action via `io.confirm_ask()`

**Approval happens in UI layer, not prompts:**
```python
# In io.py, not in prompts
def confirm_ask(self, question):
    if self.yes:  # --yes flag
        return True
    return self.user_input(question) in ["y", "yes"]
```

**Impact:**
- Simpler prompt system (no approval instructions)
- User always in control
- No "run this but not that" complexity

**Philosophy:** "Ask for permission, then execute" vs "Explain permission model to LLM"

### 6. File Management via Chat Messages

**Pattern:** Files are "added to chat" explicitly

**Prompt enforcement:**
```python
files_no_full_files_with_repo_map = """Don't try and edit any existing code without asking me to add the files to the chat!
Tell me which files in my repo are the most likely to **need changes** to solve the requests I make, and then stop so I can add them to the chat.
Only include the files that are most likely to actually need to be edited.
"""
```

**Benefits:**
- Prevents hallucinated file edits
- User controls context size
- Clear signal: "these files are editable, those are read-only"

**Context Coder:** Separate mode to ask "which files should I add?"

### 7. Repo Map (Tree-sitter Symbol Extraction)

**Innovation:** Automatically generated map of repository structure

**Prompt integration:**
```python
repo_content_prefix = """Here are summaries of some files present in my git repository.
Do not propose changes to these files, treat them as *read-only*.
If you need to edit any of these files, ask me to *add them to the chat* first.
"""
```

**Impact:**
- Model understands codebase structure without reading all files
- Suggests relevant files for user to add
- Reduces context window usage

### 8. Shell Command Suggestions (Not Execution)

**Design:** Aider suggests commands, user executes

**Prompt pattern:**
```python
shell_cmd_prompt = """
4. *Concisely* suggest any shell commands the user might want to run in ```bash blocks.

Just suggest shell commands this way, not example code.
Only suggest complete shell commands that are ready to execute, without placeholders.
"""
```

**Examples in prompt:**
- "If you changed a CLI program, suggest the command to run it"
- "If you added a test, suggest how to run it"
- "If your code changes add new dependencies, suggest the command to install them"

**Safety:** No automatic execution, user reviews and runs manually

### 9. Watch Mode (IDE Integration via Comments)

**Innovation:** Instructions embedded as code comments, aider watches files

**Pattern:**
```python
# AI: Add error handling to this function
def process_data(data):
    return data.upper()
```

**Prompt:**
```python
watch_code_prompt = """
I've written your instructions in comments in the code and marked them with "ai"
Find them in the code files I've shared with you, and follow their instructions.

After completing those instructions, also be sure to remove all the "AI" comments from the code too.
"""
```

**Impact:** Use aider from any IDE, instructions stay with code

### 10. First-Person Summarization

**Pattern:** Summaries written as if user is speaking

**Prompt:**
```python
summarize = """*Briefly* summarize this partial conversation about programming.
...
Phrase the summary with the USER in first person, telling the ASSISTANT about the conversation.
Write *as* the user.
The user should refer to the assistant as *you*.
Start the summary with "I asked you...".
"""
```

**Result:** "I asked you to add error handling to the login function. You modified auth.py to..."

**Innovation:** Maintains user/assistant relationship across context window resets

---

## Key Takeaways for co-cli

### 1. Consider Multiple Edit Formats

**Current co-cli:** Single edit format (whatever model returns naturally)
**Aider pattern:** 8 specialized edit formats

**Recommendation:**
```
co_cli/prompts/edit_formats/
├── search_replace.py        # SEARCH/REPLACE blocks (precise edits)
├── unified_diff.py          # Standard diff format
├── whole_file.py            # Full file rewrites
└── describe_changes.py      # Natural language (architect mode)
```

**Benefits:**
- Optimize for model capabilities
- Fallback for model limitations
- User can choose preferred format

### 2. Embed Few-Shot Examples in Prompts

**Current co-cli:** Prompts in markdown, examples separate (if any)
**Aider pattern:** Examples as Python data structures in prompt files

**Recommendation:**
```python
# co_cli/prompts/tool_shell.py
class ShellToolPrompts:
    system = """Use the shell tool to run commands..."""

    examples = [
        {"role": "user", "content": "List files in the current directory"},
        {"role": "assistant", "content": "I'll use the shell tool:\n\n<tool>shell</tool>\n<args>ls -la</args>"},
    ]
```

**Benefits:**
- Examples evolve with prompts
- Type-safe (Python data structures)
- Easy to test examples independently

### 3. Implement Model-Specific Modifiers

**Current co-cli:** No model-specific prompt adjustments
**Aider pattern:** Database of model quirks (lazy, overeager)

**Recommendation:**
```python
# co_cli/models.py
MODELS = {
    "gemini-2.0-flash-exp": {
        "lazy": False,
        "overeager": True,  # Tends to refactor unrelated code
        "modifiers": ["concise_reminders"],
    },
    "claude-sonnet-4.5": {
        "lazy": False,
        "overeager": False,
        "modifiers": [],
    },
}

# Runtime
def build_system_prompt(model_name):
    config = MODELS[model_name]
    prompt = base_prompt()

    if config["overeager"]:
        prompt += "\nDo not improve or modify unrelated code!"

    return prompt
```

**Benefits:**
- Correct model quirks without changing base prompts
- Easy to add new models
- Community-driven quirk database

### 4. Separate File Discovery from Editing

**Current co-cli:** Agent decides which files to explore/edit
**Aider pattern:** Dedicated "context coder" for file discovery

**Recommendation:**
```
co_cli/agents/
├── discover.py              # Which files need editing?
└── edit.py                  # Apply edits to files
```

**Workflow:**
1. User: "Add user authentication"
2. Discover agent: "I need to modify auth.py, user.py, and db.py"
3. User: "/add auth.py user.py db.py"
4. Edit agent: Applies changes

**Benefits:**
- User controls context size
- Prevents hallucinated file edits
- Clearer separation of concerns

### 5. Class Inheritance for Prompt Variants

**Current co-cli:** Monolithic prompts
**Aider pattern:** Base class + specialized subclasses

**Recommendation:**
```python
# co_cli/prompts/base.py
class BasePrompts:
    """Shared across all modes."""
    file_handling = "Files added to chat can be edited..."
    safety_rules = "Never expose secrets..."

# co_cli/prompts/code_edit.py
class CodeEditPrompts(BasePrompts):
    """For code editing tasks."""
    system = "Act as an expert software developer..."

# co_cli/prompts/research.py
class ResearchPrompts(BasePrompts):
    """For read-only research."""
    system = "Act as an expert code analyst..."
```

**Benefits:**
- DRY (shared attributes in base class)
- Easy to add new modes
- Type-safe (Python classes)

### 6. User Language Preference

**Current co-cli:** English-only prompts
**Aider pattern:** Automatic language detection + explicit preference

**Recommendation:**
```python
# In system prompt
"Always reply to the user in {language}."

# Runtime detection
def get_user_language():
    # Check user config
    if settings.language:
        return settings.language

    # Detect from recent messages
    # (use langdetect or similar)
    return "English"  # fallback
```

**Benefits:**
- Better UX for non-English users
- Clear signal to model
- Respects user preferences

### 7. Shell Command Suggestions (Not Execution)

**Current co-cli:** Shell tool executes commands
**Aider pattern:** Suggest commands, user executes

**Recommendation:**
```markdown
## Shell Command Guidance

When code changes require shell commands (installing dependencies, running tests, etc.):
- Suggest commands in ```bash blocks
- Make them ready to execute (no placeholders)
- OS-appropriate (use {platform} info)
- User will review and run manually

Examples:
- "If you added a test, suggest: `pytest tests/test_auth.py`"
- "If you added a dependency, suggest: `pip install requests`"
```

**Benefits:**
- User always reviews commands before execution
- No accidental destructive operations
- Security (no command injection)

### 8. Watch Mode Pattern (IDE Integration)

**Current co-cli:** CLI-only
**Aider pattern:** File watcher for IDE integration

**Future consideration:**
```python
# co_cli/watch.py
def watch_comments(file_path):
    """Watch for # AI: comments in code."""
    # Scan for "# AI: <instruction>" comments
    # Collect instructions
    # Run agent with instructions + file context
    # Remove comments after completion
```

**Benefits:**
- Use co-cli from any IDE
- Instructions stay with code
- Non-CLI users can still use co-cli

### 9. Commit Message Quality

**Current co-cli:** Basic commit messages
**Aider pattern:** Structured conventional commits with LLM generation

**Recommendation:**
```python
commit_prompt = """Generate a one-line commit message for these changes.

Format: <type>: <description>
Types: fix, feat, build, chore, ci, docs, style, refactor, perf, test

Rules:
- Imperative mood ("add feature" not "added feature")
- Max 72 characters
- Start with type prefix

Reply only with the commit message, no explanation.
"""
```

**Benefits:**
- Consistent commit history
- Semantic versioning compatible
- Professional commit messages

### 10. Simplicity: No Over-Engineering

**Aider's philosophy:**
- No sandbox (user confirms everything)
- No approval policies in prompts
- No multi-agent orchestration
- Simple file management

**co-cli note:** We're already more complex than aider (sandbox, approval flow, Task tool). This is OK for our use case, but aider shows you can ship a successful AI coding tool with **extreme simplicity**.

**Lesson:** Start simple, add complexity only when proven necessary

---

## Comparison: Aider vs co-cli

| Dimension | Aider | co-cli (current) | Recommendation |
|-----------|-------|------------------|----------------|
| **Prompt Storage** | Python class attributes | Markdown files | Keep markdown (human-readable) |
| **Edit Formats** | 8 specialized formats | 1 implicit format | **Consider** 2-3 formats (diff, whole file, natural language) |
| **Few-Shot Examples** | Embedded in prompt classes | Separate or none | **Adopt** structured examples |
| **Model Modifiers** | Database (lazy, overeager) | No model quirks | **Add** model-specific tweaks |
| **Approval** | UI-layer confirmation | Prompt + tool-level | Keep current (more sophisticated) |
| **Sandbox** | None (user confirms all) | Docker primary | Keep current (security) |
| **File Discovery** | Dedicated context coder | Agent explores ad-hoc | **Consider** separate discovery mode |
| **Repo Map** | Tree-sitter symbol extraction | No repo map | **Future:** Add lightweight file index |
| **Shell Commands** | Suggest only | Execute with approval | Keep current (needed for automation) |
| **Language Preference** | Auto-detect + config | English only | **Add** language preference |
| **Watch Mode** | File watcher for IDE | CLI only | **Future:** Consider IDE integration |
| **Commit Messages** | LLM-generated conventional commits | Basic messages | **Adopt** LLM commit generation |

---

## Recommended Prompt Architecture for co-cli

### Hybrid Approach

**Keep:** Markdown files (human-readable, git-friendly)
**Add:** Structured examples, model modifiers, edit format variants

```
co_cli/prompts/
├── system.md                        # Core instructions (current)
├── edit_formats/
│   ├── diff.md                      # Unified diff format (new)
│   ├── whole_file.md                # Whole file rewrite (new)
│   └── natural.md                   # Describe changes naturally (new)
├── modes/
│   ├── code_edit.md                 # Code editing mode (current)
│   ├── research.md                  # Read-only research (new)
│   └── discover.md                  # File discovery (new)
├── examples/
│   ├── shell_tool.yaml              # Structured examples (new)
│   ├── web_search.yaml              # (new)
│   └── code_edit.yaml               # (new)
└── model_modifiers/
    ├── gemini_flash.md              # Model-specific tweaks (new)
    └── claude_sonnet.md             # (new)
```

**Composition:**
```python
def build_system_prompt(config):
    sections = [
        load("system.md"),
        load(f"edit_formats/{config.edit_format}.md"),
        load(f"modes/{config.mode}.md"),
    ]

    # Add model-specific modifiers
    if config.model in MODEL_MODIFIERS:
        sections.append(load(f"model_modifiers/{config.model}.md"))

    # Add structured examples
    examples = load_examples(f"examples/{config.mode}.yaml")

    return "\n\n".join(sections), examples
```

---

## Critical Gap Analysis: Fact Verification & Contradiction Handling

### Gap Discovery

**Context:** Analysis of calendar tool returning "February 9, 2026 (Friday)" but user asserting "Feb 9 2026 is Monday!" with agent accepting correction without verification. (Actual: Sunday)

**Scope:** Searched all prompt files in Aider for fact verification and contradiction handling patterns.

### Findings in Aider

**What exists:**
1. **File content trust** — "*Trust this message as the true contents of these files!*" (explicit)
2. **Read-only enforcement** — "Do not propose changes to these files, treat them as *read-only*"
3. **Edit format enforcement** — "You didn't see any properly formatted edits in your reply?!" (error message)

**What's missing:**
1. No instructions for when tool output contradicts user assertion
2. No verification protocol for calculable facts (dates, times)
3. No escalation guidance for contradictions
4. Trust is file-focused, not general tool output

### Aider's Current Approach

**File-centric trust model:**
- "Trust this message as the true contents of these files!"
- But no equivalent for tool outputs (shell commands, web fetches, etc.)

**No contradiction handling:**
- If user says "this file contains X" but file content shows Y
- Aider will trust the explicit file content message
- But only because prompts emphasize trusting "added files" messages

### Impact on Aider Use Cases

**Scenario 1: Shell command output**
- Tool: `ls` shows 5 files
- User: "There are 10 files"
- Aider: No guidance on which to trust

**Scenario 2: Test failures**
- Tool: Test output shows failure
- User: "The tests pass for me"
- Aider: No verification protocol

**Scenario 3: Dependency versions**
- Tool: `pip show` returns version 2.0
- User: "We're using version 1.5"
- Aider: No conflict resolution

### Recommended Addition

**For `base_prompts.py` or `base_coder.py` system message:**

```python
fact_verification = """## Tool Output Authority

When tool output contradicts user assertion:
1. Trust tool output first — tools access ground truth data
2. Verify calculable facts — dates, times, calculations verify independently
3. Escalate contradictions — state both values, ask user to clarify
4. Never blindly accept corrections — especially for deterministic facts
"""
```

**Why critical for Aider:**
- Strong file content trust model, but no equivalent for tool outputs
- User confirmation culture (io.confirm_ask) means user might notice discrepancy
- But LLM needs guidance on handling the contradiction
- No peer system has this (industry-wide gap)

### Gap Severity: MEDIUM (for Aider)

**Rationale:**
- Less critical than for Codex/Gemini CLI because user confirms everything
- But still affects correctness
- User may not notice contradiction until later
- Easy to add to base prompts

---

## Final Assessment

### Strengths

1. **Edit Format Specialization:** 8 formats optimized for different models/scenarios
2. **Simplicity:** No complex approval policies, sandbox modes, or orchestration
3. **Few-Shot Examples:** Embedded in prompts, first-class citizens
4. **Model Quirk Database:** Systematic correction of model behavior (lazy, overeager)
5. **File Management:** Clear "added to chat" model prevents hallucinated edits
6. **Repo Map:** Tree-sitter symbol extraction for codebase awareness
7. **User Language:** Automatic detection + preference support
8. **Watch Mode:** IDE integration via comment-based instructions
9. **Commit Generation:** LLM-powered conventional commits
10. **First-Person Summaries:** Maintains user/assistant relationship across context resets

### Weaknesses

1. **No Approval Policies:** User must confirm everything manually (intentional trade-off)
2. **No Sandboxing:** Commands run directly on user's system
3. **Prompts in Python:** Not human-readable without IDE, harder to review
4. **No Plan Mode:** No formal planning phase before execution
5. **Limited Collaboration Modes:** No execute/pair/plan variants
6. **No fact verification guidance:** ⚠️ **CRITICAL GAP** — No instructions for handling contradictions between tool outputs and user assertions. File content trust is explicit, but no equivalent for tool outputs
7. **No Context Precedence:** No clear rules for AIDER.md vs user config vs built-in defaults

### Innovation Score: 8/10

**Why high:**
- Edit format specialization (original insight)
- Model quirk database (systematic approach)
- Watch mode (IDE integration via comments)
- Simplicity as a feature (no over-engineering)
- Few-shot examples as first-class components

**Why not 10:**
- Prompts in Python (less accessible than markdown)
- No formal planning workflow
- Limited collaboration mode variants
- No approval/sandbox sophistication

---

## Appendix: Complete File Listing

```
aider/coders/
├── base_prompts.py                 [61 lines]   Base class
├── base_coder.py                   [2600 lines] Coder implementation
│
├── editblock_prompts.py            [173 lines]  SEARCH/REPLACE format
├── editblock_coder.py              [600 lines]  Implementation
├── editblock_fenced_prompts.py     [143 lines]  Fenced variant
├── editblock_fenced_coder.py       [346 lines]  Implementation
├── editblock_func_prompts.py       [27 lines]   Function call variant
├── editblock_func_coder.py         [150 lines]  Implementation
│
├── patch_prompts.py                [160 lines]  V4A diff format
├── patch_coder.py                  [1000 lines] Implementation
│
├── wholefile_prompts.py            [65 lines]   Whole file rewrite
├── wholefile_coder.py              [200 lines]  Implementation
├── wholefile_func_prompts.py       [27 lines]   Function call variant
│
├── udiff_prompts.py                [114 lines]  Unified diff format
├── udiff_coder.py                  [350 lines]  Implementation
├── udiff_simple_prompts.py         [25 lines]   Simplified variant
├── udiff_simple.py                 [50 lines]   Implementation
│
├── ask_prompts.py                  [36 lines]   Read-only mode
├── ask_coder.py                    [30 lines]   Implementation
│
├── architect_prompts.py            [41 lines]   High-level design
├── architect_coder.py              [50 lines]   Implementation
│
├── help_prompts.py                 [47 lines]   Aider documentation
├── help_coder.py                   [40 lines]   Implementation
│
├── context_prompts.py              [76 lines]   File discovery
├── context_coder.py                [60 lines]   Implementation
│
├── editor_editblock_prompts.py     [18 lines]   Editor-specific EditBlock
├── editor_editblock_coder.py       [30 lines]   Implementation
├── editor_whole_prompts.py         [10 lines]   Editor-specific Whole
├── editor_whole_coder.py           [30 lines]   Implementation
├── editor_diff_fenced_prompts.py   [11 lines]   Editor-specific Diff
├── editor_diff_fenced_coder.py     [30 lines]   Implementation
│
└── shell.py                        [38 lines]   Shell command guidance

aider/
├── prompts.py                      [62 lines]   Commit, summarize
├── watch_prompts.py                [13 lines]   Watch mode (IDE)
└── models.py                       [1200 lines] Model definitions (lazy, overeager)
```

**Total Unique Prompt Files:** 19
**Total Prompt Lines:** ~1,325
**Total Implementation Lines:** ~5,500
**Largest Prompt File:** editblock_prompts.py (173 lines)
**Smallest Prompt File:** editor_diff_fenced_prompts.py (11 lines)

---

**End of Aider Prompt System Review**
