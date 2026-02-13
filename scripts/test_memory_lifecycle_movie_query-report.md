# REVIEW: Co Learns About a Movie — A Memory Lifecycle Trace

One E2E test. No mocks. Live model, live web, live memory files. This document traces the full conversation between the test harness and Co, message by message, to show how Co's memory lifecycle works as designed.

**Test:** `test_ollama_web_research_and_save`
**Prompt:** *"Go online and learn from Wikipedia about the movie Finch. Save a short summary of what you learn to memory."*
**Model:** `qwen3:30b-a3b-thinking-2507-q8_0-agentic` (Ollama, 262K context)

---

## The Conversation

### Turn 0 — The setup

The test creates a `CoDeps` with real credentials (Brave search API key, web policy from settings) and sends the prompt through `agent.run()` with `UsageLimits(request_limit=25)`.

pydantic-ai injects the system prompt as the first message. This is the rules-only prompt from the redesign — `instructions.md` + `rules/*.md` + counter-steering. No personality, no aspects, no memory loaded yet. Just bootstrap rules and the tool menu.

==> instructions should be fundamential principles, and expect it to be short for now, all rules should be defined in rules/ folder, such as personality loading

The user prompt follows immediately:

> Go online and learn from Wikipedia about the movie Finch. Save a short summary of what you learn to memory.

==> this is too explicit, more naturally, the ask should be like: go online learn... and then tell me about this movie, make it interesting

---

### Turn 1 — Co plans and searches

Co receives the system prompt and user message. It thinks:

> *"The user wants me to go online and learn from Wikipedia about the movie Finch, then save a short summary to memory. I should use a tool that can fetch web content. Looking at the tools, there's web_search and web_fetch. Web_search is for searching via Brave Search, which might give me the Wikipedia link, and web_fetch can get the content from a URL."*

Co calls:

```
web_search(query="Finch movie Wikipedia", max_results=3)
```

**Observation:** Co chose a good search query — added "Wikipedia" to the movie name to target the right source. It limited to 3 results to stay efficient.

---

### Turn 2 — Search results arrive

The `web_search` tool executes (Brave Search API, no approval needed — read-only tool) and returns 3 results:

1. **Finch (film) - Wikipedia** — *"Fifteen years have passed since a massive solar flare destroyed the ozone layer, turning the planet Earth into a largely uninhabitable wasteland... One of the few survivors, robotics engineer Finch Weinberg, lives alone with his dog Goodyear and a helper-robot Dewey in an underground St."*
   `https://en.wikipedia.org/wiki/Finch_(film)`

2. **Finch - Wikidata** — *"2021 film directed by Miguel Sapochnik"*

3. **Finch (film) | JH Wiki Collection Wiki | Fandom** — *"Finch is a 2021 American post-apocalyptic survival film directed by Miguel Sapochnik and written by Craig Luck and Ivor Powell. The film stars Tom Hanks..."*

The search snippets already contain substantial information: director, year, stars, premise, character names.

---

### Turn 3 — Co goes deeper

Co isn't satisfied with snippets. It thinks:

> *"The top result is the actual Wikipedia page. Now I need to fetch that page's content using web_fetch. The URL from the search is `https://en.wikipedia.org/wiki/Finch_(film)`."*

Co calls:

```
web_fetch(url="https://en.wikipedia.org/wiki/Finch_(film)")
```

**Observation:** Co followed the user's instruction literally — "learn from Wikipedia" — so it tried to fetch the full Wikipedia article rather than just using the search snippets.

---

### Turn 4 — Wikipedia says no

The `web_fetch` tool hits a wall:

```
web_fetch error (HTTP 403) for https://en.wikipedia.org/wiki/Finch_(film).
```

Wikipedia returned HTTP 403 Forbidden. The tool reports this back to Co as a `RetryPromptPart` — pydantic-ai's way of telling the model "this tool call failed, try something else."

---

### Turn 5 — Co adapts and saves

Co handles the error gracefully. It thinks:

> *"The server is refusing the request. Maybe Wikipedia's robots.txt doesn't allow scraping... I can't access the page directly. But I already have enough information from the search snippets to write a summary."*

Instead of retrying the fetch or trying a different URL, Co synthesizes what it already knows from the search results and calls:

