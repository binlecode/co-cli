---
description: Structured troubleshooting workflow — diagnose system health and identify degraded conditions
user-invocable: true
disable-model-invocation: false
---

Run `check_capabilities` to get the full runtime picture: capabilities, session state, findings, and active fallbacks.

Review the result against any prior context in this conversation (what the user was trying to do, what failed). Identify the most relevant degraded or blocking condition.

If more information is needed to diagnose, run one targeted read-only follow-up (e.g. `read_file` to inspect a credential or config path, `web_search` to look up a tool's requirements). Do not call `check_capabilities` a second time.

Respond with this exact structure:

**Likely issue:** What is wrong or degraded — be specific (e.g. "Gemini API key not set", "knowledge index offline — grep fallback active", "MCP server `notes` binary not found").

**What still works:** List capabilities that are functioning normally and relevant to the user's context.

**Active fallback:** Any degraded-mode operation currently in effect (from the `fallbacks` list). If none, say "none".

**What Co should do next:** One concrete next step — either a config fix the user can apply, or an alternative approach Co can take right now.

Keep the diagnosis concise and contextual. Doctor recommends — does not repair.
