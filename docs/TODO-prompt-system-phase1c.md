# Prompt System Refactor - Phase 1c Implementation Guide

## Executive Summary

**Goal:** Enable internal knowledge loading and memory tool integration for persistent context and learned facts.

**Problem:** Co currently has no persistent memory across sessions. It cannot remember user preferences, project insights, or learned facts. Each session starts fresh with no context about past interactions or accumulated knowledge.

**Solution:** Add internal knowledge system with markdown-based storage using the lakehouse pattern. Always-loaded context at `.co-cli/knowledge/context.md` and on-demand memory tools (`save_memory`, `recall_memory`, `list_memories`) for explicit knowledge management. Markdown files serve as source of truth with optional SQLite index for future retrieval scaling.

**Scope:** Phase 1c focuses ONLY on internal knowledge infrastructure and memory tools using markdown format. Advanced features (automatic summarization, vector search) are future phases.

**Effort:** 8-10 hours (format design + loading + memory tools + testing + verification)

**Risk:** Low-Medium (new feature, no existing behavior to break, comprehensive validation)

**Design Rationale:** Aligns with 4/4 peer systems (Claude Code, Codex, Gemini CLI, Aider) which universally use Markdown for knowledge storage. Markdown provides LLM-native format, human editability, git-friendly diffs, and clear retrieval evolution path.

---

## Table of Contents

