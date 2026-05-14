---
description: Install a skill from an HTTPS URL or local path — validate the source, invoke skill_manage(action='install'), and verify the installed body.
argument-hint: <source-url-or-path>
user-invocable: true
---

# Skill Installer

**Invocation:** `/skill-installer <source>`

Install a skill from a `.md` source — HTTPS URL or local file path. Validates the source before fetching, delegates the write and security scan to `skill_manage(action='install')`, and confirms the result.

---

## Phase 1 — Validate Source

Check the source before any network or file access:

- **HTTPS URL:** scheme must be `https://`. Verify the domain looks trustworthy — a known repository, official docs site, or a source the user explicitly provided. Reject `http://` (unencrypted).
- **Local path:** must end in `.md`. Confirm the path is absolute or clearly relative to the project root.
- **Anything else:** reject and explain why.

If the source is an unfamiliar domain or a path pointing outside the workspace, ask the user to confirm before continuing.

## Phase 2 — Install

Call `skill_manage(action='install', source=<source>)`.

The tool handles:
1. Fetch content.
2. Run `scan_skill_content` security gates (credential exfil, pipe-to-shell, destructive shell, prompt injection).
3. Validate frontmatter — `description` required, ≤1024 chars.
4. Reject name collisions with existing user skills.
5. Atomic write and reload.

On security-scan failure the write is auto-rolled back. Do not retry the same source.

## Phase 3 — Verify

After successful install:

1. Call `skill_view(<installed-name>)` and confirm the body matches what you expected.
2. Run `/skills lint <name>` to check authoring conformance (R1–R10).
3. Report any lint findings to the user — they don't block use but signal authoring gaps.

## Rules

- Never install over an existing skill — use `skill_manage(action='edit')` to update.
- HTTPS only for remote sources — reject `http://`.
- If the security scan flags content, stop and report the matched patterns; don't retry the same source.
- Lint findings after install are informational — surface them, don't suppress.
