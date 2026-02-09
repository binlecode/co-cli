# TODO: Web Tool Hardening (MVP Trim)

**Goal:** remove policy ambiguity with one web permission system, shipped in the smallest safe change.

## Scope (MVP Only)

In scope:

- Replace `web_permission_mode` with one `web_policy` config object.
- Support per-tool decision without a rule engine.
- Keep existing web security controls unchanged (SSRF/content-type/domain checks).

Out of scope (follow-up TODO):

- `recency_days`
- pagination tokens
- richer search metadata (`published_date`, `total_estimated`)
- generic rule lists / precedence resolver

---

## Target Config (Simple)

Add to `Settings`:

```python
class WebPolicy(BaseModel):
    search: Literal["allow", "ask", "deny"] = "allow"
    fetch: Literal["allow", "ask", "deny"] = "allow"
```

```python
web_policy: WebPolicy = Field(default_factory=WebPolicy)
```

Why this shape:

- one policy system
- explicit behavior per tool
- no matching engine, no precedence rules, no ambiguity

---

## Refactor Plan

### 1) Config and deps

- [x] Add `WebPolicy` model in `co_cli/config.py`.
- [x] Add `web_policy` field to `Settings`.
- [x] Remove `web_permission_mode` from `Settings`.
- [x] Add `web_policy` to `CoDeps`; remove `web_permission_mode`.

Files:

- `co_cli/config.py`
- `co_cli/deps.py`
- `settings.reference.json`

### 2) Runtime wiring

- [x] Inject `settings.web_policy` in `create_deps()`.
- [x] Update agent tool registration:
  - `web_search` requires approval when `web_policy.search == "ask"`
  - `web_fetch` requires approval when `web_policy.fetch == "ask"`

Files:

- `co_cli/main.py`
- `co_cli/agent.py`

### 3) Tool enforcement

- [x] In `web_search`, deny when `ctx.deps.web_policy.search == "deny"`.
- [x] In `web_fetch`, deny when `ctx.deps.web_policy.fetch == "deny"`.
- [x] Keep all existing fetch hardening logic untouched.

Files:

- `co_cli/tools/web.py`

### 4) Tests

- [x] Config parse test for `web_policy`.
- [x] `web_search` deny test via `web_policy.search = "deny"`.
- [x] `web_fetch` deny test via `web_policy.fetch = "deny"`.
- [x] Agent registration test for `ask` mode on each tool.

Files:

- `tests/test_config.py`
- `tests/test_web.py`
- `tests/test_agent.py`

### 5) Docs

- [x] Update design doc to reflect single policy system.
- [x] Add migration note in changelog (old `web_permission_mode` removed).

Files:

- `docs/DESIGN-12-tool-web-search.md`
- `CHANGELOG.md`

---

## Acceptance Criteria

- [x] No `web_permission_mode` in runtime/config.
- [x] One policy object controls both web tools.
- [x] `ask` behavior works per tool at registration time.
- [x] `deny` behavior works per tool at execution time.
- [x] Existing web fetch security tests still pass.
