# Prompt

Explain the core differences between optimistic concurrency control and pessimistic concurrency control in database systems. Use a simple analogy to a real-world scenario to make it easy to understand for a junior developer.

# Response

**Pessimistic Concurrency Control** assumes conflicts are likely. It locks the data immediately upon access, preventing other transactions from reading or modifying it until the current transaction is finished.

**Optimistic Concurrency Control** assumes conflicts are rare. It allows multiple transactions to proceed without locking. It only checks for conflicts at the moment of saving (commit), usually by comparing version numbers or timestamps.

### Real-World Analogy: The Shared Whiteboard

*   **Pessimistic Control:** You walk up to the whiteboard and **lock the marker** so no one else can use it. You write your notes, then unlock the marker. This guarantees no one overwrites you, but others must wait for you to finish.
*   **Optimistic Control:** Everyone grabs a marker and writes freely. When you finish, you check if the board looks the same as when you started. If someone else wrote on it while you were working, your changes are rejected, and you must try again.

### Summary of Differences

| Feature | Pessimistic | Optimistic |
| :--- | :--- | :--- |
| **Strategy** | Lock data to prevent conflict | Check for conflict at commit |
| **Performance** | Slower (due to waiting/locks) | Faster (less waiting) |
| **Risk** | Low risk of data loss | Risk of failed transactions (retries) |
| **Best For** | High-conflict data (e.g., bank balances) | Low-conflict data (e.g., blog comments) |