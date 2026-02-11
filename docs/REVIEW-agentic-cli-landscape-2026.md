# REVIEW: Agentic CLI Landscape 2026

**Analysis Date:** 2026-02-08
**Scope:** Comprehensive survey of frontier agentic CLI coding systems
**Method:** Web search + GitHub analysis + existing repo review

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Market Overview](#market-overview)
3. [Tier 1: Established Leaders](#tier-1-established-leaders)
4. [Tier 2: Rising Stars](#tier-2-rising-stars)
5. [Tier 3: Open Source Alternatives](#tier-3-open-source-alternatives)
6. [Tier 4: IDE-Based Systems](#tier-4-ide-based-systems)
7. [Comparative Analysis](#comparative-analysis)
8. [Key Trends & Innovations](#key-trends--innovations)
9. [Recommendations for co-cli](#recommendations-for-co-cli)
10. [Sources](#sources)

---

## Executive Summary

### The Agentic CLI Era Has Arrived

**2025-2026 marks the transition from IDE chatbots to autonomous terminal agents.** The CLI is no longer just where you run commands—it's where you delegate entire features to AI agents that understand your codebase, make multi-file changes, run tests, and commit code with minimal human input.

### Market Size

As of early 2026, there are **15+ significant agentic CLI systems** competing in the space:

| Category | Systems | Maturity |
|----------|---------|----------|
| **Established Leaders** | 4 systems | Production-ready |
| **Rising Stars** | 4 systems | Emerging/2025-2026 launches |
| **Open Source Alternatives** | 5+ systems | Community-driven |
| **IDE-Based** | 2 major systems | IDE-first, not pure CLI |

### Key Finding

**No single "best" system exists.** Developers choose based on where they want leverage:
- **Speed & flow** (IDE integration)
- **Control & reliability** (large codebases)
- **Autonomy** (minimal interaction)
- **Privacy** (local models)
- **Cost** (open source + BYOK)

### Complexity Spectrum

Systems range from **minimal** (Aider: ~500 lines, approval-first) to **massive** (Claude Code: ~182K lines including plugins).

---

## Market Overview

### The 2025-2026 Shift

**From:** Simple code completion (GitHub Copilot 2021-2023)
**To:** Autonomous agents (2025-2026)

**Key Capabilities:**
- Multi-file coordination
- Repository-wide understanding
- Test execution & iteration
- Git operations (commit, branch, PR)
- Shell command execution
- Terminal-first workflows

### Market Segmentation

| Segment | Primary Users | Key Need | Example Systems |
|---------|---------------|----------|-----------------|
| **Terminal Purists** | CLI-first developers | Fast, local, git-integrated | Aider, OpenCode, Codex |
| **Enterprise Scale** | Large teams, monorepos | Massive context windows (1M+ LOC) | Plandex, Windsurf |
| **Model Flexibility** | Privacy-conscious, cost-optimizers | Multi-provider, local models | OpenCode (75+ providers) |
| **Full Autonomy** | Rapid prototypers | Minimal interaction | Cline, Kimi Code |
| **Reasoning Depth** | Complex refactoring | Deep codebase understanding | Claude Code, Gemini CLI |

---

## Tier 1: Established Leaders

### 1. Claude Code (Anthropic)

**Status:** Production, reviewed in depth ✅

**Architecture:**
- Binary core + plugin system
- 79 files, ~182,400 lines (incl. skill references)
- Event-driven composition (5 hook events)
- Three prompt primitives (agents, commands, skills)

**Strengths:**
- Exceptional reasoning ability
- Massive context window (200K+ tokens)
- Plugin extensibility
- Multi-agent orchestration

**Weaknesses:**
- Binary core opacity
- Very high complexity
- Steep learning curve

**Best For:** Complex refactoring, architectural discussions, large codebases

**Innovation Score:** 9/10

**Sources:** [Faros AI Review](https://www.faros.ai/blog/best-ai-coding-agents-2026), [Pinggy Top 5](https://pinggy.io/blog/top_cli_based_ai_coding_agents/)

---

### 2. Codex (OpenAI)

**Status:** Production, reviewed in depth ✅

**Architecture:**
- Multi-file modular prompts (Rust)
- 15 files, ~2,225 lines
- Policy fragment composition
- Deep command safety analysis

**Strengths:**
- Reviewable, traceable
- Best-in-class security (policy fragments)
- Deep command tokenization
- Bwrap sandbox integration

**Weaknesses:**
- Prompt family sprawl (7+ base files)
- Maintenance sync burden
- No plugin system

**Best For:** Security-critical environments, teams requiring audit trails

**Innovation Score:** 9/10

**Notes:** Lightweight, local agent that runs in terminal, authenticates via ChatGPT subscription

**Sources:** [KDnuggets Top 5](https://www.kdnuggets.com/top-5-agentic-coding-cli-tools), [DEV Community](https://dev.to/lightningdev123/top-5-cli-coding-agents-in-2026-3pia)

---

### 3. Gemini CLI (Google)

**Status:** Production, reviewed in depth ✅

**Architecture:**
- Function-based conditional composition (TypeScript)
- 5 files, ~3,500 lines
- Single generator with model-specific variants
- Conditional micro-injections

**Strengths:**
- Single source of truth
- Type-safe (TypeScript)
- "Directive vs Inquiry" distinction (unique innovation)
- Memory tool constraints
- Fast feedback, strong on frontend tasks

**Weaknesses:**
- Monolithic (671-line main generator)
- String concatenation fragility
- No plugin system

**Best For:** Google Cloud workflows, frontend development, clear API integrations

**Innovation Score:** 8.5/10

**GitHub:** [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli)

**Notable:** Open-source release by Google (fall 2025), quickly accumulated 80K+ stars

**Sources:** [Gemini CLI GitHub Actions](https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemini-cli-github-actions/), [GitHub Repository](https://github.com/google-gemini/gemini-cli)

---

### 4. Aider

**Status:** Production, in reference repos ✅ (not yet reviewed)

**Architecture:**
- Python-based, minimalist design
- ~500 lines (estimated)
- Approval-first philosophy
- No sandbox, every action requires confirmation

**Strengths:**
- Outstanding Git automation
- Automatic commit messages
- Works with any IDE
- Supports local models
- Excellent for refactoring
- Free and open source

**Weaknesses:**
- No sandbox (every action prompts)
- Less autonomous than others
- Basic compared to complex systems

**Best For:** Terminal workflows, Git-heavy projects, refactoring tasks, teams prioritizing Git integration

**Philosophy:** "Proves you can ship without a sandbox if approval gate is strict"

**Pricing:** Free + API costs only (e.g., DeepSeek ~$1.27/million tokens)

**Innovation:** Minimalism as a strength - shows complexity isn't required

**Sources:** [AIMuitple Comparison](https://aimultiple.com/agentic-cli), [Best AI Code Editor](https://research.aimultiple.com/ai-code-editor/)

---

## Tier 2: Rising Stars

### 5. Cline (formerly Claude Dev)

**Status:** Rapidly growing, 2025 launch

**Type:** VS Code extension (CLI-like autonomous agent)

**GitHub:** [cline/cline](https://github.com/cline/cline)

**Architecture:**
- Autonomous AI coding agent
- Lives inside VS Code
- Complete workflows from single natural language prompt
- Permission-gated at every step

**Strengths:**
- Execute complete features autonomously
- Multi-step task planning
- Context-aware file operations
- Browser integration
- Permission model (user approval required)

**Weaknesses:**
- Requires VS Code (not pure CLI)
- Newer, less battle-tested

**Best For:** VS Code users wanting autonomous workflows, developers who prefer IDE integration

**Notable:** Described as "autonomous coding agent right in your IDE"

**Sources:** [Cline Review 2026](https://vibecoding.app/blog/cline-review-2026), [Cline Bot](https://cline.bot/), [GitHub Repository](https://github.com/cline/cline)

---

### 6. Plandex

**Status:** Established open source project

**GitHub:** 14.2K+ stars

**Architecture:**
- Terminal-based AI development tool
- Built for massive codebases
- Can index and reason over millions of tokens

**Strengths:**
- Handles extremely large codebases (1M+ tokens)
- Plans and executes huge coding tasks
- Systematic approach to complex changes

**Weaknesses:**
- Learning curve for complex features
- May be overkill for small projects

**Best For:** Enterprise teams, monorepos, massive refactoring projects, complex multi-module changes

**Differentiator:** Scale-first design - "built for the big stuff"

**Sources:** [Top 10 Open-Source CLI Agents](https://dev.to/forgecode/top-10-open-source-cli-coding-agents-you-should-be-using-in-2025-with-links-244m), [GitHub AI Repositories](https://opendatascience.com/the-top-ten-github-agentic-ai-repositories-in-2025/)

---

### 7. Kimi Code

**Status:** Brand new (2026 launch)

**Provider:** Moonshot AI (China)

**Model:** Based on K2.5

**Architecture:**
- AI Agent with autonomous planning
- Runs directly in terminal
- Multimodal input support

**Strengths:**
- Autonomous planning capabilities
- Multimodal (text + images)
- China's frontier AI in CLI form

**Weaknesses:**
- Very new (limited track record)
- May have regional optimization (Chinese market)

**Best For:** Developers wanting cutting-edge Chinese AI models, multimodal terminal interactions

**Differentiator:** Multimodal input in terminal environment

**Sources:** [Getting Started with Kimi Code](https://dev.to/james_miller_8dc58a89cb9e/getting-started-with-moonshot-ais-kimi-code-an-autonomous-coding-agent-for-your-terminal-5ei9)

---

### 8. Kilo CLI

**Status:** Just launched (February 3, 2026)

**Architecture:**
- Production-ready, model-agnostic
- Command-line interface for agentic code generation

**Strengths:**
- Model-agnostic (works with any provider)
- Production-ready from day one
- Enterprise-focused

**Weaknesses:**
- Too new to assess comprehensively
- Limited public information

**Best For:** Teams wanting provider flexibility, enterprise deployments

**Differentiator:** Model-agnostic design, newest entrant

**Notable:** Announced Feb 3, 2026 - fresh in the market

**Sources:** [Kilo CLI Release](https://www.aicerts.ai/news/kilo-cli-release-reshapes-terminal-coding-workflows/)

---

## Tier 3: Open Source Alternatives

### 9. OpenCode

**Status:** Established, in reference repos ✅ (not yet reviewed)

**Language:** Go

**GitHub:** Open source

**Architecture:**
- Multi-provider support (75+ LLM providers)
- Supports local models
- Go-based implementation

**Strengths:**
- Truly open source alternative to Claude Code
- Run any model (OpenAI, Anthropic, Google, local, etc.)
- Privacy-focused (local model support)
- Provider flexibility

**Weaknesses:**
- Go ecosystem (different from Python/TypeScript mainstream)
- Smaller community than leaders

**Best For:** Privacy-conscious teams, cost optimization (local models), multi-provider flexibility

**Differentiator:** 75+ provider support - broadest provider compatibility

**Sources:** [Pinggy Top 5](https://pinggy.io/blog/top_cli_based_ai_coding_agents/)

---

### 10. Goose

**Status:** Established open source

**Provider:** Block (Square)

**GitHub:** [block/goose](https://block.github.io/goose/)

**Architecture:**
- Open source AI agent
- Automates engineering tasks seamlessly

**Strengths:**
- Backed by Block/Square (enterprise credibility)
- Open source
- Production use at Block

**Weaknesses:**
- Less documentation than alternatives
- Smaller community

**Best For:** Teams wanting enterprise-backed open source, Block/Square ecosystem users

**Sources:** [GitHub Agentic AI Repositories](https://odsc.medium.com/the-top-ten-github-agentic-ai-repositories-in-2025-1a1440fe50c5), [Block Goose](https://block.github.io/goose/)

---

### 11. GPT Engineer

**Status:** Established open source

**GitHub:** 54.6K+ stars

**Architecture:**
- CLI tool for building apps from specs
- Focus on initial scaffolding

**Strengths:**
- Excellent for greenfield projects
- Large community (54K+ stars)
- Simple to use

**Weaknesses:**
- More scaffolding than iterative development
- Less sophisticated for complex refactoring

**Best For:** New projects, rapid prototyping, app generation from specifications

**Differentiator:** Spec-to-app generation focus

**Sources:** [Top 10 Open-Source CLI Agents](https://dev.to/forgecode/top-10-open-source-cli-coding-agents-you-should-be-using-in-2025-with-links-244m)

---

### 12. Spec Kit

**Status:** Rapidly growing (2025 launch)

**GitHub:** 50K+ stars (accumulated rapidly in 2025)

**Architecture:**
- CLI workflow structuring tool
- Works with Copilot, Claude, Gemini CLI
- Orchestration layer

**Strengths:**
- Provides structure to AI-assisted coding
- Model-agnostic (orchestrates others)
- Rapid adoption (50K stars in short time)

**Weaknesses:**
- Not a standalone agent (requires other tools)
- Orchestration overhead

**Best For:** Teams using multiple AI tools, standardizing AI workflows

**Differentiator:** Orchestration layer for other AI coding tools

**Sources:** [Top Ten GitHub Agentic AI Repositories](https://opendatascience.com/the-top-ten-github-agentic-ai-repositories-in-2025/)

---

### 13. OpenHands (formerly OpenDevin)

**Status:** Established open source

**GitHub:** [All-Hands-AI/OpenHands](https://github.com/All-Hands-AI/OpenHands)

**Architecture:**
- Open-source AI software engineer
- Autonomous development environment
- Browser-based interface

**Strengths:**
- Comprehensive environment (not just CLI)
- Open source Devin alternative
- Active development

**Weaknesses:**
- More environment than pure CLI
- Browser-based (not terminal-native)

**Best For:** Teams wanting Devin-like capabilities without cost, full environment control

**Note:** Not pure CLI - more of a full development environment

**Sources:** [Top 10 Open-Source CLI Agents](https://dev.to/forgecode/top-10-open-source-cli-coding-agents-you-should-be-using-in-2025-with-links-244m)

---

### 14. Devika

**Status:** Open source

**GitHub:** [stitionai/devika](https://github.com/stitionai/devika)

**Architecture:**
- First open-source agentic software engineer implementation
- Open source alternative to Devin

**Strengths:**
- Fully open source
- Agentic workflow design
- Community-driven

**Weaknesses:**
- Less mature than commercial alternatives
- Smaller community than OpenHands

**Best For:** Open source purists, learning agentic patterns, research

**Sources:** [GitHub Repository](https://github.com/stitionai/devika)

---

## Tier 4: IDE-Based Systems

*(Not pure CLI, but notable in the landscape)*

### 15. Windsurf (with Cascade Agent)

**Status:** Production IDE

**Type:** AI-powered IDE with Cascade agentic mode

**Architecture:**
- Remote codebase processing
- Hierarchical context planning
- Cascade agent for multi-file changes

**Strengths:**
- Handles 1M+ LOC codebases
- Hierarchical context understanding
- Remote processing (no local limits)
- Currently promoting Gemini 2.5

**Weaknesses:**
- IDE-bound (not pure CLI)
- BYOK required for Claude models (strained Anthropic relationship)
- Less terminal-native

**Pricing:**
- Cheaper than Cursor
- Gemini 2.5 default
- BYOK for frontier models

**Best For:** Large monorepos, enterprise teams, developers preferring IDE integration

**Sources:** [Windsurf vs Cursor](https://www.datacamp.com/blog/windsurf-vs-cursor), [IDE Comparison](https://vibecoding.app/blog/cursor-vs-windsurf)

---

### 16. Cursor

**Status:** Production IDE

**Type:** AI-powered IDE with Agent Mode

**Architecture:**
- Credit-based frontier model access
- Agent mode with broad tool access
- Grep, fuzzy file matching, codebase operations

**Strengths:**
- Access to all frontier models (Claude 4, GPT-5, etc.)
- Advanced tooling (grep, fuzzy search)
- Strong agent capabilities
- When credits run out, API pricing + 20% markup

**Weaknesses:**
- IDE-bound (not pure CLI)
- Higher cost than alternatives
- Less terminal-native

**Best For:** Experienced engineers, production-grade scenarios, access to best models

**Sources:** [Windsurf vs Cursor](https://www.builder.io/blog/windsurf-vs-cursor), [Agentic IDE Comparison](https://www.codecademy.com/article/agentic-ide-comparison-cursor-vs-windsurf-vs-antigravity)

---

## Comparative Analysis

### By Complexity

| System | Complexity | Files | Lines | Architecture |
|--------|-----------|-------|-------|--------------|
| **Aider** | Minimal | ~1 | ~500 | Approval-first |
| **Gemini CLI** | Medium | 5 | ~3,500 | Function composition |
| **Codex** | High | 15 | ~2,225 | Policy fragments |
| **OpenCode** | Medium | Unknown | Unknown | Multi-provider |
| **Plandex** | Medium-High | Unknown | Unknown | Scale-first |
| **Cline** | Medium | Unknown | Unknown | VS Code agent |
| **Claude Code** | Very High | 79 | ~182,400 | Plugin system |

---

### By Philosophy

| Philosophy | Primary Champion | Secondary Examples | Key Trait |
|------------|------------------|-------------------|-----------|
| **Minimalism** | Aider | GPT Engineer | Approval-first, simplicity |
| **Clarity** | Gemini CLI | - | Single source of truth |
| **Security** | Codex | - | Policy fragments, deep safety |
| **Extensibility** | Claude Code | Cline | Plugin/agent architecture |
| **Scale** | Plandex | Windsurf | Massive codebase handling |
| **Flexibility** | OpenCode | Kilo CLI | Multi-provider support |
| **Autonomy** | Kimi Code | Cline | Minimal interaction |

---

### By Innovation Score

| System | Score | Key Innovation |
|--------|-------|----------------|
| **Codex** | 9/10 | Orthogonal policy fragments, pre-approved command prefixes |
| **Claude Code** | 9/10 | Three prompt primitives, event-driven hooks, plugin architecture |
| **Gemini CLI** | 8.5/10 | Directive vs Inquiry, explain before acting, memory constraints |
| **Aider** | 8/10 | Minimalism as validation, approval-first works |
| **Plandex** | 8/10 | Massive scale handling (millions of tokens) |
| **OpenCode** | 7.5/10 | 75+ provider support |
| **Others** | 7-8/10 | Various specialized innovations |

---

### By Use Case

| Use Case | Best System | Why | Alternative |
|----------|-------------|-----|-------------|
| **Complex Refactoring** | Claude Code | Deep reasoning, multi-agent orchestration | Codex |
| **Git-Heavy Workflows** | Aider | Automatic commits, multi-file coordination | Codex |
| **Massive Codebases** | Plandex | Millions of tokens, scale-first design | Windsurf |
| **Privacy/Local Models** | OpenCode | 75+ providers, local model support | Aider |
| **Security-Critical** | Codex | Policy fragments, deep command safety | Claude Code (hooks) |
| **Rapid Prototyping** | GPT Engineer | Spec-to-app generation | Cline |
| **Enterprise Monorepos** | Windsurf | 1M+ LOC handling | Plandex |
| **Frontend Development** | Gemini CLI | Fast feedback, frontend-optimized | Cursor |
| **Terminal Purists** | Aider | Pure CLI, no IDE required | Codex |
| **Model Flexibility** | Kilo CLI | Model-agnostic | OpenCode |

---

### By Pricing Model

| System | Cost Model | Notes |
|--------|-----------|-------|
| **Aider** | Free + API costs | DeepSeek ~$1.27/M tokens |
| **OpenCode** | Free + API costs | BYOK any provider |
| **Goose** | Free + API costs | Open source |
| **GPT Engineer** | Free + API costs | Open source |
| **Codex** | ChatGPT subscription | Local agent, lightweight |
| **Gemini CLI** | Google Cloud pricing | Free tier available |
| **Claude Code** | Anthropic pricing | Context window premium |
| **Cursor** | Credit-based | API + 20% markup after credits |
| **Windsurf** | Subscription | Gemini 2.5 included, BYOK for Claude |
| **Cline** | Free (VS Code) + API | Extension + model costs |

---

## Key Trends & Innovations

### 1. The Agentic Shift (2025-2026)

**From:** Suggestion-based assistance (Copilot 2021-2023)
**To:** Autonomous agents (2025-2026)

**Characteristics:**
- Plan multi-step tasks
- Execute without constant guidance
- Iterate on failures
- Manage git operations
- Run tests automatically

---

### 2. Three Architectural Paradigms

#### A. Minimalism (Aider)
- Approval-first
- No sandbox
- Git-centric
- Simple prompts

**Validation:** Proves complexity isn't required for success

#### B. Modular Composition (Codex, Gemini CLI)
- Policy fragments or conditional blocks
- Type-safe configuration
- Reviewable, testable
- Medium complexity

#### C. Plugin Extensibility (Claude Code)
- Event-driven hooks
- Three prompt primitives
- Massive scale (79 files, ~182K lines)
- High complexity

---

### 3. Multi-Provider Flexibility

**Trend:** Move away from vendor lock-in

**Champions:**
- **OpenCode:** 75+ providers
- **Kilo CLI:** Model-agnostic design
- **Aider:** Works with any model

**Impact:** Teams can optimize cost, privacy, and capability independently

---

### 4. Scale-First Design

**Trend:** Handling massive codebases (1M+ lines)

**Champions:**
- **Plandex:** Millions of tokens
- **Windsurf:** 1M+ LOC remote processing
- **Claude Code:** 200K+ context window

**Why:** Enterprise adoption requires monorepo support

---

### 5. Prompt Architecture Innovations

| Innovation | Champion | Description |
|------------|----------|-------------|
| **Directive vs Inquiry** | Gemini CLI | Prevents unwanted modifications during research |
| **Policy Fragments** | Codex | Security decoupled from base prompts |
| **Three Primitives** | Claude Code | Agents, commands, skills taxonomy |
| **Event-Driven Hooks** | Claude Code | SessionStart, PreToolUse, PostToolUse |
| **Pre-Approved Prefixes** | Codex | Command patterns injected at runtime |
| **Approval-First** | Aider | Every action requires confirmation |

---

### 6. Terminal Resurgence

**Observation:** After a decade of heavy IDEs, CLI is back as the center of gravity

**Why:**
- Fast, keyboard-driven workflows
- Git-native integration
- Scriptable, automatable
- Lower cognitive load than IDE

**Evidence:** 15+ significant CLI agents launched 2025-2026

---

### 7. Model Specialization

**Trend:** Different models for different tasks

**Examples:**
- **Claude Code:** Per-agent model selection (haiku for simple, opus for complex)
- **Windsurf:** Gemini 2.5 default, BYOK for frontier
- **Cursor:** Credit system for frontier model access

**Impact:** Cost optimization + capability matching

---

## Recommendations for co-cli

### Landscape Insights

**Key Finding:** The field is **not converging** on a single approach. Instead, three viable paradigms coexist:

1. **Minimalism works** (Aider proves it)
2. **Modular complexity works** (Codex, Gemini CLI)
3. **Plugin extensibility works** (Claude Code)

**Implication:** co-cli should choose based on **target audience** and **maintenance capacity**, not "best practice."

---

### Recommended Review Priority

#### Tier 1: Must Review (Highest ROI)

1. **Aider** ⭐⭐⭐
   - **Why:** Validates minimalist approach, perfect counterpoint to Claude Code
   - **Value:** Shows approval-first can work, proves complexity isn't required
   - **Effort:** Low (simpler architecture, ~2 days)
   - **Already have it:** ✅ In repos

2. **Plandex** ⭐⭐⭐
   - **Why:** Unique scalability patterns for massive codebases
   - **Value:** Different problem space (millions of tokens), scale-first design
   - **Effort:** Medium (~3-4 days)
   - **Need to clone:** New system

#### Tier 2: Valuable Additions

3. **Cline** ⭐⭐
   - **Why:** Modern autonomous workflows, very popular
   - **Value:** VS Code integration insights, permission model
   - **Effort:** Medium (~3 days)
   - **Need to clone:** New system

4. **OpenCode** ⭐⭐
   - **Why:** Multi-provider abstraction (75+ providers)
   - **Value:** Provider flexibility patterns
   - **Effort:** Medium (~3 days, Go codebase)
   - **Already have it:** ✅ In repos

#### Tier 3: Monitoring (Too New)

5. **Kilo CLI** ⭐
   - **Why:** Just launched Feb 2026, model-agnostic
   - **Value:** Too new to assess architecture
   - **Action:** Monitor, revisit in 6 months

6. **Kimi Code** ⭐
   - **Why:** China's frontier AI, multimodal
   - **Value:** Interesting but regional optimization likely
   - **Action:** Monitor, revisit in 6 months

---

### Comparative Document Evolution

**Current:**
- `REVIEW-prompts-codex.md` ✅
- `REVIEW-prompts-gemini.md` ✅
- `REVIEW-prompts-claude-code.md` ✅
- `REVIEW-compare-three.md` ✅

**Recommended Next:**
- `REVIEW-aider-prompts.md` (validates minimalism)
- `REVIEW-plandex-prompts.md` (scale patterns)
- `REVIEW-compare-five.md` (updated comparison)

**Optional Later:**
- `REVIEW-cline-prompts.md` (modern autonomy)
- `REVIEW-opencode-prompts.md` (multi-provider)

---

### Specific Takeaways for co-cli

#### 1. From Aider (When Reviewed)
- ✅ Validation that approval-first works
- ✅ Git-centric workflow patterns
- ✅ Simplicity as a feature, not a bug
- ⚠️ May lack sophistication for complex tasks

#### 2. From Plandex (When Reviewed)
- ✅ Scale-first architecture patterns
- ✅ Handling millions of tokens efficiently
- ✅ Multi-module coordination
- ⚠️ May be overkill for smaller projects

#### 3. From Multi-Provider Trend (OpenCode, Kilo CLI)
- ✅ Provider abstraction is valuable
- ✅ BYOK (bring your own key) empowers users
- ✅ Local model support for privacy
- ✅ Cost optimization through provider choice

#### 4. From Prompt Architecture Diversity
- ✅ No single "right" architecture exists
- ✅ Minimalist (Aider) vs Complex (Claude Code) both work
- ✅ Choose based on team capacity and target users
- ⚠️ Don't over-engineer if simple solves the problem

#### 5. From Terminal Resurgence
- ✅ CLI-first is back in fashion
- ✅ Fast, keyboard-driven workflows valued
- ✅ Git-native integration critical
- ✅ Lower cognitive load than IDE

---

### Decision Framework for co-cli

| Question | If Yes → | If No → |
|----------|----------|---------|
| **Small team (<5)?** | Choose minimalist (Aider-inspired) | Consider modular (Gemini CLI-inspired) |
| **Security-critical?** | Choose policy fragments (Codex-inspired) | Standard security checks sufficient |
| **Need plugins?** | Consider hooks (Claude Code-inspired) | Skip plugin complexity |
| **Handle 1M+ LOC?** | Study Plandex patterns | Standard context handling OK |
| **Multi-provider?** | Study OpenCode/Kilo CLI | Optimize for Gemini |
| **Autonomous workflows?** | Study Cline/Kimi Code | Approval-first (Aider) |

---

### Recommended Architecture for co-cli (Updated)

**Phase 1: MVP (Gemini CLI-inspired)**
- Single generator with conditional rendering
- Directive vs Inquiry distinction
- Type-safe options dataclass
- ~500-1000 lines total

**Phase 2: Security (Codex-inspired)**
- Policy fragments in separate files
- Pre-approved command prefixes
- Sandbox mode declarations

**Phase 3: Extensibility (Claude Code-inspired, SELECTIVE)**
- Event hooks (SessionStart, PreToolUse, PostToolUse)
- Security pattern detection
- Rule-based validation
- ❌ Skip full plugin architecture (too complex)

**Phase 4: Scale (Plandex-inspired, IF NEEDED)**
- Context window optimization
- Multi-module coordination
- Only if targeting enterprise/monorepos

---

## Conclusion

### The 2026 Agentic CLI Landscape is Diverse

**Key Insight:** There is no convergence on a single architecture. Instead, multiple paradigms coexist successfully:

| Paradigm | Champion | Complexity | Philosophy | When to Choose |
|----------|----------|-----------|------------|----------------|
| **Minimalist** | Aider | Low (~500 lines) | Approval-first | Small teams, simple workflows |
| **Modular** | Gemini CLI | Medium (~3.5K lines) | Clarity-first | Balanced teams, maintainability focus |
| **Policy-Based** | Codex | High (~2.2K lines) | Security-first | Compliance-critical environments |
| **Plugin-Based** | Claude Code | Very High (~182K lines) | Extensibility-first | Large teams, diverse use cases |
| **Scale-First** | Plandex | Medium-High | Handle massive repos | Enterprise monorepos |

### For co-cli: Start Simple, Add Complexity Only When Needed

1. **Phase 1:** Gemini CLI-style generator (medium complexity)
2. **Phase 2:** Codex-style security (policy fragments)
3. **Phase 3:** Claude Code-style hooks (selective adoption)
4. **Phase 4:** Plandex-style scale (only if targeting enterprise)

**Don't over-engineer.** Aider proves that simplicity works for many use cases.

### The Winner: Context-Dependent

- **For Git-heavy workflows:** Aider
- **For massive codebases:** Plandex or Windsurf
- **For deep reasoning:** Claude Code
- **For security:** Codex
- **For simplicity:** Gemini CLI
- **For privacy:** OpenCode (local models)
- **For flexibility:** Kilo CLI (model-agnostic)

**There is no universal "best"** — only best-for-context.

---

## Sources

### Primary Research Articles
- [Best AI Coding Agents for 2026](https://www.faros.ai/blog/best-ai-coding-agents-2026) - Faros AI
- [Top 5 CLI Coding Agents in 2026](https://pinggy.io/blog/top_cli_based_ai_coding_agents/) - Pinggy
- [Best AI Coding Assistants as of February 2026](https://www.shakudo.io/blog/best-ai-coding-assistants) - Shakudo
- [5 Best AI Agents for Coding in 2026](https://www.index.dev/blog/ai-agents-for-coding) - Index.dev

### Comparative Analysis
- [Top 5 Agentic Coding CLI Tools](https://www.kdnuggets.com/top-5-agentic-coding-cli-tools) - KDnuggets
- [Agentic CLI Tools Compared: Claude Code vs Cline vs Aider](https://aimultiple.com/agentic-cli) - AIMultiple
- [The 2026 Guide to Coding CLI Tools: 15 AI Agents Compared](https://www.tembo.io/blog/coding-cli-tools-comparison) - Tembo
- [AI Coding Tools in 2025: Welcome to the Agentic CLI Era](https://thenewstack.io/ai-coding-tools-in-2025-welcome-to-the-agentic-cli-era/) - The New Stack

### Specific System Reviews
- [Cline Review (2026): Autonomous AI Coding Agent for VS Code](https://vibecoding.app/blog/cline-review-2026) - VibeCoding
- [Windsurf vs Cursor: A Comparison With Examples](https://www.datacamp.com/blog/windsurf-vs-cursor) - DataCamp
- [Cursor vs Windsurf (2026): The Definitive Comparison](https://vibecoding.app/blog/cursor-vs-windsurf) - VibeCoding
- [Best AI Code Editor: Cursor vs Windsurf vs Replit in 2026](https://research.aimultiple.com/ai-code-editor/) - AIMultiple

### Open Source & GitHub
- [Top 10 Open-Source CLI Coding Agents You Should Be Using in 2025](https://dev.to/forgecode/top-10-open-source-cli-coding-agents-you-should-be-using-in-2025-with-links-244m) - DEV Community
- [The Top Ten GitHub Agentic AI Repositories in 2025](https://opendatascience.com/the-top-ten-github-agentic-ai-repositories-in-2025/) - Open Data Science
- [GitHub - cline/cline](https://github.com/cline/cline) - Cline Repository
- [GitHub - google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) - Gemini CLI Repository

### New Launches (2026)
- [Getting Started with Moonshot AI's Kimi Code](https://dev.to/james_miller_8dc58a89cb9e/getting-started-with-moonshot-ais-kimi-code-an-autonomous-coding-agent-for-your-terminal-5ei9) - DEV Community
- [Kilo CLI Release reshapes terminal coding workflows](https://www.aicerts.ai/news/kilo-cli-release-reshapes-terminal-coding-workflows/) - AI CERTs News
- [Meet your new AI coding teammate: Gemini CLI GitHub Actions](https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemini-cli-github-actions/) - Google Blog

### Official Documentation
- [Cline Bot](https://cline.bot/) - Official Cline Site
- [Goose by Block](https://block.github.io/goose/) - Official Goose Site

---

**End of Agentic CLI Landscape 2026 Review**
