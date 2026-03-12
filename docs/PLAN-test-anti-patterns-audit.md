# Plan Audit Log: Fix Test Anti-Patterns
_Slug: test-anti-patterns | Date: 2026-03-11_

---

## Cycle C1 — Team Lead
Submitting for Core Dev + PO parallel review.

## Cycle C1 — Core Dev
**Assessment:** revise
**Blocking:** CD-M-2, CD-M-3
**Summary:** TASK-1's `asyncio.wait_for()` API is correct. Two blocking issues: TASK-2's 180s outer timeout violates CLAUDE.md "never wrap multiple sequential awaits in one shared timeout block"; TASK-3's `/health` probe is unreliable for TEI (no documented health endpoint).

**Major issues:**
- **CD-M-2** [TASK-2]: Outer `asyncio.timeout(180)` wrapping `_trigger_shell_call` + entire while loop violates CLAUDE.md rule "Never wrap multiple sequential awaits or retry loops in one shared timeout block." The per-hop 60s timeouts are already in place. Recommendation: Replace outer timeout with an iteration cap (`max_hops = 5`) on the while loop. This bounds the loop without violating the timeout policy.
- **CD-M-3** [TASK-3]: TEI services expose `/embed` and `/rerank` only — no `/health` endpoint. The `_tei_embed_available()` helper will always fail and skip will always trigger. Recommendation: Use TCP socket probe `socket.create_connection(("127.0.0.1", 8283), timeout=1.0)` for embed; same on port 8282 for rerank. Socket probe is reliable and doesn't require a live HTTP handler.

**Minor issues:**
- **CD-m-1** [TASK-7]: `done_when` does not verify that the two new tests have timeouts applied. Recommendation: Add `grep -c "asyncio.wait_for" tests/test_memory_decay.py` returns ≥4 to done_when.
- **CD-m-5** [TASK-8]: `done_when` checks only `shell_git_status`. Recommendation: Also check `web_search_fastapi` to verify both parametrize decorators are updated.

## Cycle C1 — PO
**Assessment:** revise
**Blocking:** PO-M-1
**Summary:** Plan correctly targets all findings with tight scope. TASK-2 timeout inconsistency (FIX says 120s, plan says 180s) is resolved by adopting CD-M-2 (iteration cap). One remaining concern: the outer timeout values need to be grounded in clear reasoning, not padding arithmetic.

**Major issues:**
- **PO-M-1** [TASK-2]: 180s outer timeout is inconsistent with FIX-C2 reasoning (120s) and is moot after adopting CD-M-2. Resolved if CD-M-2 is adopted (iteration cap replaces the timeout). Recommendation: Adopt CD-M-2 fully — this resolves PO-M-1 simultaneously.

**Minor issues:**
- **PO-m-1** [TASK-5]: 130s justification ("2 retries × 60s + 10s overhead") is clear and correct. No change needed.
- **PO-m-2** [TASK-1]: Replacement module docstring is verbose. Recommend one-liner: "Functional tests for memory decay mechanics. Requires a running LLM provider."

## Cycle C1 — Team Lead Decisions

| Issue ID | Decision | Rationale |
|----------|----------|-----------|
| CD-M-1   | reject   | `asyncio.run(asyncio.wait_for(coro, timeout=60))` is valid — `asyncio.wait_for()` returns a coroutine, `asyncio.run()` runs it. API is correct. |
| CD-M-2   | adopt    | Replace outer `asyncio.timeout(180)` with `max_hops = 5` iteration cap. Avoids policy violation; per-hop 60s timeouts already bound each step. |
| CD-M-3   | adopt    | Use `socket.create_connection(("127.0.0.1", PORT), timeout=1.0)` in both helpers. `/health` is unreliable for TEI. |
| CD-m-1   | adopt    | Strengthen TASK-7 done_when with grep for timeout count. |
| CD-m-5   | adopt    | Add `web_search_fastapi` check to TASK-8 done_when. |
| PO-M-1   | resolved | Moot after CD-M-2 adoption — iteration cap replaces the 180s outer timeout. |
| PO-m-1   | reject   | 130s for TASK-5 is correct per reasoning; TASK-2 now uses iteration cap, no consistency issue. |
| PO-m-2   | adopt    | Shorten TASK-1 docstring to one-liner. |
