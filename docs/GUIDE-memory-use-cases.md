# Co Memory System - Practical Use Cases Guide

This guide shows you how to experience Co's memory power through real-world scenarios.

---

## Quick Start: Your First Memory Session

### Step 1: Start a chat session

```bash
cd ~/your-project
uv run co chat
```

### Step 2: Ask Co to remember your preferences

```
You: Remember that I prefer using async/await over callbacks in Python

Co: [Uses save_memory tool - you'll see an approval prompt]
    ✓ Saved memory 1: 001-prefer-using-async-await-over-callbacks-in-python.md

    I've saved your preference. I'll keep this in mind when discussing
    Python code architecture with you.
```

**What just happened:**
- Co used the `save_memory` tool
- You approved the save (required for security)
- A markdown file was created in `.co-cli/knowledge/memories/`
- Co can now recall this preference in future conversations

### Step 3: Build up your knowledge base

Add more memories in the same session or across multiple sessions:

```
You: Remember that I use pytest for all tests, no unittest

You: Remember that our API base URL is https://api.example.com/v2

You: Remember that database migrations go in migrations/ folder using Alembic

You: Remember that I prefer SQLAlchemy 2.0 ORM for database access
```

### Step 4: Ask Co to recall

Later in the conversation (or in a new session):

```
You: What do you know about my Python testing preferences?

Co: [Uses recall_memory tool - no approval needed, it's read-only]

    I found these memories about your Python testing:

    1. You use pytest for all tests, no unittest
    2. You prefer async/await over callbacks in Python code

    Would you like me to set up tests using these preferences?
```

### Step 5: See all your memories

```
You: Show me all my memories

Co: [Uses list_memories tool]

    Total memories: 5

    **001** (2026-02-10): Prefer using async/await over callbacks in Python
    **002** (2026-02-10): Use pytest for all tests, no unittest
    **003** (2026-02-10): API base URL is https://api.example.com/v2
    **004** (2026-02-10): Database migrations go in migrations/ folder using Alembic
    **005** (2026-02-10): Prefer SQLAlchemy 2.0 ORM for database access
```

---

## Proactive Memory Detection (New!)

**The game-changer:** Co automatically detects when you share important information and offers to save it—no need to explicitly say "remember"!

### How it works

Co recognizes memory-worthy signals in your natural conversation:

| You Say | Co Recognizes | Auto-Saves As |
|---------|---------------|---------------|
| "I prefer async/await" | Preference signal | `[preference, python]` |
| "Actually, we use TypeScript" | Correction signal | `[correction, languages]` |
| "We decided to use Postgres" | Decision signal | `[decision, database]` |
| "Our API base is example.com" | Context signal | `[context, api]` |
| "We always lint before commit" | Pattern signal | `[pattern, workflow]` |

### Live example:

```
You: I prefer async/await over callbacks in Python

Co: [Detects preference signal, calls save_memory automatically]

    Save memory 1: User prefers async/await over callbacks in Python
    Tags: preference, python

    Approve? [y/n/a]

You: y

Co: ✓ Saved memory 1. I'll keep this in mind when discussing Python code.
```

**What just happened:**
- You stated a preference naturally (no "remember" command!)
- Co detected the signal pattern and called `save_memory`
- You approved (required for security)
- Co saved it with appropriate tags

### What Co detects automatically

✅ **Preferences** — "I prefer X", "I like Y", "I use Z"
✅ **Corrections** — "Actually X", "That's wrong, it's Y", "No wait, Z"
✅ **Decisions** — "We decided X", "We chose Y", "We implemented Z"
✅ **Patterns** — "We always X", "Never Y", "When we do X..."
✅ **Context** — Factual statements about project, team, environment

❌ **What Co ignores:**
- Speculation: "Maybe we should...", "I think...", "Could we..."
- Questions: "Should we use X?", "What if we tried Y?"
- Transient details: Conversation context only relevant to current session

### Comparison: Explicit vs Proactive

**Old way (still works!):**
```
You: Remember that I use pytest for testing
Co: ✓ Saved memory 1
```

**New way (automatic!):**
```
You: I use pytest for all my tests, no unittest
Co: [Detects preference signal]
    Save memory 1: User uses pytest for all tests
    Approve? [y/n/a]
```