1. [Context & Rationale](#context--rationale)
2. [Architecture Overview](#architecture-overview)
3. [Implementation Plan](#implementation-plan)
4. [Code Specifications](#code-specifications)
5. [Test Specifications](#test-specifications)
6. [Verification Procedures](#verification-procedures)
7. [Documentation Updates](#documentation-updates)
8. [Success Criteria](#success-criteria)
9. [Risk Assessment](#risk-assessment)
10. [Future Enhancements](#future-enhancements)

---

## Context & Rationale

### Why This Change

Co currently operates as a stateless agent - each chat session starts fresh with no memory of previous interactions, learned facts, or user preferences. This creates several problems:

1. **Repeated Instructions:** User must re-state preferences every session
2. **Lost Context:** Insights from previous sessions are forgotten
3. **No Learning:** Agent cannot accumulate knowledge about user's codebase
4. **Inefficiency:** Redundant explanations of project conventions

### User Impact

**Before Phase 1c:**
```
Session 1:
User: "I prefer async/await over callbacks"
Agent: "Understood, I'll use async/await"
[generates async code]

Session 2 (next day):
User: "Write concurrent code"
Agent: [generates callback-based code]  ❌
User: "No, I prefer async/await" [repeated instruction]
```

**After Phase 1c:**
```
Session 1:
User: "I prefer async/await over callbacks"
Agent: "I'll remember that" [calls save_memory tool]

Session 2 (next day):
User: "Write concurrent code"
Agent: [recalls preference, generates async code] ✅
```

### Peer System Alignment

This design aligns with 4/4 peer systems analyzed:

| System | Format | Storage | Memory Access |
|--------|--------|---------|---------------|
| **Claude Code** | Markdown (`CLAUDE.md`) | Files | Agent memory frontmatter |
| **Codex** | Markdown (`AGENTS.md`) | SQLite + files | `get_memory` tool |
| **Gemini CLI** | Markdown (`GEMINI.md`) | Files | `save_memory` appends |
| **Aider** | YAML + Markdown | Files | Chat history restore |

**Convergence:** 4/4 use Markdown for knowledge, 0/4 use JSON as primary format.

**Key Insight:** Markdown is the LLM-native format. All peer systems prioritize human editability and git workflow over programmatic schema enforcement.

See `docs/TODO-prompt-system-phase1c-kb-design-research.md` for detailed peer research and `docs/ANALYSIS-phase1c-design-contradiction.md` for format comparison.

### Design Principles

1. **Markdown as Source of Truth (Lakehouse Pattern)**
   - Files on disk are canonical, human-readable, git-friendly
   - Optional SQLite index is derived and rebuildable
   - Aligns with Basic Memory, Obsidian, Cursor patterns

2. **Explicit > Implicit**
   - No silent learning from user behavior
   - Memory tools require explicit calls
   - Manual curation of always-loaded context

3. **Hierarchy with Precedence**
   - Global context: `~/.config/co-cli/knowledge/context.md`
   - Project context: `.co-cli/knowledge/context.md` (overrides global)
   - On-demand memories: `.co-cli/knowledge/memories/*.md`

4. **Budget Management**
   - Always-loaded context: 10 KiB soft / 20 KiB hard limit
   - On-demand memories: No limit (managed by agent context window)

5. **Retrieval Evolution**
   - Phase 1c: grep + frontmatter scan (<200 memories)
   - Phase 2: SQLite FTS5 (200-500 docs)
   - Phase 3: Hybrid FTS5 + vectors (500+ docs)

---

## Architecture Overview

### File Structure

```
~/.config/co-cli/
└── knowledge/
    └── context.md                      # Global always-loaded context (3 KiB budget)

.co-cli/
├── settings.json                       # Config (Phase 1a, unchanged)
├── instructions.md                     # Project conventions (Phase 1a, unchanged)
└── knowledge/                          # Phase 1c - NEW
    ├── context.md                      # Project always-loaded context (7 KiB budget)
    └── memories/                       # Explicit memories (on-demand)
        ├── 001-prefers-async.md
        ├── 002-project-sqlalchemy.md
        └── 003-test-policy.md
```

**Future (Phase 2+):**
```
.co-cli/knowledge/
├── context.md
├── memories/*.md
├── articles/                           # Web-fetched knowledge
│   └── python-asyncio-patterns.md
└── knowledge.db                        # Derived SQLite index (FTS5 + vectors)
```

### Knowledge Format

#### context.md (Always-Loaded)

```markdown
---
version: 1
updated: 2026-02-09T14:30:00Z
---

# User

- Name: Bin
- Timezone: America/Los_Angeles
- Prefers: concise explanations with reasoning shown

# Project

- Type: Python CLI (typer + pydantic-ai)
- Architecture: Agent with RunContext[CoDeps] tools
- Test policy: functional only, no mocks

# Learned

- User prefers async/await over callbacks
- This project uses SQLAlchemy ORM exclusively
- Always run `uv sync` before `pytest`
```

**Frontmatter Fields:**
- `version`: Schema version (currently `1`)
- `updated`: ISO8601 timestamp of last edit

**Body Structure:**
- Freeform markdown sections
- Common sections: User, Project, Learned
- No rigid schema - users can add custom sections

#### Memory Files (On-Demand)

```markdown
---
id: 1
created: 2026-02-09T14:30:00Z
tags: [python, style]
source: user-told
---

User prefers async/await over callbacks. When generating Python code
that involves concurrent operations, always use asyncio patterns
rather than callback-based approaches.
```

**Frontmatter Fields:**
- `id`: Numeric ID (auto-incremented)
- `created`: ISO8601 timestamp
- `tags`: List of tags for filtering (optional)
- `source`: Origin of memory (`user-told`, `agent-inferred`, `web-fetched`)

**Filename Convention:**
- Format: `{id:03d}-{slug}.md`
- Example: `001-prefers-async.md`
- Slug: First 50 chars of content, slugified

### Data Flow

```
┌──────────────────────────────────────────────────────────────┐
│ Session Start                                                 │
└───────────────────────┬──────────────────────────────────────┘
                        │
                        ▼
            ┌───────────────────────┐
            │ load_internal_knowledge() │
            └───────────┬───────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Global       │ │ Project      │ │ Validate     │
│ context.md   │ │ context.md   │ │ frontmatter  │
│ (optional)   │ │ (optional)   │ │ + size       │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       └────────────────┼────────────────┘
                        │
                        ▼
              ┌──────────────────┐
              │ Markdown body    │
              │ (strip frontmatter) │
              └─────────┬────────┘
                        │
                        ▼
            ┌───────────────────────┐
            │ Inject into system prompt │
            └───────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│ During Chat                                                   │
└───────────────────────┬──────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ save_memory  │ │ recall_memory│ │ list_memories│
│ tool         │ │ tool         │ │ tool         │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       │                │                │
       ▼                ▼                │
┌──────────────┐ ┌──────────────┐       │
│ Write        │ │ grep +       │       │
│ NNN-slug.md  │ │ frontmatter  │       │
│ with YAML    │ │ scan         │       │
└──────────────┘ └──────┬───────┘       │
                        │                │
                        ▼                ▼
              ┌──────────────────┐ ┌──────────────┐
              │ Return markdown  │ │ List files   │
              │ for injection    │ │ with metadata│
              └──────────────────┘ └──────────────┘
```

### Prompt Assembly

```
System prompt layers (in order):
  1. system.md (base)
  2. Personality template ({name}.md)
  3. Global knowledge (~/.config/co-cli/knowledge/context.md)      ← Phase 1c NEW
  4. Project knowledge (.co-cli/knowledge/context.md)              ← Phase 1c NEW
  5. Project instructions (.co-cli/instructions.md)
  6. [system_reminder at end — recency bias]                       ← Phase 1d
```

**Knowledge Injection Format:**
```markdown
<system-reminder>
## Internal Knowledge

### Global Context
[body of ~/.config/co-cli/knowledge/context.md]

### Project Context
[body of .co-cli/knowledge/context.md]
</system-reminder>
```

**Precedence Rules:**
- Project context.md overrides global context.md on conflicts
- Later sections (project instructions) override earlier (global knowledge)
- Current turn user message has highest precedence

### Context Budget

| Layer | Budget | Enforcement |
|-------|--------|-------------|
| Global context.md | 3 KiB soft | Warn if exceeded |
| Project context.md | 7 KiB soft | Warn if exceeded |
| **Total always-loaded** | **10 KiB soft / 20 KiB hard** | Error if >20 KiB |
| On-demand memories | No limit | Agent context window manages |

**Validation at Load Time:**
- Under 10 KiB: Load silently
- 10-20 KiB: Load with warning to stderr
- Over 20 KiB: Truncate to 20 KiB + error message to stderr

---

## Implementation Plan

### Phase 1: Format Design & Schema (2 hours)

**Goal:** Define markdown format, frontmatter schema, file naming conventions.

**Tasks:**

1. **Document context.md format**
   - YAML frontmatter: `version`, `updated`
   - Freeform markdown body with common sections
   - Example templates for user/project/learned

2. **Document memory file format**
   - YAML frontmatter: `id`, `created`, `tags`, `source`
   - Markdown body with memory content
   - Filename convention: `{id:03d}-{slug}.md`

3. **Define validation rules**
   - Required frontmatter fields per format
   - Size limits (10 KiB / 20 KiB)
   - Valid tag characters
   - ISO8601 timestamp format

4. **Create example files**
   - `examples/knowledge/context.md` - Template
   - `examples/knowledge/memories/001-example.md` - Template

**Deliverables:**
- [ ] Format specification documented in this file
- [ ] Example templates created
- [ ] Validation rules defined

### Phase 2: Frontmatter Parsing Utilities (1.5 hours)

**Goal:** Implement YAML frontmatter parsing and validation helpers.

**Tasks:**

1. **Create `co_cli/_frontmatter.py` module**
   ```python
   def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]
   def strip_frontmatter(content: str) -> str
   def validate_context_frontmatter(fm: dict) -> None
   def validate_memory_frontmatter(fm: dict) -> None
   ```

2. **Implement frontmatter parsing**
   - Split on `---` delimiters
   - Parse YAML in header
   - Return (metadata_dict, body_markdown)
   - Handle missing frontmatter gracefully

3. **Implement validation functions**
   - Check required fields exist
   - Validate field types
   - Validate timestamp format (ISO8601)
   - Validate version compatibility

4. **Error handling**
   - Graceful degradation on parse errors
   - Log warnings for malformed files
   - Skip bad files, continue processing

**Deliverables:**
- [ ] `_frontmatter.py` module with 4 functions
- [ ] Unit tests for parsing and validation
- [ ] Error handling for malformed YAML

### Phase 3: Knowledge Loading Function (1.5 hours)

**Goal:** Implement `load_internal_knowledge()` to read markdown files.

**Tasks:**

1. **Create `co_cli/knowledge.py` module**
   ```python
   SIZE_TARGET = 10 * 1024  # 10 KiB soft limit
   SIZE_LIMIT = 20 * 1024   # 20 KiB hard limit

   def load_internal_knowledge() -> str | None
   ```

2. **Implement loading logic**
   - Read global context: `~/.config/co-cli/knowledge/context.md`
   - Read project context: `.co-cli/knowledge/context.md`
   - Parse frontmatter, validate fields
   - Strip frontmatter, return body markdown
   - Combine with section headers

3. **Implement size validation**
   - Calculate size of combined markdown body
   - Warn to stderr if 10-20 KiB
   - Error if >20 KiB (truncate to 20 KiB)

4. **Handle missing files gracefully**
   - Return None if no knowledge files exist
   - Don't error on missing global/project context
   - Log debug message when loading

**Deliverables:**
- [ ] `knowledge.py` module with loading function
- [ ] Size validation with warnings/errors
- [ ] Integration with existing CoDeps

### Phase 4: Prompt Integration (0.5 hours)

**Goal:** Inject loaded knowledge into system prompt.

**Tasks:**

1. **Update `co_cli/prompts/system.py`**
   - Import `load_internal_knowledge()`
   - Call during prompt assembly
   - Inject after personality, before project instructions

2. **Format knowledge section**
   ```markdown
   ## Internal Knowledge

   ### Global Context
   [global body]

   ### Project Context
   [project body]
   ```

3. **Handle None case**
   - Skip section entirely if no knowledge exists
   - Don't add empty headers

**Deliverables:**
- [ ] Knowledge injection in prompt assembly
- [ ] Proper section formatting
- [ ] Integration test

### Phase 5: Memory Tools Implementation (3 hours)

**Goal:** Implement `save_memory`, `recall_memory`, `list_memories` tools.

**Tasks:**

1. **Create `co_cli/tools/memory.py` module**
   - Three tools using `@agent.tool()` pattern
   - Access to `RunContext[CoDeps]`
   - Approval required for `save_memory`

2. **Implement `save_memory` tool**
   ```python
   @agent.tool(requires_approval=True)
   async def save_memory(
       ctx: RunContext[CoDeps],
       content: str,
       tags: list[str] | None = None,
   ) -> dict[str, Any]:
       """Save a memory to knowledge/memories/ directory."""
   ```
   - Generate next ID from existing files
   - Slugify first 50 chars for filename
   - Create YAML frontmatter with id, created, tags, source
   - Write markdown file with frontmatter + body
   - Return display message + metadata

3. **Implement `recall_memory` tool**
   ```python
   @agent.tool()
   async def recall_memory(
       ctx: RunContext[CoDeps],
       query: str,
       max_results: int = 5,
   ) -> dict[str, Any]:
       """Search memories using grep + frontmatter scan."""
   ```
   - Glob all `.co-cli/knowledge/memories/*.md` files
   - Use ripgrep to search content
   - Parse frontmatter for tag matching
   - Sort by relevance (grep rank) and recency
   - Return top N matches with metadata
   - Format as markdown list for display

4. **Implement `list_memories` tool**
   ```python
   @agent.tool()
   async def list_memories(
       ctx: RunContext[CoDeps],
   ) -> dict[str, Any]:
       """List all memories with IDs and metadata."""
   ```
   - Glob all memory files
   - Parse frontmatter for id, created, tags
   - Extract first line of body as summary
   - Return formatted list with metadata

5. **Helper functions**
   ```python
   def _next_memory_id() -> int
   def _slugify(text: str) -> str
   def _search_memories(query: str, memory_dir: Path) -> list[dict]
   ```

**Deliverables:**
- [ ] `tools/memory.py` with 3 tools + helpers
- [ ] Approval flow for save_memory
- [ ] grep-based search implementation
- [ ] Proper return format (display + metadata)

### Phase 6: Agent Integration (0.5 hours)

**Goal:** Register memory tools with agent.

**Tasks:**

1. **Update `co_cli/agent.py`**
   - Import memory tools from `co_cli.tools.memory`
   - Register with agent instance
   - Verify tools appear in tool list

2. **Verify tool signatures**
   - Check RunContext[CoDeps] access
   - Verify approval setting
   - Test tool discovery

**Deliverables:**
- [ ] Memory tools registered with agent
- [ ] Tools visible in `co status --tools`

---

## Code Specifications

### Module: `co_cli/_frontmatter.py`

**Purpose:** YAML frontmatter parsing and validation utilities.

**Functions:**

```python
"""YAML frontmatter parsing and validation utilities.

Markdown files in knowledge/ use YAML frontmatter for metadata:
---
version: 1
updated: 2026-02-09T14:30:00Z
---

Body content here...
"""

import re
from typing import Any
import yaml


def parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown content.

    Args:
        content: Full markdown file content

    Returns:
        (frontmatter_dict, body_markdown) tuple

    Raises:
        ValueError: If frontmatter is malformed

    Example:
        >>> fm, body = parse_frontmatter(content)
        >>> print(fm["version"])
        1
    """
    # Match frontmatter: ---\n<yaml>\n---\n
    pattern = r'^---\s*\n(.*?)\n---\s*\n(.*)$'
    match = re.match(pattern, content, re.DOTALL)

    if not match:
        # No frontmatter - return empty dict and full content
        return {}, content

    yaml_str = match.group(1)
    body = match.group(2)

    try:
        frontmatter = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in frontmatter: {e}")

    if not isinstance(frontmatter, dict):
        raise ValueError("Frontmatter must be a YAML object (dict)")

    return frontmatter, body


def strip_frontmatter(content: str) -> str:
    """Strip YAML frontmatter, return only body content.

    Args:
        content: Full markdown file content

    Returns:
        Body content without frontmatter

    Example:
        >>> body = strip_frontmatter(content)
        >>> assert body.startswith("# User")
    """
    _, body = parse_frontmatter(content)
    return body


def validate_context_frontmatter(fm: dict[str, Any]) -> None:
    """Validate context.md frontmatter fields.

    Required fields:
        - version: int (currently must be 1)
        - updated: ISO8601 timestamp string

    Args:
        fm: Frontmatter dictionary

    Raises:
        ValueError: If validation fails
    """
    if "version" not in fm:
        raise ValueError("Missing required field: version")

    if fm["version"] != 1:
        raise ValueError(f"Unsupported version: {fm['version']} (expected 1)")

    if "updated" not in fm:
        raise ValueError("Missing required field: updated")

    # Validate ISO8601 format (basic check)
    updated = fm["updated"]
    if not isinstance(updated, str):
        raise ValueError("Field 'updated' must be string")

    if not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', updated):
        raise ValueError(f"Field 'updated' must be ISO8601 format: {updated}")


def validate_memory_frontmatter(fm: dict[str, Any]) -> None:
    """Validate memory file frontmatter fields.

    Required fields:
        - id: int (unique memory ID)
        - created: ISO8601 timestamp string

    Optional fields:
        - tags: list[str]
        - source: str (e.g., "user-told", "agent-inferred")

    Args:
        fm: Frontmatter dictionary

    Raises:
        ValueError: If validation fails
    """
    if "id" not in fm:
        raise ValueError("Missing required field: id")

    if not isinstance(fm["id"], int):
        raise ValueError("Field 'id' must be integer")

    if "created" not in fm:
        raise ValueError("Missing required field: created")

    # Validate ISO8601 format
    created = fm["created"]
    if not isinstance(created, str):
        raise ValueError("Field 'created' must be string")

    if not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', created):
        raise ValueError(f"Field 'created' must be ISO8601 format: {created}")

    # Validate optional fields
    if "tags" in fm:
        if not isinstance(fm["tags"], list):
            raise ValueError("Field 'tags' must be list")
        if not all(isinstance(t, str) for t in fm["tags"]):
            raise ValueError("All tags must be strings")

    if "source" in fm:
        if not isinstance(fm["source"], str):
            raise ValueError("Field 'source' must be string")
```

**Tests Required:**
- Parse valid frontmatter
- Parse missing frontmatter (return empty dict)
- Parse malformed YAML (raise ValueError)
- Validate context frontmatter (valid/invalid cases)
- Validate memory frontmatter (valid/invalid cases)
- Strip frontmatter correctly

---

### Module: `co_cli/knowledge.py`

**Purpose:** Load internal knowledge from markdown files.

**Constants:**

```python
SIZE_TARGET = 10 * 1024  # 10 KiB soft limit (warn)
SIZE_LIMIT = 20 * 1024   # 20 KiB hard limit (error)
```

**Functions:**

```python
"""Internal knowledge loading from markdown files.

Loads always-loaded context from:
  - ~/.config/co-cli/knowledge/context.md (global)
  - .co-cli/knowledge/context.md (project, overrides global)

Format: Markdown with YAML frontmatter
Budget: 10 KiB soft / 20 KiB hard limit
"""

import sys
from pathlib import Path
from loguru import logger

from co_cli._frontmatter import (
    parse_frontmatter,
    strip_frontmatter,
    validate_context_frontmatter,
)


SIZE_TARGET = 10 * 1024  # 10 KiB soft limit
SIZE_LIMIT = 20 * 1024   # 20 KiB hard limit


def load_internal_knowledge() -> str | None:
    """Load internal knowledge from markdown files.

    Loads context.md from global and project locations:
      - Global: ~/.config/co-cli/knowledge/context.md
      - Project: .co-cli/knowledge/context.md (overrides global)

    Returns markdown-formatted knowledge for prompt injection.
    Validates frontmatter and enforces size limits.

    Returns:
        Markdown string with global/project sections, or None if no knowledge

    Raises:
        ValueError: If frontmatter validation fails or size exceeds hard limit

    Side effects:
        - Prints warnings to stderr if size 10-20 KiB
        - Prints error to stderr if size >20 KiB (then truncates)
    """
    global_path = Path.home() / ".config/co-cli/knowledge/context.md"
    project_path = Path.cwd() / ".co-cli/knowledge/context.md"

    sections = []

    # Load global context
    if global_path.exists():
        logger.debug(f"Loading global knowledge: {global_path}")
        try:
            content = global_path.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)
            validate_context_frontmatter(frontmatter)

            body = body.strip()
            if body:
                sections.append(("Global Context", body))
        except Exception as e:
            logger.warning(f"Failed to load global knowledge: {e}")
            # Continue without global knowledge

    # Load project context (overrides global on conflicts)
    if project_path.exists():
        logger.debug(f"Loading project knowledge: {project_path}")
        try:
            content = project_path.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)
            validate_context_frontmatter(frontmatter)

            body = body.strip()
            if body:
                sections.append(("Project Context", body))
        except Exception as e:
            logger.error(f"Failed to load project knowledge: {e}")
            raise

    # No knowledge to load
    if not sections:
        logger.debug("No internal knowledge found")
        return None

    # Combine sections
    combined = "\n\n".join(f"### {title}\n\n{body}" for title, body in sections)
    knowledge = f"## Internal Knowledge\n\n{combined}"

    # Validate size
    size = len(knowledge.encode("utf-8"))

    if size > SIZE_LIMIT:
        # Hard limit exceeded - truncate
        print(
            f"ERROR: Knowledge size {size} bytes exceeds {SIZE_LIMIT} byte limit. "
            f"Truncating to {SIZE_LIMIT} bytes. Please reduce content in context.md files.",
            file=sys.stderr,
        )
        # Truncate to SIZE_LIMIT bytes
        knowledge = knowledge.encode("utf-8")[:SIZE_LIMIT].decode("utf-8", errors="ignore")

    elif size > SIZE_TARGET:
        # Soft limit exceeded - warn
        print(
            f"WARNING: Knowledge size {size} bytes exceeds {SIZE_TARGET} byte target. "
            f"Consider reducing content in context.md files.",
            file=sys.stderr,
        )

    logger.info(f"Loaded {len(sections)} knowledge section(s), {size} bytes")
    return knowledge
```

**Tests Required:**
- Load valid global context
- Load valid project context
- Load both (project overrides global)
- Load neither (return None)
- Validate frontmatter (reject invalid)
- Size validation (under target, warn, error)
- Truncate when >20 KiB
- Handle malformed files gracefully

---

### Module: `co_cli/tools/memory.py`

**Purpose:** Memory management tools (save, recall, list).

**Implementation:**

```python
"""Memory management tools for persistent knowledge.

Tools:
  - save_memory: Save a memory to knowledge/memories/
  - recall_memory: Search memories by keyword
  - list_memories: List all memories with metadata

Storage: Markdown files with YAML frontmatter
Format: {id:03d}-{slug}.md
Location: .co-cli/knowledge/memories/
"""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from loguru import logger

from co_cli.deps import CoDeps
from co_cli._frontmatter import parse_frontmatter
import yaml


def _next_memory_id() -> int:
    """Get next available memory ID.

    Scans existing memory files in .co-cli/knowledge/memories/
    and returns max(id) + 1.

    Returns:
        Next available ID (starts at 1)
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
    if not memory_dir.exists():
        return 1

    max_id = 0
    for path in memory_dir.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")
            fm, _ = parse_frontmatter(content)
            if "id" in fm and isinstance(fm["id"], int):
                max_id = max(max_id, fm["id"])
        except Exception:
            # Skip malformed files
            continue

    return max_id + 1


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug.

    Args:
        text: Text to slugify

    Returns:
        Lowercase slug with hyphens (max 50 chars)

    Example:
        >>> _slugify("User prefers async/await!")
        'user-prefers-async-await'
    """
    # Lowercase and replace non-alphanumeric with hyphens
    slug = re.sub(r'[^a-z0-9]+', '-', text.lower())
    # Remove leading/trailing hyphens
    slug = slug.strip('-')
    # Limit length
    return slug[:50]


def _search_memories(query: str, memory_dir: Path, max_results: int = 5) -> list[dict[str, Any]]:
    """Search memories using grep + frontmatter scan.

    Phase 1c: Simple grep-based search.
    Phase 2+: Use SQLite FTS5 index.

    Args:
        query: Search query
        memory_dir: Directory containing memory files
        max_results: Maximum results to return

    Returns:
        List of memory dicts with keys: id, path, content, tags, created
    """
    if not memory_dir.exists():
        return []

    results = []
    query_lower = query.lower()

    for path in memory_dir.glob("*.md"):
        try:
            content = path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(content)

            # Search in body and tags
            body_match = query_lower in body.lower()
            tag_match = False
            if "tags" in fm:
                tag_match = any(query_lower in tag.lower() for tag in fm["tags"])

            if body_match or tag_match:
                results.append({
                    "id": fm.get("id", 0),
                    "path": str(path),
                    "content": body.strip(),
                    "tags": fm.get("tags", []),
                    "created": fm.get("created", ""),
                })
        except Exception as e:
            logger.warning(f"Failed to search {path}: {e}")
            continue

    # Sort by recency (created timestamp descending)
    results.sort(key=lambda r: r["created"], reverse=True)

    return results[:max_results]


async def save_memory(
    ctx: RunContext[CoDeps],
    content: str,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Save a memory to knowledge/memories/ directory.

    Creates a markdown file with YAML frontmatter containing
    the memory content and metadata.

    Filename format: {id:03d}-{slug}.md
    Example: 001-prefers-async.md

    Args:
        ctx: Agent runtime context
        content: Memory content (markdown)
        tags: Optional list of tags for categorization

    Returns:
        dict with keys:
            - display: Human-readable confirmation message
            - path: Absolute path to saved file
            - memory_id: Numeric ID of memory

    Tool configuration:
        requires_approval: True (side-effectful)
    """
    # Generate ID and filename
    memory_id = _next_memory_id()
    slug = _slugify(content[:50])
    filename = f"{memory_id:03d}-{slug}.md"

    # Create frontmatter
    frontmatter = {
        "id": memory_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "tags": tags or [],
        "source": "user-told",
    }

    # Build markdown content
    md_content = f"---\n{yaml.dump(frontmatter, default_flow_style=False)}---\n\n{content.strip()}\n"

    # Write file
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True, exist_ok=True)

    file_path = memory_dir / filename
    file_path.write_text(md_content, encoding="utf-8")

    logger.info(f"Saved memory {memory_id}: {filename}")

    return {
        "display": f"Saved memory {memory_id}: {filename}\nLocation: {file_path}",
        "path": str(file_path),
        "memory_id": memory_id,
    }


async def recall_memory(
    ctx: RunContext[CoDeps],
    query: str,
    max_results: int = 5,
) -> dict[str, Any]:
    """Search memories using keyword search.

    Phase 1c: Uses grep + frontmatter scan.
    Phase 2+: Will use SQLite FTS5 index.

    Searches both content and tags. Returns markdown-formatted
    results for injection into current turn context.

    Args:
        ctx: Agent runtime context
        query: Search query (keywords)
        max_results: Maximum number of results (default 5)

    Returns:
        dict with keys:
            - display: Markdown-formatted search results
            - count: Number of results found
            - results: List of result dicts (id, content, tags, created)
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"
    results = _search_memories(query, memory_dir, max_results)

    if not results:
        return {
            "display": f"No memories found matching '{query}'",
            "count": 0,
            "results": [],
        }

    # Format as markdown
    lines = [f"Found {len(results)} memor{'y' if len(results) == 1 else 'ies'} matching '{query}':\n"]

    for r in results:
        lines.append(f"**Memory {r['id']}** (created {r['created'][:10]})")
        if r["tags"]:
            lines.append(f"Tags: {', '.join(r['tags'])}")
        lines.append(f"{r['content']}\n")

    display = "\n".join(lines)

    logger.info(f"Recalled {len(results)} memories for query: {query}")

    return {
        "display": display,
        "count": len(results),
        "results": results,
    }


async def list_memories(
    ctx: RunContext[CoDeps],
) -> dict[str, Any]:
    """List all memories with IDs and metadata.

    Returns summary of all memories: ID, creation date, tags,
    and first line of content as preview.

    Args:
        ctx: Agent runtime context

    Returns:
        dict with keys:
            - display: Markdown-formatted memory list
            - count: Total number of memories
            - memories: List of memory summary dicts
    """
    memory_dir = Path.cwd() / ".co-cli/knowledge/memories"

    if not memory_dir.exists():
        return {
            "display": "No memories saved yet.",
            "count": 0,
            "memories": [],
        }

    memories = []

    for path in sorted(memory_dir.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(content)

            # Extract first line as summary
            first_line = body.strip().split("\n")[0][:80]

            memories.append({
                "id": fm.get("id", 0),
                "created": fm.get("created", ""),
                "tags": fm.get("tags", []),
                "summary": first_line,
                "path": str(path),
            })
        except Exception as e:
            logger.warning(f"Failed to read {path}: {e}")
            continue

    if not memories:
        return {
            "display": "No memories found.",
            "count": 0,
            "memories": [],
        }

    # Sort by ID
    memories.sort(key=lambda m: m["id"])

    # Format as markdown
    lines = [f"Total memories: {len(memories)}\n"]

    for m in memories:
        tags_str = f" [{', '.join(m['tags'])}]" if m["tags"] else ""
        lines.append(f"**{m['id']:03d}** ({m['created'][:10]}){tags_str}: {m['summary']}")

    display = "\n".join(lines)

    logger.info(f"Listed {len(memories)} memories")

    return {
        "display": display,
        "count": len(memories),
        "memories": memories,
    }
```

**Tool Registration (in `co_cli/agent.py`):**

```python
from co_cli.tools.memory import save_memory, recall_memory, list_memories

# Register memory tools
agent.tool(save_memory, requires_approval=True)
agent.tool(recall_memory)
agent.tool(list_memories)
```

**Tests Required:**
- `save_memory`: Create file with frontmatter, increment ID
- `recall_memory`: Search by content and tags
- `list_memories`: List all with summaries
- `_next_memory_id`: Generate sequential IDs
- `_slugify`: Convert text to slug
- `_search_memories`: grep-based search

---

### Module: `co_cli/prompts/system.py`

**Purpose:** System prompt assembly with knowledge injection.

**Update:**

```python
"""System prompt assembly with knowledge injection."""

from co_cli.knowledge import load_internal_knowledge

def assemble_system_prompt(personality: str | None = None) -> str:
    """Assemble full system prompt with all layers.

    Layers (in order):
      1. Base system.md
      2. Personality template (if specified)
      3. Internal knowledge (global + project)          ← Phase 1c NEW
      4. Project instructions.md
      5. [system_reminder] (future: Phase 1d)

    Args:
        personality: Optional personality name (e.g., "sonnet", "finch")

    Returns:
        Full system prompt string
    """
    sections = []

    # 1. Base system prompt
    base = load_base_system_prompt()
    sections.append(base)

    # 2. Personality (optional)
    if personality:
        personality_content = load_personality(personality)
        if personality_content:
            sections.append(personality_content)

    # 3. Internal knowledge (NEW)
    knowledge = load_internal_knowledge()
    if knowledge:
        # Wrap in system-reminder tags for recency bias
        sections.append(f"<system-reminder>\n{knowledge}\n</system-reminder>")

    # 4. Project instructions
    instructions = load_project_instructions()
    if instructions:
        sections.append(instructions)

    return "\n\n".join(sections)
```

**Tests Required:**
- Prompt includes knowledge when present
- Prompt excludes knowledge when absent
- Knowledge appears after personality, before instructions
- Proper section formatting

---

## Test Specifications

### Module: `tests/test_frontmatter.py` (8 tests)

**Purpose:** Test YAML frontmatter parsing and validation.

```python
"""Tests for YAML frontmatter parsing."""

import pytest
from co_cli._frontmatter import (
    parse_frontmatter,
    strip_frontmatter,
    validate_context_frontmatter,
    validate_memory_frontmatter,
)


def test_parse_valid_frontmatter():
    """Parse valid YAML frontmatter."""
    content = """---
version: 1
updated: 2026-02-09T14:30:00Z
---

# User
- Name: Test
"""
    fm, body = parse_frontmatter(content)
    assert fm["version"] == 1
    assert fm["updated"] == "2026-02-09T14:30:00Z"
    assert body.strip().startswith("# User")


def test_parse_missing_frontmatter():
    """Parse content without frontmatter."""
    content = "# Just markdown content"
    fm, body = parse_frontmatter(content)
    assert fm == {}
    assert body == content


def test_parse_malformed_yaml():
    """Reject malformed YAML in frontmatter."""
    content = """---
version: 1
invalid yaml: [
---

Body
"""
    with pytest.raises(ValueError, match="Invalid YAML"):
        parse_frontmatter(content)


def test_strip_frontmatter():
    """Strip frontmatter, return only body."""
    content = """---
version: 1
---

Body content
"""
    body = strip_frontmatter(content)
    assert body.strip() == "Body content"
    assert "version" not in body


def test_validate_context_valid():
    """Validate valid context frontmatter."""
    fm = {
        "version": 1,
        "updated": "2026-02-09T14:30:00Z",
    }
    validate_context_frontmatter(fm)  # Should not raise


def test_validate_context_missing_version():
    """Reject context missing version field."""
    fm = {"updated": "2026-02-09T14:30:00Z"}
    with pytest.raises(ValueError, match="Missing required field: version"):
        validate_context_frontmatter(fm)


def test_validate_memory_valid():
    """Validate valid memory frontmatter."""
    fm = {
        "id": 1,
        "created": "2026-02-09T14:30:00Z",
        "tags": ["python", "style"],
        "source": "user-told",
    }
    validate_memory_frontmatter(fm)  # Should not raise


def test_validate_memory_invalid_id():
    """Reject memory with non-integer ID."""
    fm = {
        "id": "not-an-int",
        "created": "2026-02-09T14:30:00Z",
    }
    with pytest.raises(ValueError, match="Field 'id' must be integer"):
        validate_memory_frontmatter(fm)
```

---

### Module: `tests/test_knowledge.py` (7 tests)

**Purpose:** Test internal knowledge loading from markdown files.

```python
"""Tests for internal knowledge loading."""

import pytest
from pathlib import Path
from co_cli.knowledge import load_internal_knowledge, SIZE_TARGET, SIZE_LIMIT


def test_load_valid_project_context(tmp_path, monkeypatch):
    """Load valid project context.md."""
    monkeypatch.chdir(tmp_path)

    knowledge_dir = tmp_path / ".co-cli/knowledge"
    knowledge_dir.mkdir(parents=True)

    context_file = knowledge_dir / "context.md"
    context_file.write_text("""---
version: 1
updated: 2026-02-09T14:30:00Z
---

# User
- Name: Test User
- Prefers: async/await
""")

    result = load_internal_knowledge()
    assert result is not None
    assert "## Internal Knowledge" in result
    assert "### Project Context" in result
    assert "# User" in result
    assert "Test User" in result


def test_load_valid_global_context(tmp_path, monkeypatch):
    """Load valid global context.md."""
    monkeypatch.chdir(tmp_path)

    global_knowledge = Path.home() / ".config/co-cli/knowledge"
    global_knowledge.mkdir(parents=True, exist_ok=True)

    global_context = global_knowledge / "context.md"
    global_context.write_text("""---
version: 1
updated: 2026-02-09T14:30:00Z
---

# User
- Name: Global User
""")

    result = load_internal_knowledge()
    assert result is not None
    assert "### Global Context" in result
    assert "Global User" in result


def test_load_both_contexts(tmp_path, monkeypatch):
    """Load both global and project contexts."""
    monkeypatch.chdir(tmp_path)

    # Global context
    global_knowledge = Path.home() / ".config/co-cli/knowledge"
    global_knowledge.mkdir(parents=True, exist_ok=True)
    (global_knowledge / "context.md").write_text("""---
version: 1
updated: 2026-02-09T14:30:00Z
---

Global content
""")

    # Project context
    project_knowledge = tmp_path / ".co-cli/knowledge"
    project_knowledge.mkdir(parents=True)
    (project_knowledge / "context.md").write_text("""---
version: 1
updated: 2026-02-09T14:30:00Z
---

Project content
""")

    result = load_internal_knowledge()
    assert "### Global Context" in result
    assert "### Project Context" in result
    assert "Global content" in result
    assert "Project content" in result


def test_load_no_knowledge(tmp_path, monkeypatch):
    """Return None when no knowledge exists."""
    monkeypatch.chdir(tmp_path)
    result = load_internal_knowledge()
    assert result is None


def test_size_under_target(tmp_path, monkeypatch, capsys):
    """Load silently when size under 10 KiB."""
    monkeypatch.chdir(tmp_path)

    knowledge_dir = tmp_path / ".co-cli/knowledge"
    knowledge_dir.mkdir(parents=True)

    # Create small context (< 10 KiB)
    content = "# User\n" + ("- Item\n" * 100)  # ~800 bytes
    (knowledge_dir / "context.md").write_text(f"""---
version: 1
updated: 2026-02-09T14:30:00Z
---

{content}
""")

    result = load_internal_knowledge()
    assert result is not None

    # No warnings
    captured = capsys.readouterr()
    assert "WARNING" not in captured.err
    assert "ERROR" not in captured.err


def test_size_warn_threshold(tmp_path, monkeypatch, capsys):
    """Warn when size between 10-20 KiB."""
    monkeypatch.chdir(tmp_path)

    knowledge_dir = tmp_path / ".co-cli/knowledge"
    knowledge_dir.mkdir(parents=True)

    # Create context at 15 KiB
    content = "# User\n" + ("- Item with some text\n" * 600)  # ~15 KiB
    (knowledge_dir / "context.md").write_text(f"""---
version: 1
updated: 2026-02-09T14:30:00Z
---

{content}
""")

    result = load_internal_knowledge()
    assert result is not None

    # Warning printed
    captured = capsys.readouterr()
    assert "WARNING" in captured.err
    assert f"exceeds {SIZE_TARGET}" in captured.err


def test_size_error_threshold(tmp_path, monkeypatch, capsys):
    """Error and truncate when size >20 KiB."""
    monkeypatch.chdir(tmp_path)

    knowledge_dir = tmp_path / ".co-cli/knowledge"
    knowledge_dir.mkdir(parents=True)

    # Create context at 25 KiB
    content = "# User\n" + ("- Item with some text\n" * 1000)  # ~25 KiB
    (knowledge_dir / "context.md").write_text(f"""---
version: 1
updated: 2026-02-09T14:30:00Z
---

{content}
""")

    result = load_internal_knowledge()
    assert result is not None

    # Result truncated to SIZE_LIMIT
    size = len(result.encode("utf-8"))
    assert size <= SIZE_LIMIT

    # Error printed
    captured = capsys.readouterr()
    assert "ERROR" in captured.err
    assert f"exceeds {SIZE_LIMIT}" in captured.err
```

---

### Module: `tests/test_memory_tools.py` (7 tests)

**Purpose:** Test memory management tools (save, recall, list).

```python
"""Tests for memory tools."""

import pytest
from pathlib import Path
from co_cli.tools.memory import (
    save_memory,
    recall_memory,
    list_memories,
    _next_memory_id,
    _slugify,
    _search_memories,
)
from co_cli.deps import CoDeps
from pydantic_ai import RunContext


@pytest.fixture
def mock_ctx():
    """Mock RunContext for tool tests."""
    deps = CoDeps(settings={}, agent=None)
    return RunContext(deps=deps, retry=0)


def test_next_memory_id_empty(tmp_path, monkeypatch):
    """Get ID 1 when no memories exist."""
    monkeypatch.chdir(tmp_path)
    assert _next_memory_id() == 1


def test_next_memory_id_existing(tmp_path, monkeypatch):
    """Get max(id) + 1 when memories exist."""
    monkeypatch.chdir(tmp_path)

    memory_dir = tmp_path / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)

    # Create memory with id=5
    (memory_dir / "005-test.md").write_text("""---
id: 5
created: 2026-02-09T14:30:00Z
---

Memory content
""")

    assert _next_memory_id() == 6


def test_slugify():
    """Convert text to URL-friendly slug."""
    assert _slugify("User prefers async/await!") == "user-prefers-async-await"
    assert _slugify("Multiple   spaces") == "multiple-spaces"
    assert _slugify("A" * 100) == "a" * 50  # Truncate to 50 chars


async def test_save_memory(tmp_path, monkeypatch, mock_ctx):
    """Save memory creates markdown file with frontmatter."""
    monkeypatch.chdir(tmp_path)

    result = await save_memory(
        mock_ctx,
        content="User prefers async/await over callbacks",
        tags=["python", "style"],
    )

    # Check return value
    assert result["memory_id"] == 1
    assert "001-user-prefers-async-await.md" in result["path"]

    # Check file created
    memory_file = Path(result["path"])
    assert memory_file.exists()

    # Check file content
    content = memory_file.read_text()
    assert "---" in content
    assert "id: 1" in content
    assert "tags:" in content
    assert "- python" in content
    assert "- style" in content
    assert "User prefers async/await" in content


async def test_recall_memory_found(tmp_path, monkeypatch, mock_ctx):
    """Recall finds matching memories."""
    monkeypatch.chdir(tmp_path)

    # Create test memory
    memory_dir = tmp_path / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)
    (memory_dir / "001-async.md").write_text("""---
id: 1
created: 2026-02-09T14:30:00Z
tags: [python]
---

User prefers async/await over callbacks
""")

    result = await recall_memory(mock_ctx, query="async")

    assert result["count"] == 1
    assert "Memory 1" in result["display"]
    assert "async/await" in result["display"]


async def test_recall_memory_not_found(tmp_path, monkeypatch, mock_ctx):
    """Recall returns empty when no matches."""
    monkeypatch.chdir(tmp_path)

    result = await recall_memory(mock_ctx, query="nonexistent")

    assert result["count"] == 0
    assert "No memories found" in result["display"]


async def test_list_memories(tmp_path, monkeypatch, mock_ctx):
    """List all memories with summaries."""
    monkeypatch.chdir(tmp_path)

    # Create test memories
    memory_dir = tmp_path / ".co-cli/knowledge/memories"
    memory_dir.mkdir(parents=True)

    (memory_dir / "001-async.md").write_text("""---
id: 1
created: 2026-02-09T14:30:00Z
tags: [python]
---

User prefers async/await over callbacks
""")

    (memory_dir / "002-sqlalchemy.md").write_text("""---
id: 2
created: 2026-02-09T15:00:00Z
tags: [database]
---

This project uses SQLAlchemy ORM exclusively
""")

    result = await list_memories(mock_ctx)

    assert result["count"] == 2
    assert "Total memories: 2" in result["display"]
    assert "001" in result["display"]
    assert "002" in result["display"]
    assert "async/await" in result["display"]
    assert "SQLAlchemy" in result["display"]
```

---

### Module: `tests/test_prompts.py` (3 tests, updated)

**Purpose:** Test prompt assembly with knowledge injection.

```python
"""Tests for prompt assembly."""

import pytest
from pathlib import Path
from co_cli.prompts.system import assemble_system_prompt


def test_prompt_includes_knowledge_when_present(tmp_path, monkeypatch):
    """System prompt includes knowledge when context.md exists."""
    monkeypatch.chdir(tmp_path)

    # Create knowledge
    knowledge_dir = tmp_path / ".co-cli/knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "context.md").write_text("""---
version: 1
updated: 2026-02-09T14:30:00Z
---

# User
- Name: Test User
""")

    prompt = assemble_system_prompt()

    assert "## Internal Knowledge" in prompt
    assert "### Project Context" in prompt
    assert "Test User" in prompt


def test_prompt_excludes_knowledge_when_absent(tmp_path, monkeypatch):
    """System prompt excludes knowledge when no context.md."""
    monkeypatch.chdir(tmp_path)

    prompt = assemble_system_prompt()

    assert "## Internal Knowledge" not in prompt


def test_prompt_knowledge_ordering(tmp_path, monkeypatch):
    """Knowledge appears after personality, before instructions."""
    monkeypatch.chdir(tmp_path)

    # Create knowledge
    knowledge_dir = tmp_path / ".co-cli/knowledge"
    knowledge_dir.mkdir(parents=True)
    (knowledge_dir / "context.md").write_text("""---
version: 1
updated: 2026-02-09T14:30:00Z
---

Knowledge content
""")

    # Create instructions
    instructions_file = tmp_path / ".co-cli/instructions.md"
    instructions_file.write_text("# Project Instructions\n\nInstructions content")

    prompt = assemble_system_prompt(personality="sonnet")

    # Check ordering
    knowledge_pos = prompt.find("## Internal Knowledge")
    instructions_pos = prompt.find("# Project Instructions")

    assert knowledge_pos > 0
    assert instructions_pos > 0
    assert knowledge_pos < instructions_pos  # Knowledge before instructions
```

---

## Verification Procedures

### Manual Testing Checklist

#### 1. Knowledge Loading

**Setup:**
```bash
# Create global context
mkdir -p ~/.config/co-cli/knowledge
cat > ~/.config/co-cli/knowledge/context.md <<'EOF'
---
version: 1
updated: 2026-02-09T14:30:00Z
---

# User
- Name: Test User
- Timezone: America/Los_Angeles
EOF

# Create project context
mkdir -p .co-cli/knowledge
cat > .co-cli/knowledge/context.md <<'EOF'
---
version: 1
updated: 2026-02-09T14:30:00Z
---

# Project
- Type: Python CLI
- Test policy: functional only
EOF
```

**Test:**
```bash
uv run co chat
# Check banner/startup messages for knowledge loading
# Look for: "Loaded 2 knowledge section(s), NNNN bytes"
```

**Verify:**
- [ ] Global context loads without errors
- [ ] Project context loads without errors
- [ ] Size reported in logs
- [ ] No warnings if size <10 KiB

#### 2. Size Warnings

**Setup:**
```bash
# Create large context (15 KiB)
python -c "print('# User\n' + ('- Item with text\n' * 600))" > .co-cli/knowledge/context.md
# Add frontmatter manually
```

**Test:**
```bash
uv run co chat
# Check stderr for warnings
```

**Verify:**
- [ ] Warning printed to stderr
- [ ] Warning mentions size and target (10 KiB)
- [ ] Chat still starts successfully
- [ ] Knowledge still loaded

#### 3. Save Memory Tool

**Test:**
```bash
uv run co chat
> save a memory: I prefer async/await over callbacks, tag it with python and style
# Agent should call save_memory tool
# Tool requires approval
```

**Verify:**
- [ ] Approval prompt appears
- [ ] After approval, memory file created
- [ ] File at `.co-cli/knowledge/memories/001-*.md`
- [ ] File contains YAML frontmatter
- [ ] Frontmatter has id, created, tags
- [ ] Body contains memory content
- [ ] Confirmation message displayed

#### 4. Recall Memory Tool

**Test:**
```bash
uv run co chat
> recall memories about async
# Agent should call recall_memory tool
```

**Verify:**
- [ ] Search executes successfully
- [ ] Results displayed with memory ID, date
- [ ] Content preview shown
- [ ] Tags shown if present
- [ ] No errors on empty results

#### 5. List Memories Tool

**Test:**
```bash
uv run co chat
> list all my memories
# Agent should call list_memories tool
```

**Verify:**
- [ ] All memories listed
- [ ] Each shows: ID, date, tags, summary
- [ ] Summary is first line (truncated to 80 chars)
- [ ] Sorted by ID
- [ ] Count displayed

#### 6. Manual Editing

**Setup:**
```bash
# Edit context manually
vim .co-cli/knowledge/context.md
# Add a new section:
# # Custom
# - My custom preference
```

**Test:**
```bash
uv run co chat
# Start new session, check if change loaded
```

**Verify:**
- [ ] Edited content appears in new session
- [ ] No errors on reload
- [ ] Agent can see updated content

#### 7. Malformed Files

**Setup:**
```bash
# Create malformed frontmatter
cat > .co-cli/knowledge/context.md <<'EOF'
---
invalid yaml: [
---
Content
EOF
```

**Test:**
```bash
uv run co chat
# Check error handling
```

**Verify:**
- [ ] Error logged to stderr
- [ ] Session starts anyway (graceful degradation)
- [ ] Other knowledge still loads if present

#### 8. Missing Files

**Test:**
```bash
# Remove all knowledge
rm -rf .co-cli/knowledge ~/.config/co-cli/knowledge
uv run co chat
```

**Verify:**
- [ ] No errors
- [ ] No knowledge section in prompt
- [ ] Chat works normally

---

### Automated Test Execution

```bash
# Run all Phase 1c tests
uv run pytest tests/test_frontmatter.py -v
uv run pytest tests/test_knowledge.py -v
uv run pytest tests/test_memory_tools.py -v
uv run pytest tests/test_prompts.py -v

# Run with coverage
uv run pytest tests/test_frontmatter.py tests/test_knowledge.py tests/test_memory_tools.py tests/test_prompts.py --cov=co_cli --cov-report=term-missing
```

**Expected Results:**
- [ ] 25 tests pass (8 + 7 + 7 + 3)
- [ ] No test failures
- [ ] Coverage >90% for new modules
- [ ] No warnings about missing imports

---

## Documentation Updates

### 1. User Documentation

**File: `README.md`**

Add section after "Configuration":

```markdown
## Internal Knowledge

Co can remember user preferences, project insights, and learned facts across sessions.

### Always-Loaded Context

Create context files for persistent knowledge:

**Global (all projects):**
```
~/.config/co-cli/knowledge/context.md
```

**Project (current directory):**
```
.co-cli/knowledge/context.md
```

**Format:**
```markdown
---
version: 1
updated: 2026-02-09T14:30:00Z
---

# User
- Name: Your Name
- Prefers: concise explanations

# Project
- Type: Python CLI
- Test policy: functional only
```

Project context overrides global context on conflicts.

### Memory Tools

During chat, Co can save and recall memories:

- **Save:** "Remember that I prefer async/await over callbacks"
- **Recall:** "What do you remember about async?"
- **List:** "Show me all my memories"

Memories are saved to `.co-cli/knowledge/memories/` as markdown files.

### Manual Editing

All knowledge files are plain markdown. Edit with any text editor:

```bash
vim .co-cli/knowledge/context.md
vim .co-cli/knowledge/memories/001-prefers-async.md
```

Changes take effect in the next session.

### Size Limits

- Always-loaded context: 10 KiB soft / 20 KiB hard limit
- On-demand memories: No limit (managed by agent)
```

### 2. Component Documentation

**File: `docs/DESIGN-14-internal-knowledge.md` (new)**

```markdown
# DESIGN-14: Internal Knowledge System

## What & How

Internal knowledge system provides persistent memory across Co sessions. Uses markdown files as source of truth (lakehouse pattern) with optional SQLite index for retrieval.

**Architecture:**
```
~/.config/co-cli/knowledge/context.md    (global, 3 KiB)
.co-cli/knowledge/context.md             (project, 7 KiB, overrides global)
.co-cli/knowledge/memories/*.md          (on-demand, no limit)
```

Always-loaded context injected into system prompt. Memories retrieved via tools (`save_memory`, `recall_memory`, `list_memories`).

## Core Logic

### Loading

Function: `load_internal_knowledge()` in `co_cli/knowledge.py`

Processing:
1. Read global context: `~/.config/co-cli/knowledge/context.md` (if exists)
2. Read project context: `.co-cli/knowledge/context.md` (if exists)
3. Parse YAML frontmatter, validate version/updated fields
4. Strip frontmatter, combine bodies with section headers
5. Validate size (10 KiB warn, 20 KiB error)
6. Return markdown for prompt injection

Precedence: Project overrides global on conflicts.

### Frontmatter Parsing

Module: `co_cli/_frontmatter.py`

Functions:
- `parse_frontmatter(content) -> (dict, str)`: Split YAML + body
- `strip_frontmatter(content) -> str`: Return body only
- `validate_context_frontmatter(fm)`: Check required fields
- `validate_memory_frontmatter(fm)`: Check required fields

Format: YAML between `---` delimiters, followed by markdown body.

### Memory Tools

Module: `co_cli/tools/memory.py`

**save_memory:**
- Generate next ID (max existing + 1)
- Slugify content for filename: `{id:03d}-{slug}.md`
- Create YAML frontmatter (id, created, tags, source)
- Write markdown file to `.co-cli/knowledge/memories/`
- Requires approval (side-effectful)

**recall_memory:**
- Search using grep + frontmatter scan (Phase 1c)
- Match query against body content and tags
- Sort by recency (created timestamp descending)
- Return top N matches formatted as markdown

**list_memories:**
- Glob all `.md` files in memories/
- Parse frontmatter for metadata
- Extract first line as summary
- Return formatted list with ID, date, tags, summary

### Retrieval Evolution

**Phase 1c (MVP): grep + frontmatter**
- Suitable for <200 memories
- No dependencies beyond ripgrep
- Full-text search via grep, tag match via frontmatter

**Phase 2 (Scale): SQLite FTS5**
- Derived index from markdown files
- BM25 keyword search
- Files remain source of truth

**Phase 3 (Semantic): Hybrid FTS5 + vectors**
- Add sqlite-vec for embeddings
- Reciprocal Rank Fusion (RRF) merge
- Handles vocabulary mismatch

### Prompt Injection

Injection point: After personality, before project instructions

Format:
```markdown
<system-reminder>
## Internal Knowledge

### Global Context
[body of global context.md]

### Project Context
[body of project context.md]
</system-reminder>
```

Frontmatter stripped before injection (metadata is for tooling, not LLM).

### Error Handling

**Malformed files:**
- Log warning, skip file, continue processing
- Don't crash on bad YAML or missing fields

**Size limits:**
- Warn to stderr if 10-20 KiB
- Error to stderr + truncate if >20 KiB
- Always allow session to start

**Missing files:**
- Return None from loader (no knowledge)
- Don't error on missing global/project context

### Security

**No injection risks:**
- Markdown bodies injected into system prompt (not user input)
- YAML parsing via `yaml.safe_load()` (no code execution)

**File access:**
- Only reads from known directories
- No user-specified paths in tools

## Config

| Setting | Env Var | Default | Description |
|---------|---------|---------|-------------|
| Global context path | - | `~/.config/co-cli/knowledge/context.md` | Global always-loaded context |
| Project context path | - | `.co-cli/knowledge/context.md` | Project always-loaded context |
| Memories directory | - | `.co-cli/knowledge/memories/` | On-demand memory files |
| Soft size limit | - | 10 KiB | Warn if exceeded |
| Hard size limit | - | 20 KiB | Error + truncate if exceeded |

**Budget Allocation:**
- Global context: 3 KiB soft
- Project context: 7 KiB soft
- Total always-loaded: 10 KiB soft / 20 KiB hard

## Files

| File | Purpose |
|------|---------|
| `co_cli/_frontmatter.py` | YAML frontmatter parsing and validation |
| `co_cli/knowledge.py` | Internal knowledge loading function |
| `co_cli/tools/memory.py` | Memory management tools (save, recall, list) |
| `co_cli/prompts/system.py` | Prompt assembly with knowledge injection |
| `tests/test_frontmatter.py` | Frontmatter parsing tests (8 tests) |
| `tests/test_knowledge.py` | Knowledge loading tests (7 tests) |
| `tests/test_memory_tools.py` | Memory tools tests (7 tests) |
| `tests/test_prompts.py` | Prompt assembly tests (3 updated) |
| `examples/knowledge/context.md` | Example context template |
| `examples/knowledge/memories/001-example.md` | Example memory template |
```

### 3. CLAUDE.md Updates

**File: `CLAUDE.md`**

Add to "Architecture" section:

```markdown
## Internal Knowledge

Co loads persistent knowledge from markdown files:
- `~/.config/co-cli/knowledge/context.md` - Global context (3 KiB)
- `.co-cli/knowledge/context.md` - Project context (7 KiB, overrides global)
- `.co-cli/knowledge/memories/*.md` - On-demand memories (no limit)

**Format:** Markdown with YAML frontmatter

**Tools:** `save_memory`, `recall_memory`, `list_memories` (agent-callable)

**Retrieval:** grep + frontmatter (Phase 1c) → FTS5 (Phase 2) → hybrid (Phase 3)

See `docs/DESIGN-14-internal-knowledge.md` for implementation.
```

---

## Success Criteria

Phase 1c is complete when ALL of the following are achieved:

### Functional Requirements

- [ ] **Load global context:** `~/.config/co-cli/knowledge/context.md` loads at session start
- [ ] **Load project context:** `.co-cli/knowledge/context.md` loads at session start
- [ ] **Precedence:** Project context overrides global on conflicts
- [ ] **Prompt injection:** Knowledge appears after personality, before instructions
- [ ] **Save memory:** `save_memory` tool creates markdown file with frontmatter
- [ ] **Recall memory:** `recall_memory` tool searches via grep + frontmatter
- [ ] **List memories:** `list_memories` tool lists all with summaries
- [ ] **Approval flow:** `save_memory` requires approval before execution

### Quality Requirements

- [ ] **Test coverage:** All 25 tests pass (8 frontmatter + 7 knowledge + 7 memory + 3 prompt)
- [ ] **Size validation:** Warnings at 10 KiB, errors at 20 KiB
- [ ] **Error handling:** Graceful degradation on malformed files
- [ ] **Manual editing:** Users can edit markdown files directly
- [ ] **Git-friendly:** Clean diffs for knowledge changes

### Documentation Requirements

- [ ] **User docs:** README.md section on internal knowledge
- [ ] **Component docs:** `DESIGN-14-internal-knowledge.md` created
- [ ] **CLAUDE.md:** Architecture section updated
- [ ] **Examples:** Template files created in `examples/knowledge/`

### Integration Requirements

- [ ] **Agent registration:** Memory tools registered with agent
- [ ] **Tool discovery:** Tools visible in `co status --tools`
- [ ] **Prompt assembly:** Knowledge injection integrated into system prompt

---

## Risk Assessment

### Low Risk

**Manual editing workflow**
- Risk: Users might break YAML syntax
- Mitigation: Validation at load time, graceful error handling, clear examples

**File system paths**
- Risk: Path construction errors on different OS
- Mitigation: Use `pathlib.Path` everywhere, test on macOS/Linux

### Medium Risk

**Size budget enforcement**
- Risk: Truncation might break markdown structure
- Mitigation: Truncate at byte boundary with UTF-8 error handling, warn users proactively

**Grep-based search performance**
- Risk: Slow with many memories
- Mitigation: Phase 1c targets <200 memories, clear upgrade path to FTS5

**Precedence conflicts**
- Risk: Project/global conflict resolution unclear
- Mitigation: Document precedence rules clearly, last-write-wins on sections

### Deferred Risks

**Auto-learning** (Phase 2+)
- Risk: Silent inference without user approval
- Mitigation: Explicitly deferred, requires opt-in design

**Vector search** (Phase 3)
- Risk: Embedding generation cost/latency
- Mitigation: Only needed at scale, clear thresholds defined

---

## Future Enhancements

### Phase 2: SQLite FTS5 Index (when >200 memories)

**Motivation:** Grep becomes slow with hundreds of memory files.

**Implementation:**
```sql
CREATE VIRTUAL TABLE memories USING fts5(
    id UNINDEXED,
    content,
    tags,
    created UNINDEXED,
    source_path UNINDEXED
);
```

**Index build:** Scan all `.md` files, parse frontmatter, insert to FTS5

**Query:** BM25 keyword search via `SELECT ... FROM memories WHERE memories MATCH ?`

**Files remain source of truth:** SQLite is derived, rebuildable with `co knowledge --reindex`

**Effort:** 4-6 hours (schema + indexing + query + tests)

### Phase 3: Hybrid Search with sqlite-vec (when >500 docs)

**Motivation:** Keyword search misses semantic matches (e.g., "concurrency" vs "parallelism").

**Implementation:**
```sql
CREATE VIRTUAL TABLE memory_vectors USING vec0(
    id TEXT PRIMARY KEY,
    embedding FLOAT[384]
);
```

**Embeddings:** Local model via Ollama (sentence-transformers) or small API call

**Retrieval:** Reciprocal Rank Fusion (RRF) combining FTS5 + vector scores

**Effort:** 6-8 hours (embedding generation + vector index + hybrid query + evaluation)

### Phase 4: Web Knowledge Internalization

**Motivation:** Agent fetches useful web content, should save for reuse.

**Flow:**
```
Agent calls web_fetch(url)
  → Gets markdown content
  → Judges relevance
  → If relevant: save_knowledge(content, source=url, tags=[...])
  → Saved to .co-cli/knowledge/articles/{slug}.md
```

**Storage pattern:**
```
.co-cli/knowledge/
├── articles/           # Web-fetched knowledge
│   └── python-asyncio-patterns.md
└── attachments/        # Binary originals (PDFs, images)
    └── api-diagram.png
```

**Quality gate:** Agent decides what to keep, not automatic for every fetch

**Effort:** 3-4 hours (save_knowledge tool + article storage + tests)

### Phase 5: Auto-Learning with Opt-In

**Motivation:** Reduce user burden of explicitly saving every preference.

**Design:**
- `co learn --auto` enables auto-learning mode
- Agent silently records observations during session
- At session end, shows draft memories for approval
- User approves/rejects/edits before saving

**Safeguards:**
- Always requires final approval
- Clear indicator when in auto-learn mode
- Easy to disable: `co learn --auto=false`

**Effort:** 8-10 hours (observation logic + approval UX + draft memory + tests)

### Phase 6: Memory Summarization

**Motivation:** Many small memories become redundant over time.

**Flow:**
```
co knowledge --summarize
  → Agent reads all memories
  → Groups related memories
  → Generates concise summary
  → Proposes merged memory + deletion of originals
  → User approves changes
```

**LLM task:** Summarization with citation to source memories

**Effort:** 5-6 hours (summarization prompt + merge logic + approval UX + tests)

### Phase 7: Multi-Project Knowledge Graph

**Motivation:** Learn patterns across projects (e.g., "always use async" applies to all Python projects).

**Design:**
- Memory tags include `scope: [project, language, global]`
- Cross-project memories saved to global knowledge
- Query-time expansion: "python" tag matches all Python projects

**Schema:**
```markdown
---
id: 42
scope: language
language: python
tags: [style, concurrency]
---

In Python projects, prefer async/await over callbacks for concurrency.
```

**Effort:** 6-8 hours (scope taxonomy + cross-project query + UI + tests)

---

## Appendix: Format Examples

### Global Context Example

**File:** `~/.config/co-cli/knowledge/context.md`

```markdown
---
version: 1
updated: 2026-02-09T14:30:00Z
---

# User

- Name: Bin Le
- Timezone: America/Los_Angeles
- Communication: Prefers concise explanations with reasoning shown
- Work schedule: Weekdays 9am-5pm PST

# Preferences

- Coding style: Async/await over callbacks
- Testing: Functional tests only, no mocks
- Error handling: Explicit error types, no silent failures
- Documentation: Code comments for "why", not "what"
```

### Project Context Example

**File:** `.co-cli/knowledge/context.md`

```markdown
---
version: 1
updated: 2026-02-09T15:00:00Z
---

# Project

- Name: co-cli
- Type: Python CLI tool (typer + pydantic-ai)
- Python version: 3.12+
- Package manager: uv

# Architecture

- Agent pattern: RunContext[CoDeps] for all tools
- Tool approval: Side-effectful tools use `requires_approval=True`
- Config: JSON files in `.co-cli/` and `~/.config/co-cli/`
- Testing: Functional only, no mocks, Docker required for shell tests

# Learned

- Always run `uv sync` before `pytest`
- This project uses SQLAlchemy ORM exclusively
- Tool return format: `dict[str, Any]` with `display` field
- Imports: Always explicit, never `from X import *`
```

### Memory Example

**File:** `.co-cli/knowledge/memories/001-prefers-async.md`

```markdown
---
id: 1
created: 2026-02-09T14:30:00Z
tags: [python, style, concurrency]
source: user-told
---

User prefers async/await over callbacks. When generating Python code
that involves concurrent operations, always use asyncio patterns
rather than callback-based approaches.

Example preference:
```python
# Preferred
async def fetch_data():
    result = await api_call()
    return result

# Avoid
def fetch_data(callback):
    api_call().then(callback)
```

This applies to all Python projects unless project-specific context
overrides this preference.
```

---

## Appendix: Peer Alignment Evidence

### Format Convergence

| System | Project Context File | Format | Auto-load? |
|--------|---------------------|--------|------------|
| Claude Code | `CLAUDE.md` | Markdown | Yes |
| Codex | `AGENTS.md` | Markdown | Yes |
| Gemini CLI | `GEMINI.md` | Markdown | Yes |
| Aider | `.aider.conf.yml` | YAML + Markdown | Yes |
| **Co (Phase 1c)** | `.co-cli/knowledge/context.md` | **Markdown + YAML frontmatter** | **Yes** |

**Conclusion:** 4/4 peers use Markdown. Co aligns.

### Hierarchy Convergence

| System | Global | Project | Precedence |
|--------|--------|---------|------------|
| Claude Code | `~/.config/claude-code/CLAUDE.md` | `./CLAUDE.md` | Project > Global |
| Codex | `~/.codex/context` | Git tree walk | Subdirectory > Root |
| Gemini CLI | `~/.config/gemini-cli/GEMINI.md` | 3-tier BFS | Closest > Parent |
| **Co (Phase 1c)** | **`~/.config/co-cli/knowledge/context.md`** | **`.co-cli/knowledge/context.md`** | **Project > Global** |

**Conclusion:** 3/4 peers use hierarchical discovery. Co aligns with 2-tier model.

### Memory Storage Convergence

| System | Memory Format | Storage | Retrieval |
|--------|---------------|---------|-----------|
| Claude Code | Frontmatter in agent .md files | Inline | Parse frontmatter |
| Codex | Summaries | SQLite | SQL query |
| Gemini CLI | Markdown entries | Single file append | grep |
| Aider | Chat history | Markdown files | Load on restore |
| **Co (Phase 1c)** | **Markdown + YAML frontmatter** | **Separate .md files** | **grep + frontmatter** |

**Conclusion:** Markdown is universal. Co's approach (atomic files) combines Claude Code's frontmatter with Codex's structured storage.

### Retrieval Pattern Convergence

| System | Initial Retrieval | Scaled Retrieval |
|--------|------------------|------------------|
| Basic Memory | grep | SQLite FTS5 |
| Khoj | - | pgvector |
| Cursor | ripgrep | Hybrid (ripgrep + vector + AI rerank) |
| sqlite-vec demos | - | FTS5 + vectors (RRF) |
| **Co Roadmap** | **grep (Phase 1c)** | **FTS5 (Phase 2) → Hybrid (Phase 3)** |

**Conclusion:** grep → FTS5 → hybrid is the proven evolution path. Co aligns.

---

**END OF PHASE 1C IMPLEMENTATION GUIDE**
