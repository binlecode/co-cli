# Implementation Plan: GitHub Workflow Skill

## Context & Objectives
We are integrating GitHub deeply into `co-cli` as a native **Skill**. Instead of writing a Python-based tool with Zod schemas or spinning up an MCP server, we are leveraging `co-cli`'s existing `bash` tool combined with the official GitHub CLI (`gh`). 

This approach delegates authentication entirely to the user's local environment, avoids maintaining massive API schemas, and protects the LLM's context window through native `co-cli` file persistence for large outputs.

## Core Processing Logic

The skill operates entirely through prompt-injection when loaded. It will instruct the agent on *how* to interact with GitHub safely and efficiently.

1. **Authentication:** The agent assumes the user is authenticated via `gh auth login`. It does not attempt to manage tokens.
2. **Tool Routing:** The agent is instructed to **only** use the `bash` tool for GitHub operations, invoking `gh`.
3. **Context Management (JSON & jq):** To prevent context window bloat when fetching PRs or issues, the skill mandates the use of `--json` and `--jq` flags to filter out noise before the output is returned to the agent.
4. **Non-Interactive Execution:** The skill explicitly forbids interactive commands (e.g., `gh pr create` without arguments, which hangs waiting for `vim`). All commands must be fully parameterized.
5. **Escape Hatch:** If a specific `gh` sub-command doesn't exist, the skill teaches the agent to fall back to `gh api <endpoint>`.

## Tasks

- [ ] **1. Create Skill Directory**
  - Create the directory: `.claude/skills/github-workflow`

- [ ] **2. Write `SKILL.md` Payload**
  - Create `.claude/skills/github-workflow/SKILL.md` with the following YAML frontmatter:
    ```yaml
    ---
    name: github-workflow
    description: Advanced GitHub workflows for PR reviews, issue triage, and CI/CD management via the gh CLI
    ---
    ```
  - **Define Constraints & Rules**:
    - "Never ask for a GitHub token. Assume `gh` is authenticated."
    - "Never use interactive commands. Always pass `--body`, `--title`, or `--fill`."
    - "Always use `--json` and `--jq` to restrict output fields to only what is necessary."
  - **Define Workflows (Recipes)**:
    - *Pull Requests*: `gh pr view <number> --json title,body,state,comments`
    - *Issues*: `gh issue list --state open --limit 10 --json number,title`
    - *CI/CD Actions*: `gh run list --limit 5` and `gh run view <id> --log-failed`
    - *Code Search*: `gh search code "<query>" --json path,textMatches`
    - *API Fallback*: `gh api repos/{owner}/{repo}/...`

- [ ] **3. Add Safety & Destructive Action Guardrails**
  - Add explicit rules in the prompt that require the agent to use the `question` tool or ask for user confirmation in chat before executing destructive actions like:
    - `gh repo delete`
    - `gh pr close`
    - `gh issue close`
    - Any `gh api -X DELETE` or `POST` commands

- [ ] **4. Test & Validate**
  - Run `uv run co chat`.
  - Type `/skills reload` to ensure the skill is picked up from `.claude/skills/`.
  - Type `/skills check` to verify `github-workflow` is active.
  - Test the skill by asking: "Use the github-workflow skill to list the last 3 open PRs in this repository."
  - Verify that the agent uses `bash` with `gh pr list --limit 3` and does not hang on interactive prompts.

## Delivery Verification
- [ ] No Python code was written or modified.
- [ ] The `SKILL.md` file is well-formed with valid YAML frontmatter.
- [ ] The skill successfully filters `gh` output using `jq`.
