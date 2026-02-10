# Prompt System Refactor - Phase 1c Implementation Guide

## Executive Summary

**Goal:** Enable internal knowledge loading and memory tool integration for persistent context and learned facts.

**Problem:** Co currently has no persistent memory across sessions. It cannot remember user preferences, project insights, or learned facts. Each session starts fresh with no context about past interactions or accumulated knowledge.

**Solution:** Add internal knowledge system with `.co-cli/internal/context.json` for auto-loaded persistent context and memory tools (`save_memory`, `recall_memory`, `list_memories`) for explicit knowledge management.

**Scope:** Phase 1c focuses ONLY on internal knowledge infrastructure and memory tools. Advanced memory features (summarization, automatic learning) are future phases.

**Effort:** 8-10 hours (schema design + loading + memory tools + testing + verification)

**Risk:** Low-Medium (new feature, no existing behavior to break, comprehensive validation)

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

**Current limitations:**
- User must re-explain project context every session
- Co cannot learn from corrections or feedback
- No persistent understanding of user preferences
- Cannot accumulate knowledge about codebase patterns
- Each session repeats the same discovery process

**Desired outcome (Phase 1c):**
- Internal knowledge auto-loaded at session start (user facts, project insights, learned patterns)
- Memory tools for explicit knowledge management (save, recall, list)
- Size-controlled context (10KB target, 20KB hard limit)
- File-based storage for transparency and git-ability
- Foundation for future automatic learning features

### The "Finch" Vision - Internal Knowledge Pillar

**"Internal Knowledge" = Co's learned context (distinct from External Knowledge = tools)**

Internal knowledge represents Co's accumulated understanding that should always be available:
- **User facts:** Name, timezone, preferences, working style
- **Project insights:** Architecture patterns, team conventions, discovered relationships
- **Learned facts:** Corrections, clarifications, accumulated wisdom from past sessions

**Internal vs External boundary:**
- **Internal:** Always in context, auto-loaded, small budget (10-20KB)
- **External:** Queried on demand via tools (web_search, obsidian, google, etc.)

**Why this matters:**
- Reduces repetitive explanations
- Builds relationship over time (Co "knows" you)
- Enables adaptive behavior based on learned preferences
- Foundation for "Finch"-like companion experience

### Current State

**What exists:**
- ✅ Agent memory system (pydantic-ai) for in-session state
- ✅ File-based config (`.co-cli/settings.json`)
- ✅ Tool system for external data access
- ✅ Prompt assembly pipeline (Phase 1a + 1b)

**What's missing (Phase 1c scope):**
- ❌ Internal knowledge schema (`context.json` format)
- ❌ Knowledge loading function (`load_internal_knowledge()`)
- ❌ Memory tools (`save_memory`, `recall_memory`, `list_memories`)
- ❌ Integration with prompt assembly (inject after personality)
- ❌ Size validation and warning system
- ❌ Tests for knowledge loading and memory tools

**What's deferred (future phases):**
- ⏳ Automatic learning from conversations (Phase 2)
- ⏳ Memory summarization and compression (Phase 2)
- ⏳ Multi-session memory management (Phase 2)
- ⏳ Memory search and retrieval (Phase 2)

---

## Architecture Overview

### Current Flow (Phase 1b)

```
User ──▶ CLI ──▶ get_agent() ──▶ get_system_prompt(provider, personality) ──▶ Agent
                                        │
                                        ▼
                            ┌───────────┴────────────┐
                            ▼                        ▼
                      system.md              personalities/
                   (with conditionals)        {name}.md
                            │                        │
                            ▼                        │
                    Process conditionals             │
                            │                        │
                            ▼                        │
                    Inject personality ──────────────┘
                            │
                            ▼
                    Project instructions
                   (.co-cli/instructions.md)
                            │
                            ▼
                      Assembled prompt
```

### New Flow (Phase 1c)

```
User ──▶ CLI ──▶ get_agent() ──▶ get_system_prompt(provider, personality) ──▶ Agent
                      │                      │                                    │
                      │                      ▼                                    │
                      │          ┌───────────┴────────────┐                       │
                      │          ▼                        ▼                       │
                      │    system.md              personalities/                 │
                      │ (with conditionals)        {name}.md                     │
                      │          │                        │                       │
                      │          ▼                        │                       │
                      │  Process conditionals             │                       │
                      │          │                        │                       │
                      │          ▼                        │                       │
                      │  Inject personality ──────────────┘                       │
                      │          │                                                │
                      │          ▼                                                │
                      │  Load internal knowledge ◀── NEW                          │
                      │   (.co-cli/internal/context.json)                        │
                      │          │                                                │
                      │          ▼                                                │
                      │  Project instructions                                     │
                      │ (.co-cli/instructions.md)                                │
                      │          │                                                │
                      │          ▼                                                │
                      │    Assembled prompt                                       │
                      │                                                           │
                      └──▶ Register memory tools ◀── NEW                          │
                           (save_memory, recall_memory, list_memories)           │
                                        │                                         │
                                        └─────────────────────────────────────────┘
```

### Internal Knowledge Lifecycle

```
Session Start:
  1. load_internal_knowledge() reads .co-cli/internal/context.json
  2. Validate size (<= 20KB, warn if > 10KB)
  3. Format as markdown section
  4. Inject into prompt (after personality, before project instructions)
  5. Agent starts with full context

During Session:
  User: "I prefer async/await over callbacks"
  Co calls: save_memory("user_preferences", "Prefers async/await over callbacks")

  Later...
  User: "How should I structure this async function?"
  Co recalls: User prefers async/await (from internal knowledge + session memory)
  Co responds: Using async/await based on your preference...

Session End:
  Memory persists in .co-cli/internal/context.json
  Next session auto-loads the same context
```

### Memory Storage Architecture

```
.co-cli/
├── settings.json                    # Configuration (Phase 1a)
├── instructions.md                  # Project conventions (Phase 1a)
├── internal/                        # Internal knowledge (Phase 1c - NEW)
│   └── context.json                 # Auto-loaded persistent context
└── memories/                        # Memory storage (Phase 1c - NEW)
    ├── user_preferences.json        # User preference memories
    ├── project_insights.json        # Project insight memories
    └── learned_facts.json           # Learned fact memories
```

**Storage rationale:**
- `internal/context.json` - Single source of truth, auto-loaded every session
- `memories/*.json` - Granular storage for memory tools, merged into context.json
- Separate directories for clear boundaries (internal vs config vs instructions)
- JSON format for structure validation and programmatic access

---

## Implementation Plan

### Phase 1: Internal Knowledge Schema

**Goal:** Define `context.json` structure with version, sections, and size limits.

**File:** `co_cli/internal_knowledge.py` (NEW)

**Tasks:**
- [ ] Create `co_cli/internal_knowledge.py` module
- [ ] Define `InternalKnowledge` Pydantic model
- [ ] Define `UserContext`, `ProjectContext`, `LearnedFacts` sub-models
- [ ] Add version field (start with "1.0")
- [ ] Add timestamp tracking (created, updated)
- [ ] Add size validation (10KB warn, 20KB error)
- [ ] Add serialization helpers (to_dict, from_dict, to_markdown)

**Estimated time:** 2 hours

---

### Phase 2: Knowledge Loading Function

**Goal:** Load and validate internal knowledge from `.co-cli/internal/context.json`.

**File:** `co_cli/internal_knowledge.py` (continued)

**Tasks:**
- [ ] Add `load_internal_knowledge() -> str | None` function
- [ ] Check if `.co-cli/internal/context.json` exists
- [ ] Load and parse JSON (handle malformed files gracefully)
- [ ] Validate against schema (version, required fields)
- [ ] Check size constraints (warn if > 10KB, error if > 20KB)
- [ ] Convert to markdown format for prompt injection
- [ ] Return None if file missing (graceful degradation)
- [ ] Log warnings for validation issues

**Estimated time:** 1.5 hours

---

### Phase 3: Memory Tools

**Goal:** Implement `save_memory`, `recall_memory`, `list_memories` tools.

**File:** `co_cli/tools/memory.py` (NEW)

**Tasks:**
- [ ] Create `co_cli/tools/memory.py` module
- [ ] Add `save_memory` tool with `agent.tool()` decorator
  - [ ] Parameters: category (user|project|learned), key, value
  - [ ] Storage: Write to `.co-cli/memories/{category}.json`
  - [ ] Merge into `context.json` after save
  - [ ] Return confirmation with timestamp
- [ ] Add `recall_memory` tool with `agent.tool()` decorator
  - [ ] Parameters: category (optional), key (optional), query (optional)
  - [ ] Search in `context.json` and `memories/*.json`
  - [ ] Return matching memories with metadata
- [ ] Add `list_memories` tool with `agent.tool()` decorator
  - [ ] Parameters: category (optional)
  - [ ] List all memories with counts and summaries
  - [ ] Return formatted display string + metadata