Both work! Use whichever feels natural.

### Tips for triggering proactive saves

Be direct and assertive:
- ✅ "I prefer X" — clear preference signal
- ✅ "We use X" — clear context signal
- ❌ "I might prefer X" — uncertain, not saved
- ❌ "What about X?" — question, not saved

### Privacy & Control

- **Approval required:** Co never saves without your permission
- **Review before save:** See exactly what will be saved and how it's tagged
- **Deny saves:** Press 'n' to reject unwanted saves
- **Delete later:** Use `/forget <id>` to remove saved memories

---

## Use Case 1: Personal Coding Style Preferences

**Scenario:** You want Co to consistently follow your coding style across all projects.

### Save your preferences:

```
You: Remember these coding preferences:
     - I use single quotes for strings in Python
     - I prefer explicit imports over star imports
     - I prefer type hints on all functions
     - I use 4-space indentation
     - I prefer descriptive variable names over short ones

Co: [Saves each as a separate memory or combines them]
    ✓ I've saved your coding style preferences.
```

### Later when writing code:

```
You: Write a function to parse JSON from a file

Co: [Recalls your preferences and writes code accordingly]

    Based on your preferences, here's the function with explicit imports,
    type hints, and descriptive names:

    ```python
    from pathlib import Path
    import json
    from typing import Any

    def parse_json_from_file(file_path: Path) -> dict[str, Any]:
        '''Parse JSON data from a file.'''
        with open(file_path, 'r', encoding='utf-8') as file:
            return json.load(file)
    ```
```

---

## Use Case 2: Project-Specific Conventions

**Scenario:** Your project has specific architectural patterns and naming conventions.

### Setup project context (one-time):

Edit `.co-cli/knowledge/context.md` directly:

```markdown
---
version: 1
updated: 2026-02-10T10:00:00Z
---

# Project: E-commerce API

## Architecture
- Layered architecture: routes → services → repositories
- All business logic in service layer
- Database access only in repositories

## Conventions
- API routes in `api/routes/`
- Services in `api/services/`
- Models in `api/models/`
- Use Pydantic for request/response models
```

This context is **always loaded** at session start—no need to ask Co to recall it.

### Save specific decisions as memories:

```
You: Remember that we decided to use JWT tokens with 1-hour expiration

You: Remember that all API errors should return {error: string, code: number} format

You: Remember that we use Redis for session storage
```

### When working on the project:

```
You: I need to add a new user registration endpoint

Co: [Has context + memories loaded]

    Based on the project architecture, I'll create:
    1. Route handler in api/routes/auth.py
    2. Service logic in api/services/user_service.py
    3. Pydantic models for request/response

    I'll also implement JWT token generation with 1-hour expiration
    as you specified earlier.
```

---

## Use Case 3: Team Knowledge Base

**Scenario:** Share knowledge across your team by committing memory files to git.

### Team member 1 saves decisions:

```
You: Remember that we use semantic versioning: MAJOR.MINOR.PATCH

You: Remember that we deploy to staging on PR merge, production on release tag

You: Remember that all PRs need 2 approvals before merge
```

### Commit and push:

```bash
git add .co-cli/knowledge/
git commit -m "Add team conventions to Co memory"
git push
```

### Team member 2 pulls and uses:

```bash
git pull
uv run co chat
```

```
You: What's our deployment process?

Co: [Recalls team memories]

    Based on the team's documented process:
    - Staging deployment: automatic on PR merge
    - Production deployment: triggered by release tag
    - All PRs need 2 approvals before merge
```

---

## Use Case 4: API Keys and Secrets Management

**Scenario:** Remember WHERE secrets are stored, not the secrets themselves.

```
You: Remember that the Stripe API key is in .env as STRIPE_SECRET_KEY

You: Remember that Google Cloud credentials are at ~/.config/gcloud/credentials.json

You: Remember that database password is in 1Password under "Dev DB Credentials"

You: Remember that AWS credentials should use IAM roles, not hard-coded keys
```

### When you need them:

```
You: I need to set up Stripe payment processing

Co: [Recalls secret locations]

    I'll set up Stripe integration. According to your notes, the Stripe
    API key is stored in .env as STRIPE_SECRET_KEY.

    I'll use this environment variable in the code:

    ```python
    import os
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
    ```
```

