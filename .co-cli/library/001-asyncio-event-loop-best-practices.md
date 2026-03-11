---
auto_category: null
created: '2026-03-11T02:09:03.279330+00:00'
decay_protected: true
id: 1
kind: article
origin_url: https://docs.python.org/3/library/asyncio-eventloop.html
provenance: web-fetch
tags:
- python
title: Asyncio Event Loop Best Practices
---

The official Python documentation on asyncio event loops provides authoritative guidance. Key best practices include:

- Use high-level functions like `asyncio.run()` instead of manually managing event loops
- Prefer `asyncio.create_task()` over `loop.create_task()` for task creation
- Utilize `asyncio.run()` for simple entry points (avoids manual loop management)
- For complex applications, use `asyncio.get_running_loop()` in coroutines
- Avoid direct manipulation of event loop methods (e.g., `loop.run_forever()`) when unnecessary
- Use `SelectorEventLoop` on Unix and `ProactorEventLoop` on Windows
- Handle signals properly with `loop.add_signal_handler()`
- For network operations, prefer `asyncio.open_connection()` over low-level socket APIs
- Use `asyncio.create_subprocess_exec()` for subprocess management
- Set debug mode via `PYTHONASYNCIODEBUG` environment variable for diagnostics
- Implement proper error handling with `loop.set_exception_handler()`

The documentation emphasizes that most applications should use the high-level asyncio APIs directly rather than interacting with the underlying event loop objects. Common pitfalls to avoid include:
- Manually calling `loop.run_forever()` without proper shutdown
- Using `loop.create_task()` instead of `asyncio.create_task()`
- Not properly handling socket connections
- Misusing `call_soon` vs `call_later` for scheduling

The recommended approach is to structure code around coroutines and tasks, using the standard asyncio functions rather than lower-level event loop methods.
