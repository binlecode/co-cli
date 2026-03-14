# Prompt

Explain the core differences between optimistic concurrency control and pessimistic concurrency control in database systems. Use a simple analogy to a real-world scenario to make it easy to understand for a junior developer.

# Response

### Core Differences

The fundamental difference lies in **when** the system checks for conflicts and **how** it handles them.

*   **Pessimistic Concurrency Control (PCC):** Assumes conflicts are likely. It **locks** the data immediately when a transaction starts. Other transactions must wait until the lock is released.
    *   *Pros:* High safety, no lost updates.
    *   *Cons:* Lower performance, potential for deadlocks (everyone waiting for each other).
*   **Optimistic Concurrency Control (OCC):** Assumes conflicts are rare. It **allows** all transactions to proceed without locking. It checks for conflicts only at the end (commit time). If a conflict is found, the transaction is rolled back and retried.
    *   *Pros:* High performance, better concurrency.
    *   *Cons:* Potential for retries and wasted work if conflicts are frequent.

### Real-World Analogy: The Shared Whiteboard

Imagine a team working on a **single shared whiteboard** in a meeting room.

*   **Pessimistic Approach (The "Do Not Disturb" Sign):**
    Before anyone starts writing, they must put a "In Use" sign on the board. No one else can touch the board until they finish writing and remove the sign.
    *   *Result:* Safe, but if two people want to write at once, one must wait in line.

*   **Optimistic Approach (The "Check Before Leaving" Rule):**
    Anyone can write on the board freely without asking. However, before they leave, they check if the board looks the same as when they started. If someone else wrote over their section in the meantime, they erase their work and start over.
    *   *Result:* Fast and efficient, but if everyone writes at once, everyone will end up erasing and restarting their work.

### When to Use Which?

*   **Use Pessimistic** when data conflicts are frequent (e.g., booking the last seat on a flight).
*   **Use Optimistic** when conflicts are rare (e.g., updating a user profile or reading data).