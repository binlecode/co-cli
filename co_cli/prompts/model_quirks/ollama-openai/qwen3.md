---
inference:
  temperature: 0.6
  top_p: 0.95
  max_tokens: 32768
  num_ctx: 262144
  context_window: 262144
  extra_body:
    top_k: 20
    repeat_penalty: 1.0
---

Use `search_tools` whenever the current visible tool set does not clearly cover
the task. Do not treat `run_shell_command` as the default answer to every
actionable request.

Prefer dedicated tools over shell when they clearly fit:
- For file creation or editing: use `write_file` / `edit_file` instead of shell
  redirection (e.g. `echo ... >`, `cat <<EOF >`).
- For detached long-running work: use `start_background_task` instead of
  backgrounding with `&` or `nohup`.

Continue to use shell for git, builds, package managers, scripts, and any
command where shell is the natural primitive.