**Security note:** Co never stores actual secrets—only metadata about where they're located.

---

## Use Case 5: Learning from Past Mistakes

**Scenario:** Document gotchas and lessons learned so Co reminds you next time.

```
You: Remember that we had a bug where we forgot to handle timezone-naive datetimes

You: Remember that SQLAlchemy lazy loading causes N+1 queries—always use joinedload

You: Remember that our Redis connection pool was too small—set max_connections=50

You: Remember that we need to add CORS headers for the frontend domain
```

### When writing similar code:

```
You: Write a function to query all users and their orders

Co: [Recalls N+1 query warning]

    I'll use joinedload to avoid N+1 queries, based on the issue you
    encountered before:

    ```python
    from sqlalchemy.orm import joinedload

    def get_users_with_orders(session):
        return session.query(User).options(
            joinedload(User.orders)
        ).all()
    ```
```

---

## Use Case 6: External Tool References

**Scenario:** Remember how to use tools you don't use daily.

```
You: Remember that to run Docker build: docker build -t myapp:latest .

You: Remember that to check disk usage: df -h for overview, du -sh * for current dir

You: Remember that to reset git branch: git fetch origin && git reset --hard origin/main

You: Remember that to profile Python: python -m cProfile -o output.prof script.py
```

### When you need them:

```
You: How do I build the Docker image?

Co: [Recalls command]

    According to your notes: docker build -t myapp:latest .

    Would you like me to run this for you?
```

---

## Advanced: Manual Memory Editing

All memories are just markdown files—you can edit them directly!

### View memories:

```bash
ls -la .co-cli/knowledge/memories/
cat .co-cli/knowledge/memories/001-prefer-async-await.md
```

### Edit a memory:

```bash
vim .co-cli/knowledge/memories/001-prefer-async-await.md
```

```markdown
---
id: 1
created: '2026-02-10T10:30:00+00:00'
tags:
- python
- style
- preference
- concurrency
source: user-told
---

User prefers async/await over callbacks in Python code.

Additional context: This preference applies especially to I/O operations
like database queries, API calls, and file operations. Use asyncio library
for concurrent operations.
```

Changes take effect immediately in the next session or recall.

---

## Memory Management Tips

### 1. Use descriptive tags

```
You: Remember that I use FastAPI for web APIs
     (tags: python, web, framework, api)
```

Tags make search more effective:
```
You: What frameworks do I use?
Co: [Searches tags: "framework"]
```

### 2. Keep memories atomic

❌ **Don't:** "Remember all my Python preferences: async, pytest, type hints, SQLAlchemy, FastAPI"

✅ **Do:** Save each preference separately:
- "Remember I prefer async/await"
- "Remember I use pytest"
- "Remember I use type hints"

### 3. Update memories by editing files

Instead of asking Co to "update memory 5", just edit the file:

```bash
vim .co-cli/knowledge/memories/005-*.md
```

### 4. Delete outdated memories

**Method 1: Use `/forget` command** (recommended):
```
You: /forget 3

Co: ✓ Deleted memory 3: 003-old-api-url.md
```

**Method 2: Delete files manually**:
```bash
rm .co-cli/knowledge/memories/003-old-api-url.md
```

Tip: Use `/list_memories` to see memory IDs before deleting.

### 5. Use context.md for always-needed info

If Co needs something in **every** conversation, put it in `context.md` instead of a memory:

```bash
vim .co-cli/knowledge/context.md
```

---

## Checking What Co Remembers

### Method 1: Ask directly

```
You: What do you remember about my testing preferences?
You: What do you remember about our database setup?
You: What do you know about how I like to write Python?
```

### Method 2: List all

```
You: Show me all my memories
You: List everything you remember about me
```

### Method 3: Check files directly

```bash
# List all memories
ls -1 .co-cli/knowledge/memories/

# View a specific memory
cat .co-cli/knowledge/memories/001-*.md

# Search memories by keyword
grep -r "pytest" .co-cli/knowledge/memories/

# Count total memories
ls .co-cli/knowledge/memories/*.md | wc -l
```

---

## Real Session Example: New Developer Onboarding

