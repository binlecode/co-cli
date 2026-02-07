# TODO: Conversation Memory Improvements

Currently `message_history` is unbounded and in-process only. See `docs/DESIGN-co-cli.md` §7 for architecture.

---

## Items

- [ ] **Sliding window**: Register a `history_processor` to trim by message count or token budget — keeps context within LLM window for long sessions
- [ ] **Persistence**: Save history to SQLite (alongside OTel traces) to enable session resume across `co chat` invocations
- [ ] **Tool output trimming**: Summarize large tool outputs (e.g. file contents) in history to conserve context space
