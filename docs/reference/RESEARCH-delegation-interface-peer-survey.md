# RESEARCH — Delegation interface peer survey (tool surface, schema, prompting)

**Status:** research / design-driver. Not linked from specs. Permanent reference.
**Date:** 2026-06-27.
**Scope:** how five peers expose *delegation* to the model — the delegate tool's **surface** (is it a tool at all?), its **API contract schema**, its **description text**, and where the **when/why-to-delegate prompting** lives (base prompt vs tool description). Drives co's post-Phase-3.6 delegation refactoring + enhancement.
**Companion:** `RESEARCH-loop-decoupling-peer-survey.md` (§"Subagent engagement") established *agent-as-tool* convergence and the four sub-axes (surface, approval, depth, return). This doc goes one level closer on the **interface and prompt** specifically.
**Method:** code-first, file:line citations from the local peer clones in `~/workspace_genai/` (`hermes-agent`, `codex`, `openclaw`, `opencode`, `fork-claude-code`). Verbatim where wording matters.

---

## 0. Why this survey

co shipped Phase 3.6: the delegated agent is a **full agent** with the orchestrator's visibility surface minus `{delegate}`, reached by a single **tool-agnostic** `delegate(task: str)` call (`co_cli/tools/system/delegate.py`, `co_cli/agent/delegation.py`). Three questions surfaced during review that the 3.6 plan did not settle:

1. Should delegation be a model-facing tool at all, or an internal impl of specific tools? (Phase-3.6 review challenge.)
2. How does the model *package* a task vs. picking a normal tool?
3. Is co's delegate interface (anonymous generalist, free-form task only) convergent with the field, or an outlier?

This survey answers all three from peer code and converts the findings into a refactoring backlog.

---

## 1. Is delegation a model-facing tool? — 5/5 YES

Every peer with delegation exposes a **generic, model-callable delegation primitive** the LLM invokes with a free-form task. None hide delegation entirely behind specific domain tools. (Verbatim registrations confirmed.)

| Peer | Tool name | Registration | Free-form task arg |
|------|-----------|--------------|---------------------|
| hermes | `delegate_task` | `tools/delegate_tool.py:3179`; toolset `toolsets.py:238` | `goal` (`:3047`) |
| codex | `spawn_agent` | `tools/spec_plan.rs:768`; handler `multi_agents_v2/spawn.rs` | `message` (`multi_agents_spec.rs:598`) |
| openclaw | `sessions_spawn` | `openclaw-tools.ts:516`; tool `sessions-spawn-tool.ts:276` | `task` (`:168`) |
| opencode | `task` | `tool/registry.ts:228`; `tool/task.ts:24` | `prompt` (`task.ts:45`) |
| claude-code | `agent` ("delegate work to a subagent") | `tools.ts`; `tools/AgentTool/AgentTool.tsx:226` | `prompt` (`:84`) |
| **co** | `delegate` | `tools/system/delegate.py:16` | `task` |

**Secondary pattern (coexists, never replaces):** some peers also have *domain tools that run an agent internally* without exposing "delegate" — claude-code's `skill` tool runs an agent via `runAgent()` (`SkillTool.ts:62`); codex's `spawn_agents_on_csv` is a CSV-batch tool that internally fans out agents (`spec_plan.rs:846`). This is an *additional* surface, not a substitute for the generic delegate tool.

**Conclusion for co:** keeping `delegate` as a model-facing tool is correct and convergent. The "delegation should not surface as a tool" hypothesis has zero peer support. The live design question is the *shape* of that tool, not its existence.

---

## 2. Tool description — convergent content

The delegate tool's model-facing **description** carries a strikingly consistent set of instructions. Full per-peer matrix (✓ = stated in the tool description or — for openclaw/opencode/claude-code — the base-prompt delegation section; `~` = partial/conditional; `Σ` = count of 5):

| # | Description theme | hermes | codex | openclaw | opencode | claude-code | Σ | co |
|---|-------------------|:------:|:-----:|:--------:|:--------:|:-----------:|:-:|:--:|
| **D1** | Isolated context; **only the summary/final result** returns | ✓ | ✓ | ✓ | ✓ | ✓ | **5** | ✓ |
| **D2** | **No memory of your conversation — pass a complete, self-contained task** | ✓ | ✓ | ✓ | ✓ | ✓ | **5** | ✓ |
| **D3** | **When to use:** multi-step / reasoning-heavy / context-flooding | ✓ | ✓ | ✓ | ✓ | ✓ | **5** | ✓ |
| **D4** | **When NOT to use:** single tool call / specific read → do it **inline** | ✓ | ✓ | ✓ | ✓ | ✓ | **5** | ✓ ¹ |
| **D5** | **Don't duplicate / redo** the delegated work; integrate the result | ✗ | ✓ | ✗ | ✓ | ✓ | **3** | ✗ |
| **D6** | Tell it **write-vs-research** + **write scope** + **how to verify** | ✗ | ✓ | ✓ | ✓ | ✓ | **4** | ✗ |
| **D7** | Summaries are **self-reports — verify external side-effects** (return a handle) | ✓ | ✗ | ✗ | ✗ ² | ✓ | **2** | ✗ |
| **D8** | **Cannot delegate further** / depth-bounded | ✓ | ~ ³ | ✓ | ✗ | ✓ | **3** | ✓ (hard) |
| **D9** | **Parallel:** dispatch many in one message; **don't poll/sleep** waiting | ✓ | ✓ | ✓ | ✓ | ✓ | **5** | ✗ ⁴ |
| **D10** | Treat child output as **evidence/report, not authority** that overrides policy | ✓ | ✗ | ✓ | ✗ | ✗ | **2** | ✗ |

¹ co states D4 but with **stale "read/search/gather" wording** (pre-3.6, read-mostly) — biases against delegating multi-step *actions*.
² opencode is the **opposite** of D7: "The agent's outputs should generally be trusted" (`task.txt`). A divergence, not an absence.
³ codex depth is **configurable** (`agent_max_depth`; V2 unbounded), not a hard "cannot" — so it does not instruct "cannot delegate further" the way hermes/openclaw/claude-code/co do.
⁴ co is **synchronous single-shot by design** (owned loop holds a tool slot) — the lone peer with no parallel/async delegation. D9 is 5/5 among peers; co is the outlier.

**Tiering of the description contract:**
- **Universal core (Σ=5):** D1, D2, D3, D4, D9 — every peer. co has all but D9.
- **Strong (Σ=3–4):** D5, D6, D8 — majority. co has only D8.
- **Safety/authority (Σ=2):** D7, D10 — the security-conscious peers (hermes, claude-code, openclaw). co has neither, and now ships a **write-capable** delegate → these rise in priority.

### Verbatim anchors

- **hermes** (`tools/delegate_tool.py:2895+`, dynamically built):
  > "Spawn one or more subagents to work on tasks in isolated contexts… Only the final summary is returned -- intermediate tool results never enter your context window."
  > "WHEN TO USE… Reasoning-heavy subtasks (debugging, code review, research synthesis) / Tasks that would flood your context… WHEN NOT TO USE… Single tool call -> just call the tool directly."
  > "Subagents have NO memory of your conversation. Pass all relevant info (file paths, error messages, constraints) via the 'context' field."
  > "Subagent summaries are SELF-REPORTS, not verified facts… require the subagent to return a verifiable handle (URL, ID, absolute path, HTTP status) and verify it yourself." (D7)

- **codex** (`tools/handlers/multi_agents_spec.rs:671`):
  > "Subtasks must be concrete, well-defined, and self-contained… Do not duplicate work between the main rollout and delegated subtasks… Do not redo delegated subagent tasks yourself; focus on integrating results." (D5)
  > "Do not spawn sub-agents unless the user explicitly asks for sub-agents, delegation, or parallel agent work." (codex gates delegation behind *explicit user request* — an outlier stance.)

- **opencode** (`tool/task.txt:1-20`):
  > "your prompt should contain a highly detailed task description for the agent to perform autonomously and you should specify exactly what information the agent should return."
  > "Clearly tell the agent whether you expect it to write code or just to do research… Tell it how to verify its work if possible." (D6)

- **claude-code** (`tools/AgentTool/prompt.ts:255+`):
  > "The result returned by the agent is not visible to the user… send a text message back to the user with a concise summary."
  > Verification contract (`constants/prompts.ts:390`): "independent adversarial verification must happen before you report completion… only the verifier assigns a verdict." (D6/D7, strongest form.)

- **co today** (`tools/system/delegate.py:22`): hits D1–D4 + D8; missing D5, D6, D7, D9.

---

## 3. API contract — schema convergence

Field names differ; *roles* converge. Full per-peer matrix — each cell is the **actual field name** in that peer (or ✗ absent); **bold** = required; `Σ` = count of 5.

| Field role | hermes | codex | openclaw | opencode | claude-code | Σ | co |
|------------|--------|-------|----------|----------|-------------|:-:|----|
| **Free-form task** (the core) | **`goal`** ¹ | **`message`** | **`task`** | **`prompt`** | **`prompt`** | **5** | **`task`** |
| **Named-agent / subagent selector** | ✗ | `agent_type` | `agentId` | **`subagent_type`** | `subagent_type` | **4** | ✗ |
| **Short description / label** | ✗ | ✗ | `label` | `description` | `description` | **3** | ✗ |
| **Background / async** (as a param) | `background` ² | — ³ | `mode=run\|session` | `background` | `run_in_background` | **4** | ✗ |
| **Model override** | ✗ | `model` | `model` | ✗ | `model` | **3** | ✗ |
| **Stable handle / name** | ✗ | **`task_name`** | `taskName` | `task_id` ⁴ | `name` | **4** | ✗ |
| **Context / fork control** | `context` | `fork_turns` | `context`,`lightContext` | ✗ | ✗ ⁵ | **3** | ✗ |
| **Per-call tool / scope** | `toolsets` | ✗ | ✗ | ✗ | ✗ | **1** | ✗ |
| **Role / depth** | `role=leaf\|orchestrator` | ✗ ⁶ | ✗ | ✗ | ✗ | **1** | ✗ (hard 1) |
| **Batch / multi-task** | `tasks[]` | ✗ ⁷ | ✗ | ✗ | ✗ | **1** | ✗ |
| **Peer-specific extras** | `acp_command`,`acp_args` | `reasoning_effort`,`service_tier` | `sandbox`,`cwd`,`thread`,`attachments`,`streamTo` | `command` | `isolation`(worktree/remote),`cwd`,`name`,`team_name`,`mode` | — | — |

¹ hermes also accepts `tasks[]` (batch) as an alternative to `goal`; exactly one of `goal`/`tasks` is required.
² hermes `background` is **deprecated/ignored** — single delegations are *always* background.
³ codex spawns are async by construction (separate `wait_agent`/`followup_task` tools), so there is no spawn-time async flag.
⁴ opencode `task_id` is a **resume** handle (continue a prior subagent session), not a fresh-spawn name.
⁵ claude-code's "fork" mode (omit `subagent_type`) inherits the **full** parent context by design — the opposite of co's isolated fork.
⁶ codex depth is **config** (`agent_max_depth`), not a per-call param.
⁷ codex achieves parallelism by emitting multiple `spawn_agent` calls in one turn, not a batch array.

**The irreducible converged contract is two fields:** (1) a **required free-form task string** — 5/5, universal; and (2) a **named-agent/subagent selector** — 4/5. co has (1); **co and hermes are the only two without (2)** (hermes substitutes `toolsets`+`role`). Everything below Σ=3 is peer-idiosyncratic richness, not a contract.

**Read against the description matrix (§2):** the schema selector (named-agent, Σ=4) is the structural twin of description themes D6 ("tell it write-vs-research") — peers that let the model *pick a role* also lean less on prose telling the agent what mode to be in, because the role *is* the mode (codex `explorer` vs `worker`). co, lacking the selector, would have to carry that entirely in D6 prose or adopt the selector.

### How the named-agent set is surfaced to the model (the 4 that have it)

- **codex** — `agent_type` description built from a **role registry** (`agent/role.rs:217`): `"Available roles:\n{roles}"`; built-ins `default`, `explorer`, `worker` (`:310-368`), each with a multi-line behavioral brief; user roles via TOML. Locked model/effort per role.
- **opencode** — `subagent_type` **required**, validated against an agent registry at `task.ts:116`; available names **not** enumerated in the schema (model learns them from agent descriptions / context).
- **openclaw** — `agentId` free-string; a **companion `agents_list` tool** (`agents-list-tool.ts:43`) lets the model discover allowed agents at runtime.
- **claude-code** — `subagent_type` optional; available agents **enumerated inline in the tool description** via `formatAgentLine`: `"- {agentType}: {whenToUse} (Tools: {tools})"` (`prompt.ts:43`), or injected as a `<system-reminder>` (feature-gated). Built-ins: general-purpose, explore, plan, verification, statusline-setup; plus `.claude/agents/` user agents.

**Two surfacing models:** (a) **enumerate-in-description** (codex, claude-code) — the role list + when-to-use rides the prompt; (b) **discover-via-tool** (openclaw `agents_list`) or **validate-silently** (opencode). For co's small-model + deferred-tier budget, (a) is the most legible but costs prefill; a co-native option is a deferred `agents_list`-style discovery tool.

---

## 4. Where the when/why prompting lives — base prompt vs tool description

| Peer | Location of WHEN/WHY-to-delegate guidance | In base prompt? | Tool-agnostic? |
|------|-------------------------------------------|-----------------|----------------|
| openclaw | `## Sub-Agent Delegation` section, `system-prompt.ts:102-118` (gated on delegation mode `prefer`) | **Yes** | No — names `sessions_spawn` |
| opencode | `anthropic.txt:79-86` ("prefer the Task tool… parallelize") | **Yes** | No — names "Task tool" |
| claude-code | `constants/prompts.ts:316-395` (fork semantics, verification contract) | **Yes** | No — names `AGENT_TOOL_NAME` |
| hermes | Tool description only (`_build_top_level_description`); base prompt has only a Kanban anti-pattern note | No | — |
| codex | Tool description only (`multi_agents_spec.rs:671`); `gpt_5_codex_prompt.md` has none | No | — |
| **co** | `DELEGATE_GUIDANCE` in toolset guidance, injected into the floor when `delegate` present (`context/guidance.py:35`) | **Yes** (base-prompt camp) | No — names `delegate` |

**Findings:**
- **Majority (3/5) treat when/why-to-delegate as a first-class base-prompt concern**, not buried in the tool schema. co is already in this camp.
- **No peer writes truly tool-agnostic delegation prose** — the guidance always names the delegation tool. "Tool-and-agent-agnostic base-prompt delegation guidance" is not a real pattern in the field; the realistic target is "base-prompt guidance that names the one delegate tool."
- **Placement is the same concern as content:** because co's guidance is tool-presence-gated floor text, it auto-drops when `delegate` is absent (e.g. inside the delegated agent, which is blocklisted from `delegate`) — a correct property to preserve.

---

## 5. co current state + gap analysis

**Aligned / convergent (keep):**
- Delegation *is* a model-facing tool (§1). ✓
- Free-form `task` core param (§3, the universal). ✓
- Description covers D1–D4 + D8 (§2). ✓
- When/why guidance in the base-prompt floor, tool-presence-gated (§4). ✓
- Context isolation, summary-only return, approval propagation, hard depth cap — established in `RESEARCH-loop-decoupling-peer-survey.md`. ✓

**Gaps (drive the refactor):**

| Gap | Evidence | Severity | Why it matters for co specifically |
|-----|----------|----------|------------------------------------|
| **G-A: No named-agent-type selector** | 4/5 peers have one (§3) | High (structural) | co delegates to one *anonymous full-surface generalist*. A small model benefits from a named specialist that narrows behavior + tool focus. This is the single most convergent element co lacks. |
| **G-B: Description missing D5/D6/D7** | §2 | Medium (prose) | co's delegate is now **write-capable** (3.5/3.6). "Verify external side-effects / summaries are self-reports" (D7) and "say write-vs-research + how to verify" (D6) matter *more* for a write-capable agent than for the old read-mostly one. "Don't redo the work" (D5) prevents the orchestrator re-doing a delegated multi-step task. |
| **G-C: Stale D4 wording** | `DELEGATE_GUIDANCE` + docstring say "read/search/gather" | Low (prose) | Biases the model away from delegating multi-step *actions* (the new sweet spot post-3.6). Already flagged; floor-guard-sensitive edit. |
| **G-D: No async/parallel** | **5/5** have parallel dispatch + don't-poll guidance (D9); 4/5 expose an async param (§3) | Deferred | co delegation is synchronous single-shot by design (owned-loop, holds a tool slot). co is the lone peer without parallel delegation. Async/parallel is a larger architectural change; out of immediate scope but recorded. |
| **G-E: No model override / no per-call scope** | 3/5 `model`; 1/5 `toolsets` | Low | co inherits parent model + full surface deliberately. Likely *keep* (co's "full agent" principle); record as a conscious divergence, not a gap to close. |

---

## 6. Design directions (refactor + enhancement backlog)

Ordered by ROI. Each is a candidate for its own plan; (R1) and (R2) are independent and small.

### R1 — Description content refresh (close G-B, G-C) — prose-only, do first
Update `DELEGATE_GUIDANCE` (`context/guidance.py`) **and** the `delegate` docstring (`tools/system/delegate.py`) to:
- Replace "read/search/gather" with "multi-step subtask (read **or act** — research, edits, shell sequences) whose intermediate results you won't need to retain" (G-C).
- Add D5: "Don't redo a delegated subtask yourself; integrate its summary."
- Add D6: "State in the task whether the sub-agent should just research or also make changes, and how to verify."
- Add D7 (load-bearing now that delegation writes): "The summary is the sub-agent's self-report. For external side-effects (writes, sends, publishes), have it return a verifiable handle (path / URL / id) and verify before relying on it."
- Keep it tool-presence-gated and floor-budget-aware; run the instruction-floor guards (budget ceiling + F5 no-deferred-signature) per `feedback_instruction_floor_guards_on_rule_edits`.

### R2 — Named-agent-type selector (close G-A) — the structural decision
The peer-dominant pattern (4/5). Decision sub-questions co must answer:

1. **Does co adopt it at all?** Tension with the just-shipped 3.6 principle ("delegated agent = anonymous full agent, tool-agnostic interface"). Counter: a *named role* is not a *tool grant* — it narrows the agent's **persona/behavior**, not its surface; the agent can still self-load any tool. The two are orthogonal. Recommended: **adopt**, framed as "role selection, surface unchanged."
2. **What is the registry?** co already has **skills** (procedural capability) and a knowledge-work positioning (`feedback_skill_curation_knowledge_work_positioning`). Two options:
   - **(a) Reuse skills as agent roles** — a delegated agent can be spawned "as" a skill, inheriting its instructions. Avoids a parallel registry; aligns with co's existing asset model.
   - **(b) New lightweight role registry** (codex `role.rs` model) — built-in roles (e.g. `researcher`, `editor`, `verifier`) + user roles, each a name + when-to-use + persona brief. Cleaner separation, but a second registry to maintain.
   - **Resolve the skills-vs-roles overlap before building** — this is the crux; do not ship a role registry that duplicates skills.
3. **How surfaced to the small model?** Prefer **enumerate-in-description** (codex/claude-code style, `"- {role}: {when-to-use}"`) for legibility, OR a deferred `delegate_roles`-style discovery tool to protect prefill (co deferred-tier precedent). Pick per budget measurement.
4. **Schema shape:** add optional `subagent_type: str` to `delegate(task, subagent_type=None)`; default = today's generalist (zero-regression). Keep `task` required and free-form (preserve the universal core, §3).

### R3 — Record conscious divergences (G-D, G-E)
Document in the delegation spec (`docs/specs/agents.md`) that co **deliberately** omits async/parallel delegation (synchronous owned-loop, holds a tool slot) and per-call model/scope overrides ("full agent inherits parent" principle), with the peer counts as context — so future reviewers see these as decisions, not oversights. Mirrors the loop-decoupling survey's "rejected-by-design" treatment.

### Non-goals (explicit)
- **Removing `delegate` as a tool** — zero peer support (§1); rejected.
- **Hiding delegation inside domain tools as the sole surface** — secondary pattern only (§1); not pursued as a replacement. (A skills-with-internal-agent path may emerge naturally from R2(a), but as an *addition*.)
- **Per-call `toolsets` allowlist** (hermes-only) — contradicts co's "full agent, gated by approval not by surface" principle (Phase 3.6); rejected.

---

## 7. Open questions

- **Q1 (R2 crux):** skills-as-roles vs a separate role registry — which serves co's knowledge-work positioning without duplicating the skills asset? Needs a skills/roles boundary decision before any impl.
- **Q2:** does a small model (qwen3.6:35b-a3b) actually delegate *better* with a named role than with a free-form task alone? Worth a focused eval before committing to R2 — the 4/5 convergence is frontier-model-derived; co's tier may differ.
- **Q3:** D7 (verify-side-effects) — is the right home the delegate description, the delegated-agent instructions, or the orchestrator's wrap-up? Possibly all three; place where the small model acts on it.
- **Q4:** `clarify` in the delegated agent (carried over from the 3.6 plan's Open Questions) — re-raise if delegated subtasks need to ask the user mid-task.

---

## Appendix — file:line index

| Peer | Tool reg | Tool description | Schema | Base-prompt guidance |
|------|----------|------------------|--------|----------------------|
| hermes | `tools/delegate_tool.py:3179` | `:2895-2951` (dynamic) | `:3027-3156` | — (none; Kanban note `:270`) |
| codex | `tools/spec_plan.rs:768` | `multi_agents_spec.rs:671-723` | `:595-635`; roles `agent/role.rs:217-368` | — (none) |
| openclaw | `openclaw-tools.ts:516` | `tool-description-presets.ts:40-76` | `sessions-spawn-tool.ts:162-241` | `system-prompt.ts:102-118` |
| opencode | `tool/registry.ts:228` | `tool/task.txt:1-20` | `tool/task.ts:43-62` | `session/prompt/anthropic.txt:79-86` |
| claude-code | `tools.ts` / `AgentTool.tsx:226` | `AgentTool/prompt.ts:66-287` | `AgentTool.tsx:82-102` | `constants/prompts.ts:316-395` |
| **co** | `tools/system/delegate.py:16` | `delegate.py:22` | `delegate.py:21` (`task: str`) | `context/guidance.py:20-36` |
