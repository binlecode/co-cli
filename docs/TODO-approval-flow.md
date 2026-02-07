# TODO: Approval Flow — Remaining Items

Core migration to `requires_approval=True` + `DeferredToolRequests` is complete. See `docs/DESIGN-co-cli.md` §3.2, §5, §8.2 for architecture.

---

## Remaining

- [ ] **Functional test**: `uv run co chat` → trigger shell, email, slack tools → verify `[y/n/a(yolo)]` prompt appears, yolo mode auto-approves, denial sends `ToolDenied` to LLM
- [ ] **Conditional approval for shell**: Convert `run_shell_command` from blanket `requires_approval=True` to conditional `ApprovalRequired` — auto-approve safe read-only commands (`ls`, `cat`, `head`, `tail`, `pwd`, `echo`, `date`, `whoami`, `wc`), prompt for everything else. Uses `ctx.tool_call_approved` + `raise ApprovalRequired(metadata={"cmd": cmd})`. Register without `requires_approval` flag (approval is conditional inside the tool).
