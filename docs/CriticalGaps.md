Critical Gaps

  1. Misalignment Between Tactical and Strategic Plans

  The TODO documents (Phase 1a/1b/1c) and DESIGN-co-evolution.md use different phasing:

  - TODO structure: 1a (conditionals), 1b (personalities), 1c (internal knowledge), Phase 2 (preferences)
  - DESIGN structure: Phase 1 (memory tools + personality + internal knowledge + task checkpoints), Phase 2 (MCP
   + background + preferences), Phase 3 (autonomy + voice)

  This is confusing. Need one unified roadmap.

  2. "Internal Knowledge" is Underspecified

  The document mentions:
  - "Co's learned context, patterns, user habits"
  - "Storage: .co-cli/internal/"
  - "Always available in context"

  But doesn't answer:
  - How does knowledge get IN? (User tells co? Co infers? Explicit commands?)
  - What's the schema? (JSON? SQLite? Plain text?)
  - What's the boundary between "memory tools" and "internal knowledge"?
    - Memory tools = explicit save/recall
    - Internal knowledge = automatic context loading?
  - How big can it get? (1KB? 10KB? 100KB? Context budget management?)

  3. Missing Connection to Peer Learnings

  REVIEW-compare-four.md identified 10 transferable techniques. DESIGN-co-evolution.md doesn't mention:
  - Model quirk counter-steering (Aider)
  - system_reminder pattern (Aider)
  - Escape hatches (Codex)
  - Contrast examples (Codex)
  - Commentary in examples (Claude Code)
  - Anti-prompt-injection in compression (Gemini CLI)

  These should be in the prompt evolution plan.

  4. Personality ↔ Preferences Interaction Unclear

  Both "personality" (done in 1b) and "preferences" (planned in Phase 2) affect behavior:
  - Personality = communication style (formal, friendly, terse)
  - Preferences = workflow settings (verbosity, auto-approve)

  But what if they conflict? If I select "terse" personality but "verbose" preference, what wins?

  5. Memory Tools Not Designed Yet

  DESIGN-co-evolution.md says "Add explicit local memory tools" in Phase 1, but there's no design doc. The TODO
  documents jump straight from personalities (1b) to internal knowledge (1c) without covering memory tools.

  Recommendations to Make Co Better

  Immediate (Fix Alignment Issues)

  R1. Create unified roadmap document

  Merge the phasing from TODO-* and DESIGN-co-evolution.md:

  Phase 1a: Model Conditionals ✅ COMPLETE
  Phase 1b: Personality Templates ✅ COMPLETE
  Phase 1c: Internal Knowledge (NEXT)
    - Memory tools (save_memory, recall_memory, list_memories)
    - Internal knowledge storage (.co-cli/internal/context.json)
    - Prompt injection after personality, before project instructions

  Phase 1d: Prompt Improvements (Quick Win)
    - Apply peer learnings: system_reminder, escape hatches, contrast examples
    - Add model quirk counter-steering database
    - Enhance system.md based on REVIEW-compare-four.md analysis

  Phase 2a: MCP Client (stdio → HTTP → OAuth)
  Phase 2b: User Preferences System
    - Research peer systems + 2026 best practices
    - Design preference precedence: personality vs preferences conflict resolution

  Phase 2c: Background Execution
    - Async task runner with approval inheritance
    - Status inspection, cancellation, persisted logs

  Phase 3a: Task Checkpoints & Planner/Result Contract
  Phase 3b: Voice Runtime (push-to-talk, cascading architecture)
  Phase 3c: Selective Autonomy (scheduled tasks, computer-use in isolation)

  R2. Design internal knowledge schema NOW (before implementing 1c)

  Create docs/TODO-prompt-system-phase1c.md with:

  {
    "user": {
      "name": "string",
      "timezone": "string",
      "work_context": "string",
      "communication_preferences": {
        "explain_reasoning": "boolean",
        "citation_style": "inline|footnotes"
      }
    },
    "project": {
      "name": "string",
      "type": "python_cli|web_app|ml_research",
      "architecture_notes": "string",
      "common_commands": ["list", "of", "strings"]
    },
    "learned_patterns": [
      {
        "pattern": "User prefers async/await over callbacks",
        "learned_from": "2026-02-09 session",
        "confidence": "high|medium|low"
      }
    ],
    "memory_index": {
      "last_updated": "ISO8601 timestamp",
      "total_memories": "integer",
      "memory_references": ["memory-001", "memory-002"]
    }
  }

  Size budget: 5KB base + 1KB per 10 memories (target: <10KB total for context efficiency)

  R3. Clarify memory tools vs internal knowledge boundary
  Feature: Access
  Memory Tools: Explicit tool calls
  Internal Knowledge: Auto-loaded at session start
  ────────────────────────────────────────
  Feature: Scope
  Memory Tools: Specific facts ("API key is in 1Password")
  Internal Knowledge: Persistent context (user name, project type)
  ────────────────────────────────────────
  Feature: Storage
  Memory Tools: .co-cli/memories/ (individual files)
  Internal Knowledge: .co-cli/internal/context.json (single file)
  ────────────────────────────────────────
  Feature: Size
  Memory Tools: Unbounded (user creates as needed)
  Internal Knowledge: Bounded (<10KB for context efficiency)
  ────────────────────────────────────────
  Feature: Prompt injection
  Memory Tools: On-demand via recall_memory tool
  Internal Knowledge: Always present after personality layer
  Near-Term (Leverage Peer Learnings)

  R4. Implement Phase 1d: Prompt Improvements (Peer Techniques)

  This is a quick win that improves co immediately:

  1. Add system_reminder section (Aider pattern):
    - Place critical rules at END of prompt (exploit recency bias)
    - Duplicate 3 most important rules: Directive vs Inquiry, Tool output display, Fact verification
  2. Add escape hatches (Codex pattern):
    - "Never reformat tool output" → "Never reformat tool output unless explicitly requested"
    - Prevents agent stuck states
  3. Add contrast examples (Codex pattern):
    - For Directive vs Inquiry: show BOTH correct and incorrect responses
    - Bad examples should be plausibly "good enough" (harder to distinguish = more effective training)
  4. Add model quirk counter-steering (Aider pattern):
    - Database in co_cli/prompts/model_quirks.py:
  MODEL_QUIRKS = {
      "gemini-2.0-flash-exp": {"lazy": False, "overeager": False},
      "qwen2.5-coder:32b": {"lazy": True, "overeager": False},  # tends to leave TODOs
      "deepseek-r1:32b": {"lazy": False, "overeager": True},    # tends to refactor unrelated code
  }
    - Inject counter-steering at prompt assembly time
  5. Add commentary to examples (Claude Code pattern):
    - When showing few-shot examples, add WHY the example matters
    - Teaches principles, not just patterns

  R5. Design personality ↔ preferences interaction

  Add to Phase 2b design:

  Precedence when conflict:
  1. Explicit user command in current turn (highest)
  2. User preferences (.co-cli/preferences.json)
  3. Personality template (selected style)
  4. Base system prompt (lowest)

  Example conflict resolution:
  - User selects personality="terse" (implies minimal verbosity)
  - User sets preference verbosity="detailed"
  - Resolution: Preference wins for content depth, personality wins for tone
    - Co provides detailed information (preference) in terse style (personality)

  Long-Term (Strategic Improvements)

  R6. Add explicit "learning mode" for internal knowledge

  Rather than co silently inferring user preferences (creepy, hard to debug), add:

  co learn "I prefer async/await over callbacks"
  co learn "This project uses SQLAlchemy ORM exclusively"
  co forget "pattern about callbacks"  # user can delete
  co knowledge --list                    # see what co knows

  This preserves user control and explainability.

  R7. Voice should be Phase 3, but prepare architecture now

  The voice design is excellent, but it's far away. To avoid late-stage rework:

  1. Design audio I/O protocol now (even if unimplemented):
  class AudioInput(Protocol):
      async def listen(self) -> AsyncIterator[bytes]: ...

  class AudioOutput(Protocol):
      async def speak(self, text: str) -> None: ...
  2. Make chat loop voice-ready:
    - Separate "get input" from "process turn"
    - Allow input source to be text OR audio transcription
    - Allow output to be text-only OR text+audio
  3. Add voice stubs to codebase (no-op implementations):
  # co_cli/voice.py (stub)
  def voice_available() -> bool:
      return False  # TODO: Phase 3

  This prevents architecture debt when Phase 3 arrives.

  R8. Consider "skills" system separate from personality

  DESIGN-co-evolution.md doesn't mention skills, but Claude Code has them. For co:

  - Personality = how co communicates (tone, style, verbosity)
  - Skills = what co can teach/explain (e.g., "Python best practices", "Git workflows")

  Skills could be markdown files in co_cli/prompts/skills/ loaded on-demand when user asks domain-specific
  questions.

  Deferred to post-Phase-2 but keep architecture open.

  Summary: Top 3 Priorities

  1. Align roadmaps (R1) - Create unified phasing document
  2. Design internal knowledge schema (R2) - Define JSON structure, size budget, boundary with memory tools
  3. Implement prompt improvements (R4) - Quick win applying peer learnings (system_reminder, escape hatches,
  model quirks, contrast examples)

  The vision in DESIGN-co-evolution.md is strong. The main gap is tactical execution planning - the document
  describes the destination but doesn't provide detailed mile markers. The TODO-phase-* documents provide
  execution detail but aren't connected to the strategic vision.

  You need a bridge document that connects:
  - Five pillars (Soul, Internal Knowledge, External Knowledge, Emotion, Habit)
  - Phase structure (1a/1b/1c/2a/2b/2c/3a/3b/3c)
  - Peer learnings (10 transferable techniques from REVIEW-compare-four.md)
  - Implementation details (schemas, APIs, test strategies)

  Should I proceed with creating comprehensive plan files for Phase 1c, 1d, and 2a/2b/2c?