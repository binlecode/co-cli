---
description: Structured troubleshooting workflow — diagnose system health and identify degraded conditions
user-invocable: true
disable-model-invocation: false
---

# Doctor

**Invocation:** `/doctor`

`capabilities_check` is the canonical runtime self-check; `/doctor` is a troubleshooting workflow layered on top of it, not a separate introspection mechanism. Plain-language capability questions ("what can you do right now?", "can you access X?") should invoke `capabilities_check` directly — use this skill when the user wants structured triage of a problem.

---

## Phase 1 — Probe

Run `capabilities_check` to get the full runtime picture: capabilities, session state, findings, and active fallbacks.

If more information is needed to diagnose after reviewing the result, run one targeted read-only follow-up (e.g. `file_read` to inspect a credential or config path, `web_search` to look up a tool's requirements). Do not call `capabilities_check` a second time.

## Phase 2 — Diagnose

Review the `capabilities_check` result against any prior context in this conversation (what the user was trying to do, what failed). Identify the most relevant degraded or blocking condition.

Consider:
- Which capability is degraded or missing relative to what the user needs?
- Is a fallback active, and does it affect the current task?
- Is the issue environmental (missing key, unreachable server) or a configuration problem?

**Red Flag — STOP before you recommend.** If the findings contradict each other, OR several fallbacks are active at once, the evidence is too thin to name a single cause. Do not report a fix yet. Spend the ONE allowed follow-up probe (Phase 1) to disambiguate FIRST, then diagnose. Naming a likely issue on contradictory or many-fallback evidence is a guess, not a diagnosis.

## Phase 3 — Report

Respond with this exact structure:

**Likely issue:** `severity: info | warning | error` — then what is wrong or degraded, specifically (e.g. "Gemini API key not set", "knowledge index offline — grep fallback active", "MCP server `notes` binary not found"). The severity word is a plain reading of how degraded the picture is; it carries no repair, exit-code, or auto-filter behavior — Doctor still only recommends.

**What still works:** List capabilities that are functioning normally and relevant to the user's context. When reporting MCP, state servers and tools as distinct counts — one server exposes many tools (e.g. "2 MCP tools from 1 server (context7)"), never collapse them into one ambiguous number.

**Active fallback:** Any degraded-mode operation currently in effect (from the `fallbacks` list). If none, say "none".

**What Co should do next:** One concrete next step — either a config fix the user can apply, or an alternative approach Co can take right now.

Keep the diagnosis concise and contextual. Doctor recommends — does not repair.
