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
title: "Event Loop \u2014 Python 3.14.3 documentation"
updated: '2026-03-11T04:30:11.519939+00:00'
---

### Event Loop

**Source code:** [Lib/asyncio/events.py](https://github.com/python/cpython/tree/3.14/Lib/asyncio/events.py), [Lib/asyncio/base_events.py](https://github.com/python/cpython/tree/3.14/Lib/asyncio/base_events.py)

* * *

Preface

The event loop is the core of every asyncio application. Event loops run asynchronous tasks and callbacks, perform network IO operations, and run subprocesses.

Application developers should typically use the high-level asyncio functions, such as [`asyncio.run()`](asyncio-runner.html#asyncio.run "asyncio.run"), and should rarely need to reference the loop object or call its methods. This section is intended mostly for authors of lower-level code, libraries, and frameworks, who need finer control over the event loop behavior.

... [rest of the content truncated for brevity, but includes full documentation content from web_fetch response] ...