- [ ] Add memory merge logic (memories/*.json → context.json)
- [ ] Add duplicate detection and deduplication

**Estimated time:** 3 hours

---

### Phase 4: Prompt Assembly Integration

**Goal:** Inject internal knowledge into prompt after personality, before project instructions.

**File:** `co_cli/prompts/__init__.py`

**Tasks:**
- [ ] Import `load_internal_knowledge` from `co_cli.internal_knowledge`
- [ ] Update `get_system_prompt()` to call `load_internal_knowledge()`
- [ ] Inject knowledge section after personality, before project instructions
- [ ] Format with clear header: "## Internal Knowledge"
- [ ] Handle None return (skip section if no knowledge)
- [ ] Add size warning logging if knowledge > 10KB
- [ ] Update docstring to document new section

**Estimated time:** 30 minutes

---

### Phase 5: Agent Integration

**Goal:** Register memory tools with agent, ensure internal knowledge loads at startup.

**File:** `co_cli/agent.py`

**Tasks:**
- [ ] Import memory tools from `co_cli.tools.memory`
- [ ] Verify `get_system_prompt()` loads internal knowledge (already integrated via Phase 4)
- [ ] Confirm memory tools are registered (auto-registered via `@agent.tool()`)
- [ ] Test end-to-end flow (load knowledge → chat → save memory → reload)

**Estimated time:** 30 minutes

---

### Phase 6: Testing

**Goal:** Comprehensive test coverage for schema, loading, memory tools, and integration.

**Files:**
- `tests/test_internal_knowledge.py` (NEW)
- `tests/test_memory_tools.py` (NEW)
- `tests/test_prompts.py` (updated)

**Tasks:**
- [ ] Create `tests/test_internal_knowledge.py` (8 tests)
  - [ ] Valid context.json loading
  - [ ] Missing file handling (returns None)
  - [ ] Malformed JSON handling (logs warning, returns None)
  - [ ] Size validation (warn > 10KB, error > 20KB)
  - [ ] Version validation
  - [ ] Markdown formatting
  - [ ] Schema validation (required fields)
  - [ ] Timestamp handling
- [ ] Create `tests/test_memory_tools.py` (7 tests)
  - [ ] save_memory creates file and merges to context
  - [ ] recall_memory finds saved memories
  - [ ] list_memories shows all categories
  - [ ] Duplicate detection
  - [ ] Category validation
  - [ ] Memory persistence across saves
  - [ ] Error handling for invalid inputs
- [ ] Update `tests/test_prompts.py` (3 new tests)
  - [ ] Internal knowledge injection in prompt
  - [ ] Knowledge appears after personality, before project
  - [ ] Prompt assembly with all components (conditionals + personality + knowledge + project)
- [ ] Run full test suite: `uv run pytest`
- [ ] Verify no regressions

**Estimated time:** 2.5 hours

---

## Code Specifications

### Internal Knowledge Schema

**File:** `co_cli/internal_knowledge.py`

```python
"""Internal knowledge management for Co CLI.

Handles loading, validation, and formatting of Co's learned context
from .co-cli/internal/context.json.
"""

from pathlib import Path
from typing import Any
from datetime import datetime
import json
import logging

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# Size constraints
SIZE_TARGET = 10 * 1024  # 10KB - warn if exceeded
SIZE_LIMIT = 20 * 1024   # 20KB - error if exceeded


class UserContext(BaseModel):
    """User-specific context and preferences."""

    name: str | None = None
    timezone: str | None = None
    working_hours: str | None = None
    preferences: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ProjectContext(BaseModel):
    """Project-specific insights and patterns."""

    name: str | None = None
    type: str | None = None
    architecture: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    conventions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class LearnedFacts(BaseModel):
    """Accumulated knowledge from interactions."""

    facts: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    clarifications: list[str] = Field(default_factory=list)


class InternalKnowledge(BaseModel):
    """Co's internal knowledge structure.

    Auto-loaded at session start from .co-cli/internal/context.json.
    Size target: 10KB (warn if exceeded)
    Size limit: 20KB (error if exceeded)
    """

    version: str = "1.0"
    created: datetime = Field(default_factory=datetime.now)
    updated: datetime = Field(default_factory=datetime.now)

    user: UserContext = Field(default_factory=UserContext)
    project: ProjectContext = Field(default_factory=ProjectContext)
    learned_facts: LearnedFacts = Field(default_factory=LearnedFacts)

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        """Validate version format."""
        if v != "1.0":
            raise ValueError(f"Unsupported version: {v}. Expected: 1.0")
        return v

    def to_markdown(self) -> str:
        """Convert internal knowledge to markdown format for prompt injection.

        Returns:
            Markdown-formatted string with all internal knowledge sections.
        """
        lines = []

        # User context
        if self.user.name or self.user.preferences or self.user.notes:
            lines.append("### User Context")
            if self.user.name:
                lines.append(f"- **Name:** {self.user.name}")
            if self.user.timezone:
                lines.append(f"- **Timezone:** {self.user.timezone}")
            if self.user.working_hours:
                lines.append(f"- **Working Hours:** {self.user.working_hours}")

            if self.user.preferences:
                lines.append("\n**Preferences:**")
                for key, value in self.user.preferences.items():
                    lines.append(f"- {key}: {value}")

            if self.user.notes:
                lines.append("\n**Notes:**")
                for note in self.user.notes:
                    lines.append(f"- {note}")

            lines.append("")  # Blank line

        # Project context
        if self.project.name or self.project.patterns or self.project.notes:
            lines.append("### Project Context")
            if self.project.name:
                lines.append(f"- **Name:** {self.project.name}")
            if self.project.type:
                lines.append(f"- **Type:** {self.project.type}")

            if self.project.architecture:
                lines.append("\n**Architecture:**")
                for item in self.project.architecture:
                    lines.append(f"- {item}")

            if self.project.patterns:
                lines.append("\n**Patterns:**")
                for pattern in self.project.patterns:
                    lines.append(f"- {pattern}")

            if self.project.conventions:
                lines.append("\n**Conventions:**")
                for conv in self.project.conventions:
                    lines.append(f"- {conv}")

            if self.project.notes:
                lines.append("\n**Notes:**")
                for note in self.project.notes:
                    lines.append(f"- {note}")

            lines.append("")  # Blank line

        # Learned facts
        if self.learned_facts.facts or self.learned_facts.corrections:
            lines.append("### Learned Facts")

            if self.learned_facts.facts:
                lines.append("\n**Facts:**")
                for fact in self.learned_facts.facts:
                    lines.append(f"- {fact}")

            if self.learned_facts.corrections:
                lines.append("\n**Corrections:**")
                for correction in self.learned_facts.corrections:
                    lines.append(f"- {correction}")

            if self.learned_facts.clarifications:
                lines.append("\n**Clarifications:**")
                for clarification in self.learned_facts.clarifications:
                    lines.append(f"- {clarification}")

        return "\n".join(lines) if lines else ""

    def size_bytes(self) -> int:
        """Calculate size of internal knowledge in bytes.

        Returns:
            Size in bytes of JSON-serialized knowledge.
        """
        return len(json.dumps(self.model_dump(), indent=2).encode("utf-8"))


def load_internal_knowledge() -> str | None:
    """Load internal knowledge from .co-cli/internal/context.json.

    Processing steps:
    1. Check if .co-cli/internal/context.json exists
    2. Load and parse JSON
    3. Validate schema (version, structure)
    4. Check size constraints (warn > 10KB, error > 20KB)
    5. Convert to markdown format
    6. Return formatted string or None if missing

    Returns:
        Markdown-formatted internal knowledge, or None if file missing.

    Raises:
        ValueError: If knowledge exceeds 20KB size limit.

    Example:
        >>> knowledge = load_internal_knowledge()
        >>> if knowledge:
        ...     print(f"Loaded {len(knowledge)} bytes of internal knowledge")
    """
    # 1. Check if file exists
    knowledge_file = Path.cwd() / ".co-cli" / "internal" / "context.json"

    if not knowledge_file.exists():
        logger.debug("No internal knowledge file found (this is OK)")
        return None

    # 2. Load and parse JSON
    try:
        content = knowledge_file.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Malformed internal knowledge JSON: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to load internal knowledge: {e}")
        return None

    # 3. Validate schema
    try:
        knowledge = InternalKnowledge(**data)
    except Exception as e:
        logger.warning(f"Invalid internal knowledge schema: {e}")
        return None

    # 4. Check size constraints
    size = knowledge.size_bytes()

    if size > SIZE_LIMIT:
        raise ValueError(
            f"Internal knowledge exceeds size limit: {size} bytes > {SIZE_LIMIT} bytes. "
            f"Please reduce content size."
        )

    if size > SIZE_TARGET:
        logger.warning(
            f"Internal knowledge exceeds recommended size: {size} bytes > {SIZE_TARGET} bytes. "
            f"Consider summarizing or moving content to project instructions."
        )

    # 5. Convert to markdown
    markdown = knowledge.to_markdown()

    if not markdown.strip():
        logger.debug("Internal knowledge file exists but is empty")
        return None

    return markdown


def save_internal_knowledge(knowledge: InternalKnowledge, path: Path | None = None) -> None:
    """Save internal knowledge to .co-cli/internal/context.json.

    Args:
        knowledge: InternalKnowledge object to save.
        path: Optional custom path (default: .co-cli/internal/context.json).

    Raises:
        ValueError: If knowledge exceeds size limit.
    """
    # Validate size
    size = knowledge.size_bytes()
    if size > SIZE_LIMIT:
        raise ValueError(
            f"Cannot save: internal knowledge exceeds size limit ({size} > {SIZE_LIMIT} bytes)"
        )

    # Update timestamp
    knowledge.updated = datetime.now()

    # Determine path
    if path is None:
        path = Path.cwd() / ".co-cli" / "internal" / "context.json"

    # Ensure directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write file
    content = json.dumps(knowledge.model_dump(), indent=2, default=str)
    path.write_text(content, encoding="utf-8")

    logger.info(f"Saved internal knowledge ({size} bytes) to {path}")
```

**Key design decisions:**
- **Pydantic models** for schema validation and serialization
- **Size constraints** enforced (10KB target, 20KB limit)
- **Graceful degradation** (missing file returns None, not error)
- **Markdown formatting** for prompt injection
- **Timestamp tracking** (created, updated)
- **Version field** for future schema evolution

---

### Boundary Definition Table

**What goes in Internal Knowledge vs External Knowledge:**

| Category | Internal Knowledge (.co-cli/internal/context.json) | External Knowledge (Tools) |
|----------|-----------------------------------------------------|---------------------------|
| **User Info** | Name, timezone, preferences, working style | Calendar events, email threads, detailed history |
| **Project Context** | Architecture patterns, key conventions, discovered relationships | Full codebase, file contents, git history |
| **Learned Facts** | Corrections, clarifications, accumulated wisdom | Documentation, web search results, Slack threads |
| **Size** | 10-20KB (always in context) | Unlimited (queried on demand) |
| **Access** | Auto-loaded every session | Tool calls (explicit retrieval) |
| **Update Frequency** | Accumulates over time (append-mostly) | Real-time (always current) |
| **Examples** | "User prefers async/await", "Project uses FastAPI", "Tests go in tests/ directory" | "Latest commit message", "Current API docs", "Recent Slack discussion" |

**Boundary principle:** If it fits in 20KB and should ALWAYS be available, it's Internal. If it's large, dynamic, or context-dependent, it's External.

---

### Memory Tools Specification

**File:** `co_cli/tools/memory.py`

```python
"""Memory management tools for Co CLI.

Provides save_memory, recall_memory, and list_memories tools for
explicit knowledge management and persistence.
"""

from pathlib import Path
from datetime import datetime
import json
import logging
from typing import Any

from pydantic_ai import RunContext

from co_cli.agent import CoDeps, agent
from co_cli.internal_knowledge import (
    load_internal_knowledge,
    InternalKnowledge,
    save_internal_knowledge,
)

logger = logging.getLogger(__name__)


@agent.tool()
async def save_memory(
    ctx: RunContext[CoDeps],
    category: str,
    key: str,
    value: str,
) -> dict[str, Any]:
    """Save a memory for future sessions.

    Stores knowledge in .co-cli/memories/{category}.json and merges
    into .co-cli/internal/context.json for auto-loading.

    Args:
        category: Memory category (user_preferences, project_insights, learned_facts)
        key: Memory identifier (e.g., "coding_style", "database_schema")
        value: Memory content to save

    Returns:
        Dict with:
        - display: Human-readable confirmation message
        - category: Category name
        - key: Memory key
        - timestamp: Save timestamp

    Example:
        User: "I prefer async/await over callbacks"
        save_memory("user_preferences", "async_style", "Prefers async/await over callbacks")
    """
    # Validate category
    valid_categories = ["user_preferences", "project_insights", "learned_facts"]
    if category not in valid_categories:
        return {
            "display": f"Error: Invalid category '{category}'. "
                      f"Must be one of: {', '.join(valid_categories)}",
            "error": True,
        }

    # Memory file path
    memory_file = Path.cwd() / ".co-cli" / "memories" / f"{category}.json"
    memory_file.parent.mkdir(parents=True, exist_ok=True)

    # Load existing memories
    if memory_file.exists():
        try:
            memories = json.loads(memory_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning(f"Malformed memory file: {memory_file}. Starting fresh.")
            memories = {}
    else:
        memories = {}

    # Save memory with timestamp
    timestamp = datetime.now().isoformat()
    memories[key] = {
        "value": value,
        "timestamp": timestamp,
    }

    # Write memory file
    memory_file.write_text(
        json.dumps(memories, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    # Merge into context.json
    await _merge_memories_to_context()

    logger.info(f"Saved memory: {category}/{key}")

    return {
        "display": f"Saved memory to {category}: {key}\n"
                  f"Value: {value}\n"
                  f"Timestamp: {timestamp}",
        "category": category,
        "key": key,
        "timestamp": timestamp,
    }


@agent.tool()
async def recall_memory(
    ctx: RunContext[CoDeps],
    category: str | None = None,
    key: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """Recall saved memories.

    Search internal knowledge and memory files for matching memories.

    Args:
        category: Optional category filter (user_preferences, project_insights, learned_facts)
        key: Optional specific key to retrieve
        query: Optional text search in memory values

    Returns:
        Dict with:
        - display: Formatted list of matching memories
        - memories: List of memory dicts (category, key, value, timestamp)
        - count: Number of matches

    Example:
        recall_memory(category="user_preferences")  # All user preferences
        recall_memory(key="async_style")             # Specific memory
        recall_memory(query="async")                 # Text search
    """
    memories_dir = Path.cwd() / ".co-cli" / "memories"

    if not memories_dir.exists():
        return {
            "display": "No memories saved yet.",
            "memories": [],
            "count": 0,
        }

    # Collect all memories
    all_memories = []

    # Determine categories to search
    if category:
        categories = [category]
    else:
        categories = ["user_preferences", "project_insights", "learned_facts"]

    # Load memories from each category
    for cat in categories:
        memory_file = memories_dir / f"{cat}.json"
        if not memory_file.exists():
            continue

        try:
            memories = json.loads(memory_file.read_text(encoding="utf-8"))
            for mem_key, mem_data in memories.items():
                # Apply filters
                if key and mem_key != key:
                    continue
                if query and query.lower() not in mem_data["value"].lower():
                    continue

                all_memories.append({
                    "category": cat,
                    "key": mem_key,
                    "value": mem_data["value"],
                    "timestamp": mem_data.get("timestamp", "unknown"),
                })
        except json.JSONDecodeError:
            logger.warning(f"Skipping malformed memory file: {memory_file}")
            continue

    # Format display
    if not all_memories:
        display = "No matching memories found."
    else:
        lines = [f"Found {len(all_memories)} memories:\n"]
        for mem in all_memories:
            lines.append(f"**{mem['category']}/{mem['key']}**")
            lines.append(f"  Value: {mem['value']}")
            lines.append(f"  Saved: {mem['timestamp']}\n")
        display = "\n".join(lines)

    return {
        "display": display,
        "memories": all_memories,
        "count": len(all_memories),
    }


@agent.tool()
async def list_memories(
    ctx: RunContext[CoDeps],
    category: str | None = None,
) -> dict[str, Any]:
    """List all saved memories with counts and summaries.

    Args:
        category: Optional category filter (user_preferences, project_insights, learned_facts)

    Returns:
        Dict with:
        - display: Formatted summary of all memories
        - categories: Dict of category -> count
        - total_count: Total number of memories

    Example:
        list_memories()  # All categories
        list_memories(category="user_preferences")  # One category
    """
    memories_dir = Path.cwd() / ".co-cli" / "memories"

    if not memories_dir.exists():
        return {
            "display": "No memories saved yet.",
            "categories": {},
            "total_count": 0,
        }

    # Determine categories
    if category:
        categories = [category]
    else:
        categories = ["user_preferences", "project_insights", "learned_facts"]

    # Count memories per category
    category_counts = {}
    total_count = 0

    for cat in categories:
        memory_file = memories_dir / f"{cat}.json"
        if not memory_file.exists():
            category_counts[cat] = 0
            continue

        try:
            memories = json.loads(memory_file.read_text(encoding="utf-8"))
            count = len(memories)
            category_counts[cat] = count
            total_count += count
        except json.JSONDecodeError:
            logger.warning(f"Skipping malformed memory file: {memory_file}")
            category_counts[cat] = 0

    # Format display
    lines = [f"Memory Summary (Total: {total_count})\n"]
    for cat, count in category_counts.items():
        lines.append(f"- **{cat}**: {count} memories")

    display = "\n".join(lines)

    return {
        "display": display,
        "categories": category_counts,
        "total_count": total_count,
    }


async def _merge_memories_to_context() -> None:
    """Merge memory files into context.json for auto-loading.

    Internal helper function. Reads all .co-cli/memories/*.json files
    and merges content into .co-cli/internal/context.json.
    """
    memories_dir = Path.cwd() / ".co-cli" / "memories"
    context_file = Path.cwd() / ".co-cli" / "internal" / "context.json"

    # Load existing context or create new
    if context_file.exists():
        try:
            data = json.loads(context_file.read_text(encoding="utf-8"))
            knowledge = InternalKnowledge(**data)
        except Exception as e:
            logger.warning(f"Failed to load context.json: {e}. Creating new.")
            knowledge = InternalKnowledge()
    else:
        knowledge = InternalKnowledge()

    # Merge user preferences
    pref_file = memories_dir / "user_preferences.json"
    if pref_file.exists():
        try:
            prefs = json.loads(pref_file.read_text(encoding="utf-8"))
            for key, data in prefs.items():
                knowledge.user.preferences[key] = data["value"]
        except Exception as e:
            logger.warning(f"Failed to merge user preferences: {e}")

    # Merge project insights
    insights_file = memories_dir / "project_insights.json"
    if insights_file.exists():
        try:
            insights = json.loads(insights_file.read_text(encoding="utf-8"))
            # Add to patterns list (deduplicate)
            for key, data in insights.items():
                value = data["value"]
                if value not in knowledge.project.patterns:
                    knowledge.project.patterns.append(value)
        except Exception as e:
            logger.warning(f"Failed to merge project insights: {e}")

    # Merge learned facts
    facts_file = memories_dir / "learned_facts.json"
    if facts_file.exists():
        try:
            facts = json.loads(facts_file.read_text(encoding="utf-8"))
            # Add to facts list (deduplicate)
            for key, data in facts.items():
                value = data["value"]
                if value not in knowledge.learned_facts.facts:
                    knowledge.learned_facts.facts.append(value)
        except Exception as e:
            logger.warning(f"Failed to merge learned facts: {e}")

    # Save merged context
    try:
        save_internal_knowledge(knowledge)
        logger.debug("Merged memories into context.json")
    except ValueError as e:
        logger.error(f"Cannot merge memories: {e}")
```

**Key design decisions:**
- **Three memory categories:** user_preferences, project_insights, learned_facts
- **Granular storage:** Each category in separate JSON file for clarity
- **Auto-merge:** save_memory() automatically merges into context.json
- **Deduplication:** Prevents duplicate entries in lists
- **Timestamp tracking:** Each memory has save timestamp
- **Tool return format:** Dict with `display` (formatted string) + metadata

---

### Prompt Assembly Integration

**File:** `co_cli/prompts/__init__.py`

**Update `get_system_prompt()` function:**

```python
def get_system_prompt(provider: str, personality: str | None = None) -> str:
    """Assemble system prompt with model-specific conditionals, personality,
    internal knowledge, and project overrides.

    Processing steps:
    1. Load base system.md
    2. Process model conditionals ([IF gemini] / [IF ollama])
    3. Inject personality template (if specified)
    4. Load internal knowledge from .co-cli/internal/context.json  # NEW
    5. Append project instructions from .co-cli/instructions.md (if exists)
    6. Validate result (no empty prompt, no unprocessed markers)

    Args:
        provider: LLM provider name ("gemini", "ollama", or unknown)
        personality: Optional personality template name

    Returns:
        Assembled system prompt as string

    Raises:
        FileNotFoundError: If system.md doesn't exist
        ValueError: If assembled prompt is empty or has unprocessed conditionals

    Example:
        >>> prompt = get_system_prompt("gemini", personality="friendly")
        >>> assert "Internal Knowledge" in prompt or True  # May or may not be present
    """
    # ... [existing code for steps 1-3] ...

    # 4. Load internal knowledge (NEW)
    from co_cli.internal_knowledge import load_internal_knowledge

    internal_knowledge = load_internal_knowledge()
    if internal_knowledge:
        base_prompt += "\n\n## Internal Knowledge\n\n"
        base_prompt += internal_knowledge

    # 5. Load project instructions (existing)
    project_instructions = Path.cwd() / ".co-cli" / "instructions.md"
    if project_instructions.exists():
        instructions_content = project_instructions.read_text(encoding="utf-8")
        base_prompt += "\n\n## Project-Specific Instructions\n\n"
        base_prompt += instructions_content

    # ... [existing validation code] ...

    return base_prompt
```

**Prompt assembly order (final):**
```
1. Base system.md (identity, principles, tool guidance)
2. Model conditionals ([IF gemini] / [IF ollama])          ← Phase 1a ✅
3. Personality template                                     ← Phase 1b ✅
4. Internal knowledge (.co-cli/internal/context.json)      ← Phase 1c (NEW)
5. Project instructions (.co-cli/instructions.md)          ← Phase 1a ✅
```

---

## Test Specifications

### Test Structure

```
tests/
├── test_internal_knowledge.py (NEW) - Schema, loading, validation (8 tests)
├── test_memory_tools.py (NEW) - Memory tools functionality (7 tests)
└── test_prompts.py (UPDATED) - Prompt assembly with knowledge (3 new tests)
```

### Test File: `tests/test_internal_knowledge.py`

**Complete test suite:**

```python
"""Tests for internal knowledge management."""

import json
from pathlib import Path

import pytest

from co_cli.internal_knowledge import (
    InternalKnowledge,
    UserContext,
    ProjectContext,
    LearnedFacts,
    load_internal_knowledge,
    save_internal_knowledge,
    SIZE_TARGET,
    SIZE_LIMIT,
)


class TestInternalKnowledgeSchema:
    """Test internal knowledge schema and validation."""

    def test_empty_knowledge_valid(self):
        """Empty knowledge object is valid."""
        knowledge = InternalKnowledge()
        assert knowledge.version == "1.0"
        assert knowledge.user.name is None
        assert knowledge.project.name is None
        assert knowledge.learned_facts.facts == []

    def test_full_knowledge_valid(self):
        """Fully populated knowledge object is valid."""
        knowledge = InternalKnowledge(
            user=UserContext(
                name="Alex",
                timezone="America/Los_Angeles",
                preferences={"coding_style": "async/await"},
            ),
            project=ProjectContext(
                name="co-cli",
                type="python_cli",
                patterns=["Uses pydantic-ai", "Docker for sandboxing"],
            ),
            learned_facts=LearnedFacts(
                facts=["User prefers detailed explanations"],
                corrections=["Database is PostgreSQL, not MySQL"],
            ),
        )
        assert knowledge.user.name == "Alex"
        assert knowledge.project.name == "co-cli"
        assert len(knowledge.learned_facts.facts) == 1

    def test_invalid_version_raises_error(self):
        """Invalid version raises ValidationError."""
        with pytest.raises(ValueError, match="Unsupported version"):
            InternalKnowledge(version="2.0")

    def test_markdown_formatting_empty(self):
        """Empty knowledge produces empty markdown."""
        knowledge = InternalKnowledge()
        markdown = knowledge.to_markdown()
        assert markdown == ""

    def test_markdown_formatting_full(self):
        """Full knowledge produces formatted markdown."""
        knowledge = InternalKnowledge(
            user=UserContext(
                name="Alex",
                preferences={"style": "async/await"},
            ),
            project=ProjectContext(
                name="co-cli",
                patterns=["Uses pydantic-ai"],
            ),
            learned_facts=LearnedFacts(
                facts=["Prefers detailed explanations"],
            ),
        )
        markdown = knowledge.to_markdown()

        assert "### User Context" in markdown
        assert "Alex" in markdown
        assert "async/await" in markdown
        assert "### Project Context" in markdown
        assert "co-cli" in markdown
        assert "pydantic-ai" in markdown
        assert "### Learned Facts" in markdown
        assert "detailed explanations" in markdown

    def test_size_calculation(self):
        """Size calculation returns accurate byte count."""
        knowledge = InternalKnowledge(
            user=UserContext(name="Test"),
        )
        size = knowledge.size_bytes()
        assert size > 0
        assert isinstance(size, int)


class TestLoadInternalKnowledge:
    """Test loading internal knowledge from file."""

    def test_load_missing_file_returns_none(self, tmp_path, monkeypatch):
        """Missing context.json returns None gracefully."""
        monkeypatch.chdir(tmp_path)
        result = load_internal_knowledge()
        assert result is None

    def test_load_valid_file_returns_markdown(self, tmp_path, monkeypatch):
        """Valid context.json loads and returns markdown."""
        monkeypatch.chdir(tmp_path)

        # Create valid context file
        context_dir = tmp_path / ".co-cli" / "internal"
        context_dir.mkdir(parents=True)
        context_file = context_dir / "context.json"

        data = {
            "version": "1.0",
            "user": {
                "name": "Test User",
                "preferences": {"style": "async"},
            },
            "project": {},
            "learned_facts": {"facts": ["Test fact"]},
        }
        context_file.write_text(json.dumps(data), encoding="utf-8")

        result = load_internal_knowledge()
        assert result is not None
        assert "Test User" in result
        assert "async" in result
        assert "Test fact" in result

    def test_load_malformed_json_returns_none(self, tmp_path, monkeypatch):
        """Malformed JSON returns None with warning."""
        monkeypatch.chdir(tmp_path)

        context_dir = tmp_path / ".co-cli" / "internal"
        context_dir.mkdir(parents=True)
        context_file = context_dir / "context.json"
        context_file.write_text("{ invalid json }", encoding="utf-8")

        result = load_internal_knowledge()
        assert result is None

    def test_load_invalid_schema_returns_none(self, tmp_path, monkeypatch):
        """Invalid schema returns None with warning."""
        monkeypatch.chdir(tmp_path)

        context_dir = tmp_path / ".co-cli" / "internal"
        context_dir.mkdir(parents=True)
        context_file = context_dir / "context.json"

        # Invalid: missing required fields, wrong version
        data = {"version": "99.0", "invalid_field": "test"}
        context_file.write_text(json.dumps(data), encoding="utf-8")

        result = load_internal_knowledge()
        assert result is None

    def test_load_empty_knowledge_returns_none(self, tmp_path, monkeypatch):
        """Empty but valid knowledge returns None."""
        monkeypatch.chdir(tmp_path)

        context_dir = tmp_path / ".co-cli" / "internal"
        context_dir.mkdir(parents=True)
        context_file = context_dir / "context.json"

        # Valid structure but no content
        data = {
            "version": "1.0",
            "user": {},
            "project": {},
            "learned_facts": {},
        }
        context_file.write_text(json.dumps(data), encoding="utf-8")

        result = load_internal_knowledge()
        assert result is None

    def test_load_warns_on_size_target_exceeded(self, tmp_path, monkeypatch):
        """Loading knowledge > 10KB logs warning."""
        monkeypatch.chdir(tmp_path)

        context_dir = tmp_path / ".co-cli" / "internal"
        context_dir.mkdir(parents=True)
        context_file = context_dir / "context.json"

        # Create knowledge just over 10KB
        large_facts = ["Fact " + "x" * 100 for _ in range(120)]
        data = {
            "version": "1.0",
            "user": {},
            "project": {},
            "learned_facts": {"facts": large_facts},
        }
        context_file.write_text(json.dumps(data), encoding="utf-8")

        # Should load but warn
        result = load_internal_knowledge()
        assert result is not None

    def test_load_raises_on_size_limit_exceeded(self, tmp_path, monkeypatch):
        """Loading knowledge > 20KB raises ValueError."""
        monkeypatch.chdir(tmp_path)

        context_dir = tmp_path / ".co-cli" / "internal"
        context_dir.mkdir(parents=True)
        context_file = context_dir / "context.json"

        # Create knowledge over 20KB
        huge_facts = ["Fact " + "x" * 200 for _ in range(120)]
        data = {
            "version": "1.0",
            "user": {},
            "project": {},
            "learned_facts": {"facts": huge_facts},
        }
        context_file.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(ValueError, match="exceeds size limit"):
            load_internal_knowledge()


class TestSaveInternalKnowledge:
    """Test saving internal knowledge to file."""

    def test_save_creates_file(self, tmp_path, monkeypatch):
        """save_internal_knowledge creates context.json."""
        monkeypatch.chdir(tmp_path)

        knowledge = InternalKnowledge(
            user=UserContext(name="Test User"),
        )
        save_internal_knowledge(knowledge)

        context_file = tmp_path / ".co-cli" / "internal" / "context.json"
        assert context_file.exists()

        data = json.loads(context_file.read_text(encoding="utf-8"))
        assert data["user"]["name"] == "Test User"

    def test_save_raises_on_size_limit(self, tmp_path, monkeypatch):
        """save_internal_knowledge raises error if size > 20KB."""
        monkeypatch.chdir(tmp_path)

        huge_facts = ["Fact " + "x" * 200 for _ in range(120)]
        knowledge = InternalKnowledge(
            learned_facts=LearnedFacts(facts=huge_facts),
        )

        with pytest.raises(ValueError, match="exceeds size limit"):
            save_internal_knowledge(knowledge)
```

**Test count:** 8 tests covering schema, loading, validation, size constraints

---

### Test File: `tests/test_memory_tools.py`

**Complete test suite:**

```python
"""Tests for memory management tools."""

import json
from pathlib import Path

import pytest

from co_cli.tools.memory import save_memory, recall_memory, list_memories
from co_cli.agent import CoDeps
from pydantic_ai import RunContext


class TestSaveMemory:
    """Test save_memory tool."""

    @pytest.mark.asyncio
    async def test_save_memory_creates_file(self, tmp_path, monkeypatch):
        """save_memory creates memory file."""
        monkeypatch.chdir(tmp_path)

        # Create minimal RunContext
        ctx = RunContext(deps=CoDeps())

        result = await save_memory(
            ctx,
            category="user_preferences",
            key="coding_style",
            value="Prefers async/await",
        )

        assert result["category"] == "user_preferences"
        assert result["key"] == "coding_style"
        assert "Saved memory" in result["display"]

        # Check file created
        memory_file = tmp_path / ".co-cli" / "memories" / "user_preferences.json"
        assert memory_file.exists()

        data = json.loads(memory_file.read_text(encoding="utf-8"))
        assert "coding_style" in data
        assert data["coding_style"]["value"] == "Prefers async/await"

    @pytest.mark.asyncio
    async def test_save_memory_invalid_category_returns_error(self, tmp_path, monkeypatch):
        """save_memory with invalid category returns error."""
        monkeypatch.chdir(tmp_path)
        ctx = RunContext(deps=CoDeps())

        result = await save_memory(
            ctx,
            category="invalid_category",
            key="test",
            value="test",
        )

        assert result.get("error") is True
        assert "Invalid category" in result["display"]

    @pytest.mark.asyncio
    async def test_save_memory_merges_to_context(self, tmp_path, monkeypatch):
        """save_memory merges to context.json."""
        monkeypatch.chdir(tmp_path)
        ctx = RunContext(deps=CoDeps())

        await save_memory(
            ctx,
            category="learned_facts",
            key="fact1",
            value="Test fact",
        )

        # Check context.json created and contains fact
        context_file = tmp_path / ".co-cli" / "internal" / "context.json"
        assert context_file.exists()

        data = json.loads(context_file.read_text(encoding="utf-8"))
        assert "Test fact" in data["learned_facts"]["facts"]


class TestRecallMemory:
    """Test recall_memory tool."""

    @pytest.mark.asyncio
    async def test_recall_memory_no_memories_returns_empty(self, tmp_path, monkeypatch):
        """recall_memory with no saved memories returns empty."""
        monkeypatch.chdir(tmp_path)
        ctx = RunContext(deps=CoDeps())

        result = await recall_memory(ctx)

        assert result["count"] == 0
        assert "No memories" in result["display"]

    @pytest.mark.asyncio
    async def test_recall_memory_finds_saved_memory(self, tmp_path, monkeypatch):
        """recall_memory finds previously saved memory."""
        monkeypatch.chdir(tmp_path)
        ctx = RunContext(deps=CoDeps())

        # Save a memory
        await save_memory(
            ctx,
            category="user_preferences",
            key="style",
            value="Async preferred",
        )

        # Recall it
        result = await recall_memory(ctx, category="user_preferences")

        assert result["count"] == 1
        assert result["memories"][0]["key"] == "style"
        assert result["memories"][0]["value"] == "Async preferred"

    @pytest.mark.asyncio
    async def test_recall_memory_filters_by_key(self, tmp_path, monkeypatch):
        """recall_memory filters by specific key."""
        monkeypatch.chdir(tmp_path)
        ctx = RunContext(deps=CoDeps())

        # Save multiple memories
        await save_memory(ctx, "user_preferences", "key1", "Value 1")
        await save_memory(ctx, "user_preferences", "key2", "Value 2")

        # Recall specific key
        result = await recall_memory(ctx, key="key1")

        assert result["count"] == 1
        assert result["memories"][0]["key"] == "key1"

    @pytest.mark.asyncio
    async def test_recall_memory_text_search(self, tmp_path, monkeypatch):
        """recall_memory searches in memory values."""
        monkeypatch.chdir(tmp_path)
        ctx = RunContext(deps=CoDeps())

        await save_memory(ctx, "learned_facts", "fact1", "Python uses async/await")
        await save_memory(ctx, "learned_facts", "fact2", "JavaScript uses promises")

        # Search for "async"
        result = await recall_memory(ctx, query="async")

        assert result["count"] == 1
        assert "async/await" in result["memories"][0]["value"]


class TestListMemories:
    """Test list_memories tool."""

    @pytest.mark.asyncio
    async def test_list_memories_no_memories_returns_empty(self, tmp_path, monkeypatch):
        """list_memories with no saved memories returns empty."""
        monkeypatch.chdir(tmp_path)
        ctx = RunContext(deps=CoDeps())

        result = await list_memories(ctx)

        assert result["total_count"] == 0
        assert "No memories" in result["display"]

    @pytest.mark.asyncio
    async def test_list_memories_counts_all_categories(self, tmp_path, monkeypatch):
        """list_memories counts memories across all categories."""
        monkeypatch.chdir(tmp_path)
        ctx = RunContext(deps=CoDeps())

        # Save memories in different categories
        await save_memory(ctx, "user_preferences", "pref1", "Value 1")
        await save_memory(ctx, "user_preferences", "pref2", "Value 2")
        await save_memory(ctx, "learned_facts", "fact1", "Fact 1")

        result = await list_memories(ctx)

        assert result["total_count"] == 3
        assert result["categories"]["user_preferences"] == 2
        assert result["categories"]["learned_facts"] == 1
```

**Test count:** 7 tests covering save, recall, list, filtering, search

---

### Test File: `tests/test_prompts.py` (Updates)

**Add to existing test file:**

```python
class TestInternalKnowledgeIntegration:
    """Test internal knowledge integration in prompt assembly."""

    def test_prompt_with_internal_knowledge(self, tmp_path, monkeypatch):
        """Prompt includes internal knowledge when present."""
        monkeypatch.chdir(tmp_path)

        # Create internal knowledge
        context_dir = tmp_path / ".co-cli" / "internal"
        context_dir.mkdir(parents=True)
        context_file = context_dir / "context.json"

        data = {
            "version": "1.0",
            "user": {"name": "Test User"},
            "project": {"name": "test-project"},
            "learned_facts": {"facts": ["Test fact"]},
        }
        context_file.write_text(json.dumps(data), encoding="utf-8")

        prompt = get_system_prompt("gemini")

        assert "## Internal Knowledge" in prompt
        assert "Test User" in prompt
        assert "test-project" in prompt
        assert "Test fact" in prompt

    def test_prompt_without_internal_knowledge(self, tmp_path, monkeypatch):
        """Prompt works without internal knowledge."""
        monkeypatch.chdir(tmp_path)

        prompt = get_system_prompt("gemini")

        # Should not have internal knowledge section
        assert "## Internal Knowledge" not in prompt
        # Should still be valid
        assert "You are Co" in prompt

    def test_prompt_assembly_order(self, tmp_path, monkeypatch):
        """Internal knowledge appears after personality, before project instructions."""
        monkeypatch.chdir(tmp_path)

        # Create internal knowledge
        context_dir = tmp_path / ".co-cli" / "internal"
        context_dir.mkdir(parents=True)
        (context_dir / "context.json").write_text(
            json.dumps({
                "version": "1.0",
                "user": {"name": "Test"},
                "project": {},
                "learned_facts": {},
            }),
            encoding="utf-8"
        )

        # Create project instructions
        instructions_dir = tmp_path / ".co-cli"
        instructions_dir.mkdir(exist_ok=True)
        (instructions_dir / "instructions.md").write_text("# Project Rules")

        prompt = get_system_prompt("gemini", personality="professional")

        # Check order
        base_idx = prompt.index("You are Co")
        personality_idx = prompt.index("## Personality")
        knowledge_idx = prompt.index("## Internal Knowledge")
        project_idx = prompt.index("## Project-Specific Instructions")

        assert base_idx < personality_idx < knowledge_idx < project_idx
```

**Test count:** 3 new tests for internal knowledge integration

**Total test count for Phase 1c:** 18 tests (8 + 7 + 3)

---

## Verification Procedures

### Automated Testing

**Step 1: Run internal knowledge tests**
```bash
uv run pytest tests/test_internal_knowledge.py -v
```

**Expected output:**
```
tests/test_internal_knowledge.py::TestInternalKnowledgeSchema::test_empty_knowledge_valid PASSED
tests/test_internal_knowledge.py::TestInternalKnowledgeSchema::test_full_knowledge_valid PASSED
tests/test_internal_knowledge.py::TestInternalKnowledgeSchema::test_invalid_version_raises_error PASSED
tests/test_internal_knowledge.py::TestInternalKnowledgeSchema::test_markdown_formatting_empty PASSED
tests/test_internal_knowledge.py::TestInternalKnowledgeSchema::test_markdown_formatting_full PASSED
tests/test_internal_knowledge.py::TestInternalKnowledgeSchema::test_size_calculation PASSED
tests/test_internal_knowledge.py::TestLoadInternalKnowledge::test_load_missing_file_returns_none PASSED
tests/test_internal_knowledge.py::TestLoadInternalKnowledge::test_load_valid_file_returns_markdown PASSED

=================== 8 passed in 0.5s ===================
```

**Step 2: Run memory tools tests**
```bash
uv run pytest tests/test_memory_tools.py -v
```

**Expected output:**
```
tests/test_memory_tools.py::TestSaveMemory::test_save_memory_creates_file PASSED
tests/test_memory_tools.py::TestSaveMemory::test_save_memory_invalid_category_returns_error PASSED
tests/test_memory_tools.py::TestSaveMemory::test_save_memory_merges_to_context PASSED
tests/test_memory_tools.py::TestRecallMemory::test_recall_memory_no_memories_returns_empty PASSED
tests/test_memory_tools.py::TestRecallMemory::test_recall_memory_finds_saved_memory PASSED
tests/test_memory_tools.py::TestRecallMemory::test_recall_memory_filters_by_key PASSED
tests/test_memory_tools.py::TestRecallMemory::test_recall_memory_text_search PASSED

=================== 7 passed in 1.2s ===================
```

**Step 3: Run full test suite**
```bash
uv run pytest
```

**Check for regressions:**
- All existing tests should still pass
- No new warnings or errors
- Test count increases by ~18

**Step 4: Run with coverage**
```bash
uv run pytest tests/test_internal_knowledge.py tests/test_memory_tools.py \
  --cov=co_cli.internal_knowledge --cov=co_cli.tools.memory \
  --cov-report=term-missing
```

**Expected coverage:**
- `co_cli/internal_knowledge.py`: >90% coverage
- `co_cli/tools/memory.py`: >90% coverage

---

### Manual Verification - Internal Knowledge Loading

**Setup:**
```bash
# Create test internal knowledge
mkdir -p .co-cli/internal
cat > .co-cli/internal/context.json << 'EOF'
{
  "version": "1.0",
  "user": {
    "name": "Alex",
    "timezone": "America/Los_Angeles",
    "preferences": {
      "coding_style": "Prefer async/await over callbacks",
      "verbosity": "Detailed explanations preferred"
    }
  },
  "project": {
    "name": "co-cli",
    "type": "python_cli",
    "patterns": [
      "Uses pydantic-ai for agent framework",
      "Docker for sandboxing",
      "pytest for testing (no mocks)"
    ]
  },
  "learned_facts": {
    "facts": [
      "User prefers explicit imports (no import *)",
      "Database is SQLite for local development"
    ]
  }
}
EOF

# Start chat
uv run co chat
```

**Test 1: Verify knowledge is loaded**
```
User: What do you know about me?

# Expected response should mention:
# - Name: Alex
# - Timezone: America/Los_Angeles
# - Prefers async/await
# - Detailed explanations
```

**Test 2: Verify knowledge affects behavior**
```
User: How should I structure this async function?

# Expected response should:
# ✓ Use async/await (from preferences)
# ✓ Include detailed explanation (from preferences)
```

**Test 3: Verify project knowledge**
```
User: What testing framework does this project use?

# Expected response should mention:
# - pytest
# - No mocks policy
```

**Checklist:**
- [ ] Internal knowledge loaded at session start
- [ ] User preferences visible in responses
- [ ] Project context used in recommendations
- [ ] Learned facts influence behavior
- [ ] No error if context.json missing

---

### Manual Verification - Memory Tools

**Test 1: save_memory tool**
```
User: I prefer snake_case for variable names

# Co should call save_memory
# Check response mentions "Saved memory"

# Verify file created
cat .co-cli/memories/user_preferences.json
# Should contain snake_case preference
```

**Test 2: recall_memory tool**
```
User: What are my coding preferences?

# Co should call recall_memory
# Should list saved preferences including snake_case
```

**Test 3: list_memories tool**
```
User: List all my saved memories

# Co should call list_memories
# Should show count per category
```

**Test 4: Memory persistence**
```bash
# Exit chat
# Restart chat
uv run co chat

# Ask about preferences
User: What variable naming style do I prefer?

# Should recall snake_case from previous session
```

**Checklist:**
- [ ] save_memory creates .co-cli/memories/*.json
- [ ] recall_memory finds saved memories
- [ ] list_memories shows counts
- [ ] Memories persist across sessions
- [ ] Memories merge into context.json

---

### Manual Verification - Size Limits

**Test 1: Size warning**
```bash
# Create large context (~12KB, above 10KB target)
cat > .co-cli/internal/context.json << 'EOF'
{
  "version": "1.0",
  "user": {},
  "project": {},
  "learned_facts": {
    "facts": [
      "Fact 1 with lots of text...",
      "Fact 2 with lots of text...",
      ... (many facts totaling ~12KB)
    ]
  }
}
EOF

# Start chat
uv run co chat

# Check logs for warning
# Should see: "exceeds recommended size"
```

**Test 2: Size error**
```bash
# Create huge context (~25KB, above 20KB limit)
# Should raise error on load
# Agent should refuse to start
```

**Checklist:**
- [ ] Warning logged when size > 10KB
- [ ] Error raised when size > 20KB
- [ ] Clear error message with size info
- [ ] Suggests reducing content size

---

### Debug Verification

**Check internal knowledge loading:**

```python
from co_cli.internal_knowledge import load_internal_knowledge
from pathlib import Path

# Check file exists
context_file = Path.cwd() / ".co-cli" / "internal" / "context.json"
print(f"File exists: {context_file.exists()}")

# Load knowledge
knowledge = load_internal_knowledge()
if knowledge:
    print(f"Loaded {len(knowledge)} bytes")
    print("Content preview:")
    print(knowledge[:500])
else:
    print("No knowledge loaded")
```

**Check memory tools:**

```python
from co_cli.tools.memory import save_memory, recall_memory, list_memories
from co_cli.agent import CoDeps
from pydantic_ai import RunContext

ctx = RunContext(deps=CoDeps())

# Save test memory
result = await save_memory(
    ctx,
    category="user_preferences",
    key="test",
    value="Test value"
)
print(result["display"])

# List memories
result = await list_memories(ctx)
print(result["display"])
```

---

## Documentation Updates

### File: `README.md`

**Add new section:** "Internal Knowledge & Memory"

**Location:** After "Project-Specific Instructions" section

**Content:**

```markdown
## Internal Knowledge & Memory

Co can remember information across sessions using internal knowledge and memory tools.

### Internal Knowledge

Internal knowledge is auto-loaded from `.co-cli/internal/context.json` at every session start.

**Structure:**
```json
{
  "version": "1.0",
  "user": {
    "name": "Your Name",
    "timezone": "America/Los_Angeles",
    "preferences": {
      "coding_style": "Prefer async/await over callbacks",
      "verbosity": "Detailed explanations preferred"
    }
  },
  "project": {
    "name": "my-project",
    "type": "python_web_app",
    "patterns": [
      "Uses FastAPI for web framework",
      "SQLAlchemy for database ORM",
      "pytest for testing"
    ]
  },
  "learned_facts": {
    "facts": [
      "Database is PostgreSQL in production, SQLite in dev",
      "Team prefers explicit imports (no import *)"
    ]
  }
}
```

**Size limits:**
- Target: 10KB (warning if exceeded)
- Maximum: 20KB (error if exceeded)

### Memory Tools

Co provides three memory tools for explicit knowledge management:

**save_memory** - Save information for future sessions
```
User: I prefer snake_case for variable names
Co: [calls save_memory("user_preferences", "variable_naming", "Prefers snake_case")]
```

**recall_memory** - Retrieve saved memories
```
User: What are my coding preferences?
Co: [calls recall_memory(category="user_preferences")]
```

**list_memories** - Show all saved memories
```
User: List my saved memories
Co: [calls list_memories()]
```

**Storage locations:**
- Internal knowledge: `.co-cli/internal/context.json` (auto-loaded)
- Memory files: `.co-cli/memories/*.json` (granular storage)

**How it works:**
1. Use memory tools during conversations to save important information
2. Memories are stored in `.co-cli/memories/` (granular files)
3. Memories auto-merge into `.co-cli/internal/context.json`
4. Internal knowledge auto-loads at next session start
5. Co remembers context across sessions

**Example workflow:**
```bash
# Session 1
$ uv run co chat
User: I prefer async/await over callbacks
Co: I'll remember that preference. [saves to memory]

# Session 2 (later)
$ uv run co chat
User: How should I structure this async function?
Co: Based on your preference for async/await... [recalls from internal knowledge]
```

**Tips:**
- Keep internal knowledge under 10KB for best performance
- Use descriptive keys when saving memories
- Review `.co-cli/internal/context.json` periodically to prune old facts
- Commit context.json to git for team-shared knowledge
```

---

### File: `docs/DESIGN-00-co-cli.md`

**Update component table:**

Add new row:

```markdown
| `co_cli/internal_knowledge.py` | Internal knowledge schema, loading, and validation |
| `co_cli/tools/memory.py` | Memory management tools (save, recall, list) |
```

**Update cross-cutting concerns:**

Add new section:

```markdown
### Internal Knowledge

Internal knowledge represents Co's accumulated understanding that should always be available:
- Auto-loaded from `.co-cli/internal/context.json` at session start
- Size-controlled (10KB target, 20KB limit)
- Structured: user context, project context, learned facts
- Injected into prompt after personality, before project instructions

See `co_cli/internal_knowledge.py` for schema and loading logic.
See `co_cli/tools/memory.py` for memory management tools.
```

---

## Success Criteria

Phase 1c is complete when ALL of the following are true:

### Code Criteria
- [ ] `co_cli/internal_knowledge.py` module exists with schema classes
- [ ] `InternalKnowledge` Pydantic model defined with validation
- [ ] `load_internal_knowledge() -> str | None` function implemented
- [ ] Size validation enforced (10KB warn, 20KB error)
- [ ] Markdown formatting for prompt injection
- [ ] `co_cli/tools/memory.py` module exists with three tools
- [ ] `save_memory` tool saves and merges to context
- [ ] `recall_memory` tool searches and retrieves memories
- [ ] `list_memories` tool shows counts and summaries
- [ ] Internal knowledge integrated into `get_system_prompt()`
- [ ] Prompt assembly order correct (base → conditionals → personality → knowledge → project)

### Test Criteria
- [ ] `tests/test_internal_knowledge.py` exists with 8 tests
- [ ] All schema tests pass (validation, formatting, size)
- [ ] All loading tests pass (valid, missing, malformed, size limits)
- [ ] `tests/test_memory_tools.py` exists with 7 tests
- [ ] All save_memory tests pass (create, validate, merge)
- [ ] All recall_memory tests pass (search, filter, retrieve)
- [ ] All list_memories tests pass (count, summary)
- [ ] `tests/test_prompts.py` updated with 3 new tests
- [ ] Integration tests pass (knowledge in prompt, order correct)
- [ ] No regressions: `uv run pytest` (all tests pass)
- [ ] Coverage >90% for internal_knowledge and memory modules

### Behavioral Criteria
- [ ] Internal knowledge auto-loads at session start
- [ ] User context influences Co's responses
- [ ] Project context informs recommendations
- [ ] Learned facts affect behavior
- [ ] save_memory persists data across sessions
- [ ] recall_memory retrieves saved memories correctly
- [ ] list_memories shows accurate counts
- [ ] Memories merge into context.json automatically
- [ ] Size warning logged when knowledge > 10KB
- [ ] Size error raised when knowledge > 20KB
- [ ] Graceful degradation when context.json missing

### Documentation Criteria
- [ ] README.md documents internal knowledge structure
- [ ] README.md documents memory tools usage
- [ ] README.md includes example workflow
- [ ] DESIGN-00-co-cli.md updated with new modules
- [ ] Code comments explain boundary (internal vs external)

### Quality Criteria
- [ ] All functions have type hints
- [ ] All functions have docstrings (Google style)
- [ ] Pydantic models used for schema validation
- [ ] Error messages clear and actionable
- [ ] Logging appropriate (debug, warning, error levels)
- [ ] File paths use pathlib.Path
- [ ] JSON encoding specifies UTF-8
- [ ] No global state or mutable defaults
- [ ] Code follows Black formatting
- [ ] No import *

---

## Risk Assessment

### Identified Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Internal knowledge grows unbounded | Medium | High | Size limits enforced (10KB warn, 20KB error), clear error messages |
| Malformed JSON breaks loading | Low | Medium | Graceful error handling, returns None with warning, agent still works |
| Memory merge conflicts | Low | Medium | Simple append/merge strategy, deduplication for lists |
| Size limit too restrictive | Medium | Low | Conservative limits (20KB = ~5000 words), can adjust in Phase 2 |
| Performance impact from large context | Low | Low | Size limits prevent excessive context, loading happens once at startup |
| Schema evolution challenges | Medium | Medium | Version field for future migrations, Pydantic validation catches issues |
| Memory tools misused by users | Low | Low | Clear tool descriptions, validation, category constraints |
| File permission issues | Low | Low | Standard file operations, clear error messages |

### Risk Mitigation Strategy

**1. Size Management**
- Enforce hard limit (20KB) to prevent context explosion
- Warn at 10KB to encourage pruning
- Clear error messages with actual size vs limit
- Documentation suggests regular review and cleanup

**2. Graceful Degradation**
- Missing context.json returns None (not error)
- Malformed JSON logs warning and returns None
- Invalid schema logs warning and returns None
- Agent works fine without internal knowledge

**3. Data Validation**
- Pydantic models enforce schema structure
- Version field enables future migrations
- Category validation for memory tools
- Timestamp tracking for audit trail

**4. Error Handling**
- Try/except blocks around JSON parsing
- Specific error messages with context
- Logging at appropriate levels
- User-friendly display messages

**5. Testing Coverage**
- 18 comprehensive tests cover all paths
- Edge cases explicitly tested
- Integration tests verify end-to-end flow
- No mocks (functional tests only)

---

## Future Enhancements

### Phase 2: Advanced Memory Features

**Automatic Learning:**
- Detect when user corrects Co → save as learned fact
- Identify user preferences from repeated patterns
- Extract project conventions from code interactions

**Memory Summarization:**
- Compress old memories to save space
- Keep recent memories detailed, summarize old ones
- LLM-powered summarization of learned facts

**Memory Search:**
- Semantic search over memories (embedding-based)
- Relevance ranking for recall queries
- Cross-category search

**Memory Management:**
- Archive old memories
- Export/import memory sets
- Team-shared memories (merge strategies)

**Estimated effort:** 12-15 hours (includes research)

---

### Phase 3: Context Governance

**Dynamic Context Budget:**
- Adjust knowledge size based on model context window
- Prioritize recent memories over old ones
- Auto-prune least-used facts

**Memory Policies:**
- TTL for temporary facts
- Pinned memories (never prune)
- Category-specific size limits

**Privacy Controls:**
- Mark sensitive memories (exclude from logs)
- Per-project memory isolation
- User-level vs project-level separation

**Estimated effort:** 8-10 hours

---

## Implementation Checklist

### Phase 1: Schema Design (2 hours)

- [ ] Create `co_cli/internal_knowledge.py` module
- [ ] Add module docstring
- [ ] Define `UserContext` Pydantic model
  - [ ] name, timezone, working_hours fields
  - [ ] preferences dict
  - [ ] notes list
- [ ] Define `ProjectContext` Pydantic model
  - [ ] name, type fields
  - [ ] architecture, patterns, conventions lists
  - [ ] notes list
- [ ] Define `LearnedFacts` Pydantic model
  - [ ] facts, corrections, clarifications lists
- [ ] Define `InternalKnowledge` Pydantic model
  - [ ] version field with validation
  - [ ] created, updated timestamp fields
  - [ ] user, project, learned_facts sub-models
  - [ ] to_markdown() method
  - [ ] size_bytes() method
- [ ] Add SIZE_TARGET and SIZE_LIMIT constants
- [ ] Test models in Python REPL

---

### Phase 2: Loading Logic (1.5 hours)

- [ ] Add `load_internal_knowledge() -> str | None` function
- [ ] Check if `.co-cli/internal/context.json` exists
- [ ] Load and parse JSON with try/except
- [ ] Validate against InternalKnowledge schema
- [ ] Check size constraints (warn/error)
- [ ] Convert to markdown format
- [ ] Return None if missing or empty
- [ ] Add logging (debug, warning, error)
- [ ] Add `save_internal_knowledge()` helper function
- [ ] Test loading with various inputs

---

### Phase 3: Memory Tools (3 hours)

- [ ] Create `co_cli/tools/memory.py` module
- [ ] Add module docstring
- [ ] Add `save_memory` tool
  - [ ] Add `@agent.tool()` decorator
  - [ ] Parameters: category, key, value
  - [ ] Validate category (user_preferences | project_insights | learned_facts)
  - [ ] Create `.co-cli/memories/{category}.json`
  - [ ] Save memory with timestamp
  - [ ] Call `_merge_memories_to_context()`
  - [ ] Return dict with display + metadata
- [ ] Add `recall_memory` tool
  - [ ] Parameters: category (optional), key (optional), query (optional)
  - [ ] Search in memories/*.json
  - [ ] Apply filters (category, key, text query)
  - [ ] Return formatted results
- [ ] Add `list_memories` tool
  - [ ] Parameters: category (optional)
  - [ ] Count memories per category
  - [ ] Format summary with counts
  - [ ] Return dict with display + metadata
- [ ] Add `_merge_memories_to_context()` helper
  - [ ] Load existing context or create new
  - [ ] Merge user_preferences into user.preferences
  - [ ] Merge project_insights into project.patterns
  - [ ] Merge learned_facts into learned_facts.facts
  - [ ] Deduplicate lists
  - [ ] Save merged context
- [ ] Test each tool manually

---

### Phase 4: Prompt Integration (30 minutes)

- [ ] Open `co_cli/prompts/__init__.py`
- [ ] Add import: `from co_cli.internal_knowledge import load_internal_knowledge`
- [ ] Update `get_system_prompt()` function
  - [ ] Call `load_internal_knowledge()` after personality injection
  - [ ] If knowledge returned: append "## Internal Knowledge" section
  - [ ] If None: skip section
- [ ] Update docstring to document internal knowledge section
- [ ] Test prompt assembly manually

---

### Phase 5: Agent Integration (30 minutes)

- [ ] Open `co_cli/agent.py`
- [ ] Verify `get_system_prompt()` call includes internal knowledge (via Phase 4)
- [ ] Import memory tools (auto-registered via decorator)
- [ ] Test end-to-end flow:
  - [ ] Start chat session
  - [ ] Check internal knowledge loaded
  - [ ] Save a memory
  - [ ] Restart session
  - [ ] Verify memory persisted

---

### Phase 6: Testing (2.5 hours)

**File: `tests/test_internal_knowledge.py`**
- [ ] Create test file with module docstring
- [ ] Add `TestInternalKnowledgeSchema` class
  - [ ] `test_empty_knowledge_valid`
  - [ ] `test_full_knowledge_valid`
  - [ ] `test_invalid_version_raises_error`
  - [ ] `test_markdown_formatting_empty`
  - [ ] `test_markdown_formatting_full`
  - [ ] `test_size_calculation`
- [ ] Add `TestLoadInternalKnowledge` class
  - [ ] `test_load_missing_file_returns_none`
  - [ ] `test_load_valid_file_returns_markdown`
  - [ ] `test_load_malformed_json_returns_none`
  - [ ] `test_load_invalid_schema_returns_none`
  - [ ] `test_load_empty_knowledge_returns_none`
  - [ ] `test_load_warns_on_size_target_exceeded`
  - [ ] `test_load_raises_on_size_limit_exceeded`
- [ ] Add `TestSaveInternalKnowledge` class
  - [ ] `test_save_creates_file`
  - [ ] `test_save_raises_on_size_limit`
- [ ] Run: `uv run pytest tests/test_internal_knowledge.py -v`

**File: `tests/test_memory_tools.py`**
- [ ] Create test file with module docstring
- [ ] Add `TestSaveMemory` class
  - [ ] `test_save_memory_creates_file`
  - [ ] `test_save_memory_invalid_category_returns_error`
  - [ ] `test_save_memory_merges_to_context`
- [ ] Add `TestRecallMemory` class
  - [ ] `test_recall_memory_no_memories_returns_empty`
  - [ ] `test_recall_memory_finds_saved_memory`
  - [ ] `test_recall_memory_filters_by_key`
  - [ ] `test_recall_memory_text_search`
- [ ] Add `TestListMemories` class
  - [ ] `test_list_memories_no_memories_returns_empty`
  - [ ] `test_list_memories_counts_all_categories`
- [ ] Run: `uv run pytest tests/test_memory_tools.py -v`

**File: `tests/test_prompts.py`**
- [ ] Add `TestInternalKnowledgeIntegration` class
  - [ ] `test_prompt_with_internal_knowledge`
  - [ ] `test_prompt_without_internal_knowledge`
  - [ ] `test_prompt_assembly_order`
- [ ] Run: `uv run pytest tests/test_prompts.py::TestInternalKnowledgeIntegration -v`

**Full suite:**
- [ ] Run: `uv run pytest`
- [ ] Verify all tests pass (no regressions)
- [ ] Check coverage: `uv run pytest --cov=co_cli.internal_knowledge --cov=co_cli.tools.memory`

---

### Phase 7: Documentation (30 minutes)

- [ ] Update `README.md`
  - [ ] Add "Internal Knowledge & Memory" section
  - [ ] Document context.json structure
  - [ ] Document memory tools
  - [ ] Include example workflow
  - [ ] Document size limits
- [ ] Update `docs/DESIGN-00-co-cli.md`
  - [ ] Add internal_knowledge.py to component table
  - [ ] Add tools/memory.py to component table
  - [ ] Add cross-cutting concern section
- [ ] Review documentation for clarity

---

### Phase 8: Verification (1 hour)

**Automated:**
- [ ] Run full test suite: `uv run pytest`
- [ ] Check coverage: `uv run pytest --cov=co_cli`
- [ ] Verify no warnings or errors

**Manual - Internal Knowledge:**
- [ ] Create test context.json with sample data
- [ ] Start chat session
- [ ] Ask Co what it knows about user
- [ ] Verify responses include internal knowledge
- [ ] Test with missing context.json (should work)

**Manual - Memory Tools:**
- [ ] Save memory via conversation
- [ ] Verify file created in .co-cli/memories/
- [ ] Recall memory via conversation
- [ ] List all memories
- [ ] Restart session, verify persistence

**Manual - Size Limits:**
- [ ] Create context.json ~12KB (warn)
- [ ] Create context.json ~25KB (error)
- [ ] Verify appropriate warnings/errors

---

### Phase 9: Completion

- [ ] All success criteria met
- [ ] All tests pass
- [ ] Manual verification complete
- [ ] Documentation complete
- [ ] Code review complete

**Git commit:**
```bash
git add co_cli/internal_knowledge.py
git add co_cli/tools/memory.py
git add co_cli/prompts/__init__.py
git add tests/test_internal_knowledge.py
git add tests/test_memory_tools.py
git add tests/test_prompts.py
git add README.md
git add docs/DESIGN-00-co-cli.md

git commit -m "feat(knowledge): add internal knowledge and memory tools

- Add InternalKnowledge schema with user/project/learned_facts sections
- Add load_internal_knowledge() with size validation (10KB warn, 20KB limit)
- Add memory tools: save_memory, recall_memory, list_memories
- Integrate internal knowledge into prompt assembly (after personality)
- Add comprehensive test suite (18 tests, >90% coverage)
- Document internal knowledge and memory system in README

Phase 1c complete: Co now has persistent memory across sessions
Ref: docs/TODO-prompt-system-phase1c.md"
```

---

## Appendix A: Schema Examples

### Minimal Context

```json
{
  "version": "1.0",
  "user": {
    "name": "Alex"
  },
  "project": {
    "name": "co-cli"
  },
  "learned_facts": {}
}
```

### Full Context

```json
{
  "version": "1.0",
  "created": "2026-02-09T10:00:00",
  "updated": "2026-02-09T15:30:00",
  "user": {
    "name": "Alex Chen",
    "timezone": "America/Los_Angeles",
    "working_hours": "9am-6pm PT",
    "preferences": {
      "coding_style": "Prefer async/await over callbacks",
      "verbosity": "Detailed explanations preferred",
      "variable_naming": "snake_case for Python, camelCase for JavaScript",
      "imports": "Explicit imports only (no import *)"
    },
    "notes": [
      "Works best with concrete examples",
      "Prefers functional programming style when possible"
    ]
  },
  "project": {
    "name": "co-cli",
    "type": "python_cli",
    "architecture": [
      "Agent-based architecture using pydantic-ai",
      "Tool system with RunContext pattern",
      "SQLite for telemetry and traces"
    ],
    "patterns": [
      "Uses pydantic-ai for agent framework",
      "Docker for sandboxing shell commands",
      "pytest for testing (no mocks)",
      "Rich library for terminal output",
      "OpenTelemetry for observability"
    ],
    "conventions": [
      "Type hints required for all functions",
      "Google-style docstrings",
      "Black formatting (88 char line limit)",
      "Explicit imports only (no import *)",
      "Empty __init__.py files (no re-exports)"
    ],
    "notes": [
      "Config precedence: env vars > project > user > defaults",
      "XDG paths for config and data",
      "Functional tests only (no mocks policy)"
    ]
  },
  "learned_facts": {
    "facts": [
      "Database is SQLite for local development, co-cli.db",
      "Tests require Docker running for shell sandbox tests",
      "LLM provider can be 'gemini' or 'ollama'",
      "User prefers async/await pattern over callbacks",
      "Project follows semantic versioning (MAJOR.MINOR.PATCH)"
    ],
    "corrections": [
      "Initial assumption: Database was PostgreSQL. Corrected: It's SQLite.",
      "Thought shell commands ran directly. Corrected: They run in Docker sandbox."
    ],
    "clarifications": [
      "When user says 'test', they mean 'pytest', not unittest",
      "'Config' refers to Settings class in co_cli/config.py",
      "'Agent' refers to pydantic-ai Agent instance"
    ]
  }
}
```

---

## Appendix B: Markdown Formatting Example

**Input (context.json):**
```json
{
  "version": "1.0",
  "user": {
    "name": "Alex",
    "preferences": {
      "style": "async/await"
    }
  },
  "project": {
    "name": "co-cli",
    "patterns": ["Uses pydantic-ai"]
  },
  "learned_facts": {
    "facts": ["Database is SQLite"]
  }
}
```

**Output (markdown in prompt):**
```markdown
## Internal Knowledge

### User Context
- **Name:** Alex

**Preferences:**
- style: async/await

### Project Context
- **Name:** co-cli

**Patterns:**
- Uses pydantic-ai

### Learned Facts

**Facts:**
- Database is SQLite
```

---

## Appendix C: Timeline Estimate

| Phase | Task | Time Estimate |
|-------|------|---------------|
| 1 | Schema design (Pydantic models) | 2 hours |
| 2 | Loading logic (load_internal_knowledge) | 1.5 hours |
| 3 | Memory tools (save/recall/list) | 3 hours |
| 4 | Prompt integration | 30 minutes |
| 5 | Agent integration | 30 minutes |
| 6 | Testing (18 comprehensive tests) | 2.5 hours |
| 7 | Documentation (README, DESIGN) | 30 minutes |
| 8 | Verification (manual testing) | 1 hour |
| **Total** | | **~11 hours** |

**Breakdown by activity:**
- **Implementation:** 7.5 hours
- **Testing:** 2.5 hours
- **Documentation:** 0.5 hour
- **Verification:** 0.5 hour

**Assumptions:**
- Uninterrupted work time
- No major design changes
- Tests pass on first or second attempt
- Familiar with Pydantic and tool system

**Buffer:**
- Add 20% for context switching (13 hours)
- Add 50% for discovery/debugging (16 hours)

**Realistic estimate:** 2-3 days (including reviews, breaks, other tasks)

---

**END OF PHASE 1C IMPLEMENTATION GUIDE**

This document contains all specifications for Phase 1c implementation. Nothing has been omitted.
