# Prompt

We have a production web application using a Node.js backend and a PostgreSQL database. During peak hours, the application experiences random 504 Gateway Timeout errors. The CPU on the Node instances is fine, memory is stable, and the database CPU is under 40%. The issue vanishes instantly when we restart the Node.js instances. What are the top 3 most likely root causes? Walk through your diagnostic reasoning for each.

# Response

Based on the symptoms—specifically the 504 Gateway Timeout errors during peak load, combined with healthy CPU/Memory metrics and an "instant fix" upon restart—the issue is almost certainly a **resource exhaustion within the application process or connection layer**, rather than a compute shortage.

When a Node.js process restarts, it resets the operating system file descriptors, clears the event loop state, and re-requests database connections from scratch. This behavior points to the top 3 most likely root causes:

### 1. PostgreSQL Connection Pool Exhaustion (or Leaks)

**Why this fits:**
This is the most common architectural issue in Node.js + PostgreSQL environments. Node.js typically uses an async driver (like `pg` or `knex`) with a configurable connection pool. If the configured `maxConnections` is too low for peak traffic, or if connections are not being returned to the pool properly (leaks), new requests will sit in a queue inside the Node.js process waiting for an available DB connection.

*   **Why CPU/Memory look fine:** While waiting for a DB connection, the Node.js event loop is idle. The process isn't doing calculations (low CPU), and a connection object consumes negligible memory. Therefore, aggregate resource metrics remain stable.
*   **Why Restart fixes it:** A restart forces all existing connections to close. On boot, the app re-establishes a fresh pool, clearing the backlog of waiting requests and allowing traffic to flow normally immediately.
*   **Why DB CPU is low:** The database is effectively idle. It is waiting for the Node.js app to assign a query to it. If the app is stuck waiting for a DB *slot*, the DB isn't running queries, hence the low CPU utilization.

**Diagnostic Reasoning:**
*   **Check Metrics:** Monitor the `totalActiveConnections` from your DB driver against the configured `maxConnections`.
*   **Check Logs:** Look for `pool timeout` errors in the Node.js application logs during the 504 windows.
*   **Check Queries:** Ensure every query explicitly closes the connection (via `client.query()` returning to the pool or `await connection.end()`). Common patterns causing this include holding database transactions longer than necessary inside long-running API routes.

### 2. Operating System File Descriptor (Socket) Exhaustion

**Why this fits:**
Node.js is single-threaded but can handle thousands of concurrent connections. However, the Operating System enforces a limit on the number of open file descriptors (`ulimit -n`) available to a process. If the application opens a socket for every request and fails to close it (e.g., connection pooling issues, or connections to third-party APIs not being reused), the count climbs until it hits the OS limit. Once hit, the OS rejects new socket creation (`EMFILE` error).

*   **Why CPU/Memory look fine:** The application isn't consuming RAM or CPU cycles when it hits this limit. It is simply unable to establish a new network connection. The process hangs waiting for an OS resource that is currently unavailable.
*   **Why Restart fixes it:** Restarting the container/pod clears the process state and releases all held file descriptors back to the OS kernel, resetting the count from the exhausted limit.
*   **Why it happens at Peak:** During peak hours, the volume of concurrent outgoing connections (APIs, DBs, Caching layers) spikes, pushing the accumulated open-socket count over the threshold.

**Diagnostic Reasoning:**
*   **Check OS Limits:** Run `ulimit -n` inside a running container to see the soft/hard limits.
*   **Check `lsof`:** If possible, run `lsof -p <PID> | wc -l` on a healthy instance vs. a failing instance (if accessible).
*   **Check Logs:** Look for system error `EMFILE` (Too many open files) or "Connection refused" in the Node.js error logs.
*   **Investigate Leaks:** Look for HTTP client implementations that aren't using connection reuse (Keep-Alive disabled) or streams that aren't being properly closed/destroyed.

### 3. Event Loop Blocking (Synchronous Operations or GC Pauses)

**Why this fits:**
Node.js relies on a single-threaded event loop. If a specific piece of code performs a blocking operation (like heavy JSON parsing, large file I/O, synchronous crypto operations, or a complex loop), the thread freezes.

*   **Why CPU/Memory look fine:** This is a classic metric blind spot. The thread is blocked, not spinning. Therefore, CPU usage might register as near zero or average out low across the fleet. If a long Garbage Collection (GC) cycle triggers, the heap pauses (Stop-The-World), but memory usage remains stable.
*   **Why Restart fixes it:** A restart resets the heap state, clears the event loop queue, and discards any accumulated state that might be causing the block. It does not fix the code, but it clears the immediate state causing the hang.
*   **Why it happens at Peak:** Peak load often correlates with specific heavy payloads or specific data patterns that trigger the blocking logic more frequently (e.g., parsing a large dataset that becomes larger during peak reporting).

**Diagnostic Reasoning:**
*   **Use Node.js Profiling:** Tools like `clinic.js`, `0x`, or the built-in `--inspect` flag to trace where time is spent. Look for high `Event Loop Lags`.
*   **Check Logs:** Look for `FATAL ERROR: Ineffective mark-compact` or stack traces indicating heavy processing.
*   **Code Review:** Search for synchronous calls in async routes (e.g., `JSON.parse` on huge payloads, `fs.readFileSync`, `child_process.execSync`, or synchronous cryptographic functions).

### Summary of Investigation Plan

| Hypothesis | Most Likely Indicator | Verification Command/Action |
| :--- | :--- | :--- |
| **1. DB Pool Exhaustion** | App logs showing DB query timeouts or connection waits. | Check driver metrics: `activeConnections` vs `maxConnections`. |
| **2. FD/Socket Exhaustion** | System logs showing `EMFILE` or open connection counts maxing out. | `lsof -p <PID> | wc -l` on live instance. Check `ulimit -n`. |
| **3. Event Loop Block** | High Event Loop Lag in monitoring tools. | `node --inspect` or `node --prof` during incident. Check for Sync ops. |

**Recommendation:** Start with **Hypothesis 1 (DB Pool)**. Since you are using PostgreSQL, ensure you are using a pooler (like PgBouncer) or configuring the `poolSize` correctly in your Node driver (e.g., `max` property in `pg-pool`) relative to your load balancer's concurrency settings. This combination accounts for the 504s and the "restart fixes" behavior most frequently in this stack.