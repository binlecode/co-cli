---
auto_category: null
created: '2026-03-11T02:09:03.279330+00:00'
decay_protected: true
id: 1
kind: article
origin_url: https://docs.python.org/3/library/asyncio-eventloop.html
provenance: web-fetch
tags:
- event-loop
- asyncio
- python
- best-practices
title: Python asyncio Event Loop Best Practices
updated: '2026-03-31T21:55:01.572055+00:00'
---

# Python asyncio Event Loop Best Practices

## Overview

The event loop is the core of every asyncio application. Event loops run asynchronous tasks and callbacks, perform network IO operations, and run subprocesses.

## Key Best Practices

### 1. Use High-Level Functions

**Application developers should typically use the high-level asyncio functions, such as `asyncio.run()`, and should rarely need to reference the loop object or call its methods.**

This section is intended mostly for authors of lower-level code, libraries, and frameworks, who need finer control over the event loop behavior.

### 2. Getting the Event Loop

```python
import asyncio

# Preferred in coroutines and callbacks
loop = asyncio.get_running_loop()

# Alternative (deprecated policy system)
loop = asyncio.get_event_loop()
```

**Note:** `get_running_loop()` is preferred over `get_event_loop()` in coroutines and callbacks.

### 3. Running the Event Loop

**Recommended approach using `asyncio.run()`:**

```python
import asyncio

async def main():
    # Your async code here
    await asyncio.sleep(1)
    return "done"

if __name__ == '__main__':
    result = asyncio.run(main())
```

**Low-level approach (for advanced use cases):**

```python
import asyncio

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

try:
    loop.run_forever()
finally:
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.close()
```

### 4. Scheduling Callbacks

**Use `call_soon()` for immediate scheduling:**

```python
def callback():
    print("Hello World")

loop.call_soon(callback)
```

**Use `call_soon_threadsafe()` when scheduling from another thread:**

```python
# Thread-safe variant - MUST be used from other threads
loop.call_soon_threadsafe(callback)
```

**Use `call_later()` for delayed execution:**

```python
import time

def delayed_callback():
    print(f"Called at {time.time()}")

# Schedule callback to run after 5 seconds
loop.call_later(5, delayed_callback)
```

### 5. Creating Tasks and Futures

**Preferred way to create Futures:**

```python
future = loop.create_future()
```

**Schedule coroutines as Tasks:**

```python
async def my_coroutine():
    await asyncio.sleep(1)
    return "done"

task = loop.create_task(my_coroutine())
```

### 6. Thread Pool and Process Pool Execution

**For blocking I/O operations:**

```python
import asyncio
import concurrent.futures

def blocking_io():
    with open('/dev/urandom', 'rb') as f:
        return f.read(100)

async def main():
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, blocking_io)
    print('default thread pool', result)

asyncio.run(main())
```

**For CPU-bound operations:**

```python
def cpu_bound():
    return sum(i * i for i in range(10 ** 7))

async def main():
    loop = asyncio.get_running_loop()
    with concurrent.futures.ProcessPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, cpu_bound)
        print('custom process pool', result)

asyncio.run(main())
```

### 7. Error Handling

**Custom exception handler:**

```python
def custom_handler(loop, context):
    print(f"Custom error: {context['message']}")

loop.set_exception_handler(custom_handler)
```

### 8. Debug Mode

**Enable debug mode:**

```python
loop.set_debug(True)

# Set threshold for slow callbacks (default: 100ms)
loop.slow_callback_duration = 0.1
```

### 9. Server Objects

**Server objects are asynchronous context managers:**

```python
async def client_connected(reader, writer):
    data = await reader.readline()
    writer.write(data)
    await writer.drain()

async def main(host, port):
    srv = await asyncio.start_server(client_connected, host, port)
    async with srv:
        await srv.serve_forever()

asyncio.run(main('127.0.0.1', 0))
```

## Important Notes

- **Most asyncio objects are not thread-safe** - use `call_soon_threadsafe()` when scheduling from other threads
- **The asyncio policy system is deprecated** and will be removed in Python 3.16
- **Use `asyncio.run()`** for most applications - it handles loop creation, execution, and cleanup automatically
- **For high-performance file transfers**, use `loop.sendfile()` which uses `os.sendfile()` when available
- **Enable debug mode** during development to catch slow callbacks and other issues

## References

- [Python asyncio Event Loop Documentation](https://docs.python.org/3/library/asyncio-eventloop.html)
- [asyncio - Asynchronous I/O](https://docs.python.org/3/library/asyncio.html)
- [asyncio Development Guide](https://docs.python.org/3/library/asyncio-dev.html)

---

*This article was saved from the official Python 3.14.3 documentation on March 31, 2026.*