```
save_memory(
    content="Finch (2021) is a post-apocalyptic film directed by Miguel
             Sapochnik, starring Tom Hanks as a robotics engineer surviving
             a solar flare-apocalypse. Set in a world where Earth's ozone
             layer is destroyed, Finch lives underground with his dog
             Goodyear and a robot named Dewey. The film explores survival,
             companionship, and humanity in a devastated world.",
    tags=["movie", "summary"]
)
```

**`save_memory` requires approval.** It's registered with `requires_approval=True` because it writes to disk — a side effect. pydantic-ai doesn't execute it. Instead, it returns a `DeferredToolRequests` object, and the agent run stops.

---

### Result — Deferred, awaiting approval

The test receives `DeferredToolRequests` with one pending call:

```
Deferred: save_memory (id=call_66bfgavc)
```

In the live chat loop, this would show the user a confirmation prompt:

> Co wants to save a memory: "Finch (2021) is a post-apocalyptic film..."
> [Approve] [Deny]

The test doesn't approve — it just verifies the chain happened correctly and stops.

---

## What the test asserts

The test (`test_ollama_web_research_and_save`) verifies five things about this conversation:

1. **Web tools were used.** At least one of `web_search` or `web_fetch` appears in the tool call sequence. (Both were called in this trace.)

2. **Result is `DeferredToolRequests`.** The final output is not a text string — it's a deferred approval request, proving `save_memory` was called and properly gated.

3. **`save_memory` is in the deferred calls.** The specific tool awaiting approval is `save_memory`, not some other side-effectful tool.

4. **The saved content mentions "Finch".** Co didn't save garbage or hallucinate unrelated content — the memory captures what it actually learned.

5. **Tool ordering: web before save.** The first web tool call index is strictly less than the `save_memory` call index. Co researched before it tried to save. This catches a degenerate case where the model might try to save from prior knowledge without actually going online.

---

## The full message sequence

```
Message 0  [ModelRequest]
  SystemPromptPart    → rules-only system prompt (instructions + rules + counter-steering)
  UserPromptPart      → "Go online and learn from Wikipedia about the movie Finch..."

Message 1  [ModelResponse]
  ThinkingPart        → plans approach, decides on web_search
  ToolCallPart        → web_search(query="Finch movie Wikipedia", max_results=3)

Message 2  [ModelRequest]
  ToolReturnPart      → 3 search results with snippets (Wikipedia, Wikidata, Fandom)

Message 3  [ModelResponse]
  ThinkingPart        → wants more detail, decides to fetch the Wikipedia page
  ToolCallPart        → web_fetch(url="https://en.wikipedia.org/wiki/Finch_(film)")

Message 4  [ModelRequest]
  RetryPromptPart     → HTTP 403 error from Wikipedia

Message 5  [ModelResponse]
  ThinkingPart        → adapts, synthesizes from search snippets instead
  ToolCallPart        → save_memory(content="Finch (2021) is a post-apocalyptic film...",
                                     tags=["movie", "summary"])

→ DeferredToolRequests (save_memory awaiting approval)
```

**Total: 3 model turns, 2 tool executions (search + fetch), 1 deferred (save).**

---

## What this trace demonstrates about Co's design

**Rules-only bootstrap works.** The system prompt contains no personality, no aspects, no preloaded memory — just instructions and behavioral rules. Co still reasons correctly about which tools to use and in what order.

**Tool autonomy.** Nobody told Co "call web_search then web_fetch then save_memory." The prompt said "go online, learn, save." Co decomposed that into a multi-step tool plan on its own.

**Error recovery.** When `web_fetch` returned 403, Co didn't retry in a loop or give up. It recognized it already had enough information from the search snippets and moved to the next step.

**Approval gate.** `save_memory` is a write operation — it creates files on disk. The approval gate stopped execution and gave control back to the user (or in this case, the test harness). Read-only tools (`web_search`, `web_fetch`) executed without approval.

**Content quality.** The saved summary is accurate, concise, and sourced from the actual search results — not hallucinated from training data. It includes: title, year, director, star, premise, character names (Goodyear, Dewey), and thematic description.

**Memory as the final step.** The memory lifecycle starts here. If the user approves, `save_memory` would:
1. Check for duplicates against recent memories (dedup window)
2. Write the markdown file with YAML frontmatter
3. Check if total count exceeds limit
4. Trigger decay if over limit (consolidate oldest)

Future `recall_memory` calls matching "Finch" or "movie" would find this memory, and gravity would touch it (refresh its timestamp), keeping it accessible as long as it's useful.