This shows a realistic conversation flow:

```
# Day 1: Setup
You: I'm starting on this project. Can you help me understand the codebase?

Co: I'll analyze the codebase for you. First, let me check what I know...
    [Loads context.md with project architecture]

    This is an e-commerce API with layered architecture. Let me explore
    the key components...

You: Remember that I'm new to FastAPI

Co: ✓ Saved. I'll provide extra context when working with FastAPI code.

You: Remember that I prefer detailed comments in code I'm learning

Co: ✓ Saved. I'll add explanatory comments when showing you code.

# Day 5: Building features
You: I need to add a new product search endpoint

Co: [Recalls: project architecture, FastAPI newbie status, prefers comments]

    I'll create the search endpoint following the project's layered architecture.
    Since you're learning FastAPI, I'll add detailed comments:

    # api/routes/products.py
    from fastapi import APIRouter, Query
    from api.services.product_service import ProductService

    router = APIRouter()

    @router.get("/products/search")
    async def search_products(
        q: str = Query(..., description="Search query"),
        # Query(...) means this parameter is required
        # The description appears in auto-generated API docs
    ):
        """
        Search for products by name or description.

        This endpoint uses the service layer (ProductService) to handle
        business logic, following the project's architecture pattern.
        """
        service = ProductService()
        results = await service.search(q)
        return {"products": results}

    Would you like me to implement the service layer next?

You: Yes, and remember that we decided to use PostgreSQL full-text search for this

Co: ✓ Saved. I'll implement the search using PostgreSQL FTS...
```

---

## FAQ

### Q: How much can I store?

**Context files:** 10 KiB soft limit per file (warning), 20 KiB hard limit (error)
**Memories:** No hard limit, but grep search is efficient up to ~200 memories

### Q: Are memories private?

Memories are stored in `.co-cli/knowledge/` in your project directory:
- **Project memories:** Shareable via git (commit them!)
- **Personal memories:** Use global `~/.config/co-cli/knowledge/` or add to `.gitignore`

### Q: Can I search by date?

Not yet in Phase 1c. Current search is keyword + tag based. Future phases will add date filtering.

### Q: How are memories categorized?

Co automatically categorizes memories based on signal type:
- **preference** — "I prefer X", "I like Y"
- **correction** — "Actually X", "That's wrong"
- **decision** — "We decided X", "We chose Y"
- **context** — Factual statements
- **pattern** — "We always X", "Never Y"

View categories with `/list_memories`:
```
**001** (2026-02-10) [preference] : User prefers async/await over callbacks
**002** (2026-02-10) [correction] : Team uses TypeScript not JavaScript
**003** (2026-02-10) [decision] : Chose PostgreSQL for database
```

### Q: What's the difference between context.md and memories?

| Feature | context.md | memories/*.md |
|---------|-----------|---------------|
| Loading | Always loaded at session start | On-demand via recall_memory |
| Best for | Architecture, conventions, structure | Specific decisions, preferences |
| Size | 7 KiB budget (project) or 3 KiB (global) | Unlimited |
| Approval | No approval (you edit manually) | Requires approval when saving |
| Categorization | Manual | Automatic (preference/correction/decision/etc) |

### Q: Can I use Co without memories?

Yes! Memories are optional. Co works fine without them—you just won't have persistent knowledge across sessions.

---

## Next Steps

1. **Try it now:**
   ```bash
   uv run co chat
   # Ask Co to remember something!
   ```

2. **Set up project context:**
   ```bash
   vim .co-cli/knowledge/context.md
   ```

3. **View the demo:**
   ```bash
   uv run python scripts/demo_knowledge_roundtrip.py
   ```

4. **Read the design doc:**
   ```bash
   cat docs/DESIGN-14-knowledge-system.md
   ```

---

## Summary

Co's memory system gives you:

✅ **Persistent preferences** across all sessions
✅ **Project-specific knowledge** loaded automatically
✅ **Team knowledge sharing** via git commits
✅ **No vendor lock-in** — just markdown files
✅ **Full control** — edit files directly anytime
✅ **Privacy** — all data stored locally

Start small: ask Co to remember one thing. Build up your knowledge base over time. The more Co knows about your preferences and project conventions, the better it can assist you! 🚀
