# TODO: Phase 2.6 — Articles Knowledge System (Multimodal Lakehouse)

**Status**: 📝 DOCUMENTED | **Effort**: 12-16 hours | **Priority**: MEDIUM

---

## Overview

Implement articles knowledge system: web-fetched documentation, tutorials, and multimodal content stored in a file-based lakehouse. Extends Phase 1c's knowledge foundation to include curated external content alongside context and memories.

**Phase 1c delivered**: Always-loaded context + on-demand memories
**Phase 2.6 adds**: Curated articles with multimodal support (text, images, PDFs, audio transcripts)

**Core principle**: Files as source of truth (lakehouse pattern). Articles are larger, less frequently accessed, and sourced from external URLs rather than learned from conversation.

**Design consistency**: Phase 2.6 uses the same direct file access pattern as existing Obsidian tools (no API, direct vault access via `Path.rglob("*.md")`). This validates the lakehouse approach already proven in production.

---

## Goals

1. **Multimodal lakehouse**: Store text, images, PDFs, and audio transcripts as markdown + assets
2. **Web integration**: Fetch and save content from URLs with quality gate
3. **Agent tools**: `save_article`, `recall_article`, `list_articles` with approval
4. **Unified retrieval**: Search articles alongside memories using same infrastructure
5. **Source attribution**: Preserve original URLs, fetch timestamps, and quality metadata

---

## Architecture

```
.co-cli/knowledge/
├── context.md                    # Always-loaded (Phase 1c ✅)
├── memories/                     # On-demand, lifecycle-managed (Phase 1c ✅)
│   ├── 001-preference.md
│   ├── 002-decision.md
│   └── ...
└── articles/                     # NEW: Curated external content
    ├── python-async-tutorial.md
    ├── fastapi-dependency-injection.md
    ├── sqlalchemy-best-practices.md
    └── assets/                   # NEW: Multimodal assets
        ├── python-async-tutorial/
        │   ├── diagram-event-loop.png
        │   └── code-example.py
        └── fastapi-dependency-injection/
            └── architecture.svg
```

**Knowledge hierarchy**:
```
Internal Knowledge (umbrella term)
├── Context (always-loaded, static, user-curated)
│   └── .co-cli/knowledge/context.md
├── Memories (on-demand, lifecycle-managed, agent-written)
│   └── .co-cli/knowledge/memories/*.md
└── Articles (on-demand, curated, web-fetched) ← Phase 2.6
    └── .co-cli/knowledge/articles/*.md + assets/
```

---

## Design Decisions

### 1. Lakehouse Pattern

**Files as source of truth**: Markdown with YAML frontmatter + asset directory for multimodal content.

**Why lakehouse?**
- ✅ Version control friendly (git diff works)
- ✅ Human-readable and editable
- ✅ No database lock-in
- ✅ Direct file access (grep, fzf, editors)
- ✅ Multimodal support via asset directories

**Trade-offs**:
- Search slower than database (mitigated by FTS5 in future phases)
- No ACID transactions (acceptable for read-heavy workload)
- Large files (images, PDFs) in repo (mitigated by `.gitignore` for assets/)

**Proven pattern in co**: Obsidian tools already use direct file access (no API):

```python
# co_cli/tools/obsidian.py (lines 75-105)
vault = Path(ctx.deps.settings.obsidian_vault_path)
for note in vault.rglob("*.md"):
    content = note.read_text(encoding="utf-8")
    # Parse frontmatter, search content
```

**Articles will use identical pattern**:

```python
# co_cli/tools/articles.py (Phase 2.6)
articles_dir = Path(ctx.deps.settings.articles_dir)
for article in articles_dir.glob("*.md"):
    content = article.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(content)
    # Load and search articles
```

**Consistency**: No special APIs, no MCP needed for local knowledge access. Direct file system operations proven reliable in production.

### 2. Article vs Memory

| Aspect | Memory | Article |
|--------|--------|---------|
| **Source** | Conversation (learned) | Web (fetched) |
| **Size** | Small (500 chars) | Large (500-5000 words) |
| **Lifecycle** | Dedup + decay | Manual curation only |
| **Structure** | Simple (content + tags) | Rich (title, source, assets) |
| **Frequency** | Written often | Written rarely |
| **Content type** | Text only | Multimodal (text, images, PDFs) |

**Design rule**: If it's learned from conversation → memory. If it's fetched from web → article.

**Full comparison table (Context vs Memory vs Articles):**

| Aspect | Context | Persistent Memory | Articles |
|--------|---------|-------------------|----------|
| **Loading** | Always (session start) | On-demand (via tools) | On-demand (via tools) |
| **Size** | Small (10 KiB total) | Small per file (500 chars) | Medium per file (2000 words) |
| **Count** | 2 files (global + project) | Many (200 default limit) | Many (no limit) |
| **Management** | Manual (user edits) | Lifecycle (dedup, decay) | Manual curation |
| **Format** | Freeform markdown | Structured (tags, metadata) | Structured (source, title) |
| **Source** | User-written | Agent + user | Web-fetched |
| **Lifecycle** | Static (user maintains) | Dynamic (auto-managed) | Static (user maintains) |
| **Multimodal** | Text only | Text only | Text + images + PDFs + code |
| **Assets** | None | None | `assets/{slug}/` directory |
| **Use case** | Fundamental facts | Discrete learnings | Reference docs + tutorials |

### 3. Multimodal Support

**Text content**: Inline in markdown body
**Images**: Stored in `assets/{slug}/` directory, referenced via relative paths
**PDFs**: Extracted text in markdown, original PDF in assets
**Audio**: Transcript in markdown, audio file in assets (optional)
**Code files**: Stored in assets, syntax-highlighted snippets in markdown

**Example article structure**:
```
articles/
├── python-async-tutorial.md       # Main content
└── assets/
    └── python-async-tutorial/     # Asset directory (same slug)
        ├── diagram-event-loop.png
        ├── example-asyncio.py
        └── original.pdf           # Original source PDF (optional)
```

**Markdown references**:
```markdown
## Event Loop

![Event Loop Diagram](assets/python-async-tutorial/diagram-event-loop.png)

### Code Example

```python
# See: assets/python-async-tutorial/example-asyncio.py
import asyncio
...
```
```

### 4. Quality Gate (Curation)

**Problem**: Not all web content is worth saving. Need quality filter.

**Solution**: Agent-assisted curation with user approval.

**Quality criteria** (LLM-evaluated):
- Relevance to user's work (0.0-1.0)
- Accuracy and correctness (0.0-1.0)
- Clarity and organization (0.0-1.0)
- Actionability (has examples, not just theory) (0.0-1.0)
- Overall quality score: average of 4 criteria

**Curation flow**:
```
User: "Save this article about FastAPI"
  ↓
Agent calls web_fetch(url)
  ↓
Agent evaluates quality (0.0-1.0 score)
  ↓
If score < 0.6: "This content seems low quality, skip saving?"
If score ≥ 0.6: Propose save_article with summary
  ↓
User approval (y/n/a)
  ↓
Save article + assets
```

**Quality assessment prompt** (added to system.md):
```markdown
When evaluating content for saving as an article:
- Relevance: Does it relate to user's current project/work?
- Accuracy: Is information correct and up-to-date?
- Clarity: Is it well-organized and easy to follow?
- Actionability: Does it include examples and practical guidance?

Score 0.6+ = worth saving, <0.6 = skip unless user insists
```

### 5. Search Strategy

**Phase 2.6 (MVP)**: Same as memories - grep-based search
**Phase 3**: SQLite FTS5 (keyword search)
**Phase 4**: Hybrid FTS5 + vectors (semantic search)

**Unified search**: `recall_knowledge(query)` searches both memories AND articles, returns mixed results sorted by relevance.

**Search scope**:
- Article title
- Article body (markdown text)
- Tags
- Source URL
- Asset filenames

---

## Implementation Plan

### Part 1: Core Data Model (2-3 hours)

#### 1.1 Article Frontmatter Schema

**File**: `co_cli/_frontmatter.py` (extend existing)

```python
@dataclass
class ArticleFrontmatter:
    """Frontmatter for article knowledge files."""
    source: str              # Original URL
    fetched: str             # ISO8601 timestamp
    title: str               # Article title
    tags: list[str]          # Categorization tags
    quality_score: float     # 0.0-1.0 curation score
    author: str | None       # Original author (if known)
    published: str | None    # Original publish date (if known)
    asset_count: int         # Number of assets in directory
    content_type: str        # "article" | "tutorial" | "reference" | "guide"

def validate_article_frontmatter(data: dict[str, Any]) -> ArticleFrontmatter:
    """Validate article frontmatter fields."""
    required = {"source", "fetched", "title", "tags", "quality_score"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    # Validate types
    if not isinstance(data["source"], str) or not data["source"].startswith("http"):
        raise ValueError("source must be a valid HTTP(S) URL")

    if not isinstance(data["quality_score"], (int, float)) or not 0.0 <= data["quality_score"] <= 1.0:
        raise ValueError("quality_score must be float 0.0-1.0")

    # Validate timestamp
    try:
        datetime.fromisoformat(data["fetched"].replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"fetched must be ISO8601 timestamp: {e}")

    return ArticleFrontmatter(**data)
```

#### 1.2 ArticleEntry Dataclass

**File**: `co_cli/tools/articles.py` (new file)

```python
@dataclass
class ArticleEntry:
    """Article metadata and content."""
    slug: str                      # Filename without .md (e.g. "python-async-tutorial")
    path: Path                     # Full path to .md file
    content: str                   # Markdown body (without frontmatter)

    # From frontmatter
    source: str
    fetched: str
    title: str
    tags: list[str]
    quality_score: float
    author: str | None
    published: str | None
    asset_count: int
    content_type: str

    # Computed
    asset_dir: Path | None         # Path to assets/{slug}/ if exists
    has_assets: bool               # Whether asset_dir exists and non-empty
```

#### 1.3 Slug Generation

**File**: `co_cli/tools/articles.py`

```python
def generate_slug(title: str) -> str:
    """Generate URL-safe slug from article title.

    Examples:
        "Python Async I/O Tutorial" → "python-async-io-tutorial"
        "FastAPI: Dependency Injection" → "fastapi-dependency-injection"
        "SQLAlchemy ORM (Best Practices)" → "sqlalchemy-orm-best-practices"
    """
    # Lowercase, replace non-alphanumeric with hyphens, collapse multiple hyphens
    slug = re.sub(r"[^\w\s-]", "", title.lower())
    slug = re.sub(r"[-\s]+", "-", slug)
    slug = slug.strip("-")

    # Truncate to 80 chars (filesystem friendly)
    if len(slug) > 80:
        slug = slug[:80].rstrip("-")

    return slug

def ensure_unique_slug(slug: str, articles_dir: Path) -> str:
    """Ensure slug is unique by appending -2, -3, etc."""
    if not (articles_dir / f"{slug}.md").exists():
        return slug

    # Find next available suffix
    counter = 2
    while (articles_dir / f"{slug}-{counter}.md").exists():
        counter += 1

    return f"{slug}-{counter}"
```

---

### Part 2: Article Storage (2-3 hours)

#### 2.1 Directory Structure

**File**: `co_cli/config.py` (extend existing)

```python
@dataclass
class Settings:
    # ... existing fields ...

    # Article storage paths
    articles_dir: Path = field(default_factory=lambda: Path.cwd() / ".co-cli/knowledge/articles")
    articles_assets_dir: Path = field(default_factory=lambda: Path.cwd() / ".co-cli/knowledge/articles/assets")

    # Article retrieval settings
    article_max_results: int = 5          # Default max results for recall_article
    article_quality_threshold: float = 0.6  # Minimum quality to auto-propose save
```

#### 2.2 Article Scanner

**File**: `co_cli/tools/articles.py`

```python
def _load_all_articles(articles_dir: Path) -> list[ArticleEntry]:
    """Load all articles from directory.

    Scans *.md files in articles_dir, parses frontmatter, validates,
    and returns typed entries. Invalid files are skipped with warning.

    Returns:
        List of ArticleEntry, sorted by fetched timestamp (newest first)
    """
    if not articles_dir.exists():
        return []

    entries: list[ArticleEntry] = []

    for md_file in articles_dir.glob("*.md"):
        try:
            content = md_file.read_text(encoding="utf-8")
            frontmatter, body = parse_frontmatter(content)

            if not frontmatter:
                logger.warning(f"Skipping {md_file}: missing frontmatter")
                continue

            # Validate frontmatter
            article_meta = validate_article_frontmatter(frontmatter)

            # Compute asset directory
            slug = md_file.stem
            asset_dir = articles_dir / "assets" / slug
            has_assets = asset_dir.exists() and any(asset_dir.iterdir())

            entry = ArticleEntry(
                slug=slug,
                path=md_file,
                content=body.strip(),
                source=article_meta.source,
                fetched=article_meta.fetched,
                title=article_meta.title,
                tags=article_meta.tags,
                quality_score=article_meta.quality_score,
                author=article_meta.author,
                published=article_meta.published,
                asset_count=article_meta.asset_count,
                content_type=article_meta.content_type,
                asset_dir=asset_dir if has_assets else None,
                has_assets=has_assets,
            )

            entries.append(entry)

        except Exception as e:
            logger.warning(f"Failed to load article {md_file}: {e}")
            continue

    # Sort by fetched timestamp (newest first)
    entries.sort(key=lambda e: e.fetched, reverse=True)

    return entries
```

#### 2.3 Article Writer

**File**: `co_cli/tools/articles.py`

```python
def _write_article(
    articles_dir: Path,
    title: str,
    content: str,
    source: str,
    tags: list[str],
    quality_score: float,
    content_type: str,
    author: str | None = None,
    published: str | None = None,
    assets: dict[str, bytes] | None = None,
) -> Path:
    """Write article to disk with frontmatter.

    Args:
        articles_dir: Base articles directory
        title: Article title
        content: Markdown body
        source: Original URL
        tags: Categorization tags
        quality_score: Curation score (0.0-1.0)
        content_type: "article" | "tutorial" | "reference" | "guide"
        author: Original author (optional)
        published: Original publish date (optional)
        assets: Dict of filename → bytes for asset files (optional)

    Returns:
        Path to written .md file
    """
    articles_dir.mkdir(parents=True, exist_ok=True)

    # Generate unique slug
    slug = generate_slug(title)
    slug = ensure_unique_slug(slug, articles_dir)

    article_path = articles_dir / f"{slug}.md"

    # Write assets if provided
    asset_count = 0
    if assets:
        asset_dir = articles_dir / "assets" / slug
        asset_dir.mkdir(parents=True, exist_ok=True)

        for filename, data in assets.items():
            asset_path = asset_dir / filename
            asset_path.write_bytes(data)
            asset_count += 1

        logger.info(f"Wrote {asset_count} assets to {asset_dir}")

    # Build frontmatter
    frontmatter = {
        "source": source,
        "fetched": datetime.now(UTC).isoformat(),
        "title": title,
        "tags": tags,
        "quality_score": quality_score,
        "author": author,
        "published": published,
        "asset_count": asset_count,
        "content_type": content_type,
    }

    # Write article file
    article_content = f"---\n{yaml.dump(frontmatter, sort_keys=False)}---\n\n{content.strip()}\n"
    article_path.write_text(article_content, encoding="utf-8")

    logger.info(f"Wrote article to {article_path}")
    return article_path
```

---

### Part 3: Web Fetching & Quality Assessment (3-4 hours)

#### 3.1 Enhanced Web Fetch Tool

**File**: `co_cli/tools/web.py` (extend existing)

**Current**: `web_fetch` returns markdown-converted content
**Enhancement**: Return structured data including metadata extraction

```python
@dataclass
class WebFetchResult:
    """Result from fetching web content."""
    url: str                    # Final URL (after redirects)
    content: str                # Markdown-converted content
    title: str | None           # Extracted page title
    author: str | None          # Extracted author (meta tags)
    published: str | None       # Extracted publish date (meta tags)
    word_count: int             # Content word count
    images: list[str]           # Image URLs found in content
    code_blocks: int            # Number of code blocks

def web_fetch_structured(url: str, ctx: RunContext[CoDeps]) -> WebFetchResult:
    """Fetch web content with metadata extraction.

    Enhanced version of web_fetch that extracts structured metadata
    for article curation. Uses existing web_fetch for conversion,
    adds metadata parsing.
    """
    # Use existing web_fetch for conversion
    markdown = web_fetch(url, ctx)

    # Extract metadata (title from <h1>, author from meta, etc.)
    # This is a simplified version - real implementation would use
    # BeautifulSoup or similar for proper HTML parsing

    title = _extract_title(markdown)
    author = _extract_author(url)  # From meta tags if available
    published = _extract_published(url)  # From meta tags if available
    word_count = len(markdown.split())
    images = _extract_image_urls(markdown)
    code_blocks = markdown.count("```")

    return WebFetchResult(
        url=url,
        content=markdown,
        title=title,
        author=author,
        published=published,
        word_count=word_count,
        images=images,
        code_blocks=code_blocks,
    )
```

#### 3.2 Quality Assessment Function

**File**: `co_cli/tools/articles.py`

```python
async def _assess_article_quality(
    title: str,
    content: str,
    tags: list[str],
    word_count: int,
    code_blocks: int,
    agent: Agent,
) -> tuple[float, str]:
    """Assess article quality using LLM.

    Returns:
        (quality_score, reasoning)
        quality_score: 0.0-1.0 overall quality
        reasoning: Text explanation of assessment
    """
    assessment_prompt = f"""
Assess the quality of this article for saving to knowledge base.

**Article metadata:**
- Title: {title}
- Tags: {', '.join(tags)}
- Word count: {word_count}
- Code blocks: {code_blocks}

**Content preview:**
{content[:2000]}...

**Evaluation criteria:**
1. Relevance (0.0-1.0): Does it relate to user's technical work?
2. Accuracy (0.0-1.0): Is information correct and up-to-date?
3. Clarity (0.0-1.0): Is it well-organized and easy to follow?
4. Actionability (0.0-1.0): Does it include examples and practical guidance?

Respond with JSON:
{{
  "relevance": 0.8,
  "accuracy": 0.9,
  "clarity": 0.7,
  "actionability": 0.85,
  "overall": 0.81,
  "reasoning": "High-quality tutorial with clear examples..."
}}
"""

    # Run assessment using agent (no tools needed)
    result = await agent.run(assessment_prompt)

    # Parse JSON response
    try:
        assessment = json.loads(result.data)
        quality_score = assessment["overall"]
        reasoning = assessment["reasoning"]
        return (quality_score, reasoning)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"Failed to parse quality assessment: {e}")
        return (0.5, "Assessment failed, defaulting to neutral score")
```

---

### Part 4: Agent Tools (3-4 hours)

#### 4.1 save_article Tool

**File**: `co_cli/tools/articles.py`

```python
@agent.tool(requires_approval=True)
async def save_article(
    ctx: RunContext[CoDeps],
    source: str,
    title: str | None = None,
    tags: list[str] | None = None,
    content_type: str = "article",
    fetch_assets: bool = False,
) -> dict[str, Any]:
    """Save web content as an article to knowledge base.

    Fetches content from URL, assesses quality, and saves to
    .co-cli/knowledge/articles/ with frontmatter metadata.

    **When to use:**
    - User explicitly asks to save a URL: "Save this article about FastAPI"
    - You found valuable content during research that user should reference later
    - Documentation or tutorial that's relevant to user's project

    **When NOT to use:**
    - For facts learned in conversation → use save_memory instead
    - For low-quality content (quality_score < 0.6) unless user insists
    - For content already in knowledge base (check with recall_article first)

    Args:
        source: URL to fetch content from
        title: Article title (auto-detected if not provided)
        tags: Categorization tags (e.g. ["python", "async", "tutorial"])
        content_type: "article" | "tutorial" | "reference" | "guide"
        fetch_assets: If True, download and save images/files (experimental)

    Returns:
        {
            "display": "Saved article 'Python Async Tutorial' with 3 assets",
            "slug": "python-async-tutorial",
            "quality_score": 0.85,
            "word_count": 3500,
            "asset_count": 3
        }

    Requires user approval before saving.
    """
    deps = ctx.deps
    articles_dir = deps.settings.articles_dir

    # Fetch content
    deps.console.print(f"[info]Fetching content from {source}...[/]")
    fetch_result = web_fetch_structured(source, ctx)

    # Use auto-detected title if not provided
    if title is None:
        title = fetch_result.title or "Untitled Article"

    # Auto-detect tags if not provided (simple heuristic)
    if tags is None:
        tags = _auto_detect_tags(fetch_result.content)

    # Assess quality
    deps.console.print("[info]Assessing article quality...[/]")
    quality_score, reasoning = await _assess_article_quality(
        title=title,
        content=fetch_result.content,
        tags=tags,
        word_count=fetch_result.word_count,
        code_blocks=fetch_result.code_blocks,
        agent=ctx.deps.agent,
    )

    # Warn if low quality
    if quality_score < deps.settings.article_quality_threshold:
        deps.console.print(
            f"[warning]Quality score {quality_score:.2f} is below threshold "
            f"{deps.settings.article_quality_threshold:.2f}[/]"
        )
        deps.console.print(f"[dim]Reasoning: {reasoning}[/]")

    # Fetch assets if requested
    assets: dict[str, bytes] | None = None
    if fetch_assets and fetch_result.images:
        deps.console.print(f"[info]Fetching {len(fetch_result.images)} assets...[/]")
        assets = await _fetch_assets(fetch_result.images)

    # Write article (approval happens before this executes)
    article_path = _write_article(
        articles_dir=articles_dir,
        title=title,
        content=fetch_result.content,
        source=source,
        tags=tags,
        quality_score=quality_score,
        content_type=content_type,
        author=fetch_result.author,
        published=fetch_result.published,
        assets=assets,
    )

    slug = article_path.stem
    asset_count = len(assets) if assets else 0

    # Display message
    display = f"Saved article '{title}'"
    if asset_count > 0:
        display += f" with {asset_count} asset(s)"
    display += f" (quality: {quality_score:.2f})"

    return {
        "display": display,
        "slug": slug,
        "quality_score": quality_score,
        "word_count": fetch_result.word_count,
        "asset_count": asset_count,
    }
```

#### 4.2 recall_article Tool

**File**: `co_cli/tools/articles.py`

```python
@agent.tool()
def recall_article(
    ctx: RunContext[CoDeps],
    query: str,
    tags: list[str] | None = None,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Search articles in knowledge base.

    Searches article titles, content, tags, and source URLs.
    Returns most relevant articles sorted by recency.

    **Use this proactively** when:
    - User asks about a topic you might have articles for
    - You need reference documentation to answer a question
    - User wants to know what articles exist on a topic

    Args:
        query: Search keywords (e.g. "python async", "fastapi")
        tags: Filter by tags (AND logic - all must match)
        max_results: Max results to return (default from settings)

    Returns:
        {
            "display": "Found 3 article(s) matching 'python async':\n1. Python Async...",
            "results": [
                {
                    "slug": "python-async-tutorial",
                    "title": "Python Async I/O Tutorial",
                    "source": "https://...",
                    "excerpt": "First 200 chars of content...",
                    "tags": ["python", "async"],
                    "fetched": "2026-02-10T10:30:00Z",
                    "quality_score": 0.85,
                    "has_assets": true
                },
                ...
            ],
            "count": 3
        }
    """
    deps = ctx.deps
    articles_dir = deps.settings.articles_dir
    max_results = max_results or deps.settings.article_max_results

    # Load all articles
    articles = _load_all_articles(articles_dir)

    if not articles:
        return {
            "display": "No articles in knowledge base yet. Use save_article to add content.",
            "results": [],
            "count": 0,
        }

    # Filter by query (case-insensitive substring match)
    query_lower = query.lower()
    matched = [
        a for a in articles
        if query_lower in a.title.lower()
        or query_lower in a.content.lower()
        or any(query_lower in tag.lower() for tag in a.tags)
        or query_lower in a.source.lower()
    ]

    # Filter by tags if provided (AND logic)
    if tags:
        tags_lower = [t.lower() for t in tags]
        matched = [
            a for a in matched
            if all(any(tag in article_tag.lower() for article_tag in a.tags) for tag in tags_lower)
        ]

    # Sort by quality score (highest first), then recency
    matched.sort(key=lambda a: (a.quality_score, a.fetched), reverse=True)

    # Limit results
    matched = matched[:max_results]

    # Format results
    results = []
    for article in matched:
        excerpt = article.content[:200].strip()
        if len(article.content) > 200:
            excerpt += "..."

        results.append({
            "slug": article.slug,
            "title": article.title,
            "source": article.source,
            "excerpt": excerpt,
            "tags": article.tags,
            "fetched": article.fetched,
            "quality_score": article.quality_score,
            "has_assets": article.has_assets,
        })

    # Format display
    if not results:
        display = f"No articles found matching '{query}'"
    else:
        display = f"Found {len(results)} article(s) matching '{query}':\n"
        for i, r in enumerate(results, 1):
            assets_marker = " 📎" if r["has_assets"] else ""
            display += f"\n{i}. {r['title']}{assets_marker}"
            display += f"\n   Source: {r['source']}"
            display += f"\n   Tags: {', '.join(r['tags'])}"
            display += f"\n   Quality: {r['quality_score']:.2f}"
            display += f"\n   {r['excerpt']}\n"

    return {
        "display": display,
        "results": results,
        "count": len(results),
    }
```

#### 4.3 list_articles Tool

**File**: `co_cli/tools/articles.py`

```python
@agent.tool()
def list_articles(ctx: RunContext[CoDeps]) -> dict[str, Any]:
    """List all articles in knowledge base.

    Shows article titles, sources, quality scores, and tags.
    Sorted by fetch date (newest first).

    Returns:
        {
            "display": "3 article(s) in knowledge base:\n1. Python Async...",
            "articles": [
                {
                    "slug": "python-async-tutorial",
                    "title": "Python Async I/O Tutorial",
                    "source": "https://...",
                    "tags": ["python", "async"],
                    "fetched": "2026-02-10",
                    "quality_score": 0.85,
                    "word_count": 3500,
                    "has_assets": true
                },
                ...
            ],
            "count": 3
        }
    """
    deps = ctx.deps
    articles_dir = deps.settings.articles_dir

    articles = _load_all_articles(articles_dir)

    if not articles:
        return {
            "display": "No articles in knowledge base yet. Use save_article to add content.",
            "articles": [],
            "count": 0,
        }

    # Format for display
    articles_data = []
    for article in articles:
        # Format date as YYYY-MM-DD
        fetched_date = article.fetched.split("T")[0]

        articles_data.append({
            "slug": article.slug,
            "title": article.title,
            "source": article.source,
            "tags": article.tags,
            "fetched": fetched_date,
            "quality_score": article.quality_score,
            "word_count": len(article.content.split()),
            "has_assets": article.has_assets,
        })

    # Build display string
    display = f"{len(articles)} article(s) in knowledge base:\n"
    for i, a in enumerate(articles_data, 1):
        assets_marker = " 📎" if a["has_assets"] else ""
        display += f"\n{i}. {a['title']}{assets_marker}"
        display += f"\n   Fetched: {a['fetched']} | Quality: {a['quality_score']:.2f}"
        display += f"\n   Tags: {', '.join(a['tags'])}"
        display += f"\n   Source: {a['source']}\n"

    return {
        "display": display,
        "articles": articles_data,
        "count": len(articles),
    }
```

---

### Part 5: Multimodal Asset Handling (2-3 hours)

#### 5.1 Asset Fetcher

**File**: `co_cli/tools/articles.py`

```python
async def _fetch_assets(image_urls: list[str]) -> dict[str, bytes]:
    """Fetch images/assets from URLs.

    Downloads images referenced in article content for local storage.
    Supports PNG, JPG, GIF, SVG, and PDF files.

    Args:
        image_urls: List of image URLs to fetch

    Returns:
        Dict of filename → bytes for successfully fetched assets
    """
    import httpx

    assets: dict[str, bytes] = {}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for url in image_urls:
            try:
                # Extract filename from URL
                filename = Path(url).name

                # Skip if no extension or unsupported type
                ext = Path(filename).suffix.lower()
                if ext not in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf"}:
                    logger.debug(f"Skipping unsupported asset type: {filename}")
                    continue

                # Fetch content
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()

                # Validate content type
                content_type = response.headers.get("content-type", "")
                if not any(t in content_type for t in ["image/", "application/pdf"]):
                    logger.warning(f"Unexpected content type {content_type} for {url}")
                    continue

                assets[filename] = response.content
                logger.info(f"Fetched asset: {filename} ({len(response.content)} bytes)")

            except Exception as e:
                logger.warning(f"Failed to fetch asset {url}: {e}")
                continue

    return assets
```

#### 5.2 Asset Path Rewriter

**File**: `co_cli/tools/articles.py`

```python
def _rewrite_asset_paths(markdown: str, slug: str) -> str:
    """Rewrite absolute image URLs to local asset paths.

    Converts:
        ![Diagram](https://example.com/images/diagram.png)
    To:
        ![Diagram](assets/python-async-tutorial/diagram.png)

    Args:
        markdown: Original markdown content with absolute URLs
        slug: Article slug for asset directory

    Returns:
        Markdown with rewritten local paths
    """
    import re

    # Pattern: ![alt](http://...)
    def rewrite_match(match):
        alt_text = match.group(1)
        url = match.group(2)

        # Extract filename
        filename = Path(url).name

        # Build local path
        local_path = f"assets/{slug}/{filename}"

        return f"![{alt_text}]({local_path})"

    # Rewrite image references
    rewritten = re.sub(
        r"!\[(.*?)\]\((https?://[^\)]+)\)",
        rewrite_match,
        markdown
    )

    return rewritten
```

#### 5.3 PDF Text Extraction

**File**: `co_cli/tools/articles.py`

```python
def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract text from PDF for full-text search.

    Uses pypdf for extraction. Returns markdown-formatted text.
    Original PDF saved in assets/ for reference.

    Args:
        pdf_bytes: PDF file content

    Returns:
        Markdown-formatted extracted text
    """
    try:
        import pypdf
        from io import BytesIO

        reader = pypdf.PdfReader(BytesIO(pdf_bytes))

        # Extract text from all pages
        pages = []
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text()
            if text.strip():
                pages.append(f"## Page {i}\n\n{text.strip()}")

        if not pages:
            return "*(PDF text extraction failed - no readable content)*"

        return "\n\n".join(pages)

    except ImportError:
        logger.warning("pypdf not installed, PDF text extraction unavailable")
        return "*(Install pypdf to extract PDF text)*"
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        return f"*(PDF extraction error: {e})*"
```

---

### Part 6: Integration & Commands (1-2 hours)

#### 6.1 Tool Registration

**File**: `co_cli/agent.py`

```python
def get_agent(deps: CoDeps) -> Agent:
    """Create and configure agent with tools."""
    # ... existing tools ...

    # Article knowledge tools
    agent.tool_plain()(tools.articles.save_article)
    agent.tool_plain()(tools.articles.recall_article)
    agent.tool_plain()(tools.articles.list_articles)

    return agent
```

#### 6.2 Slash Commands

**File**: `co_cli/_commands.py`

```python
def handle_slash_command(command: str, rest: str, ctx: ChatContext) -> bool:
    """Handle slash commands. Returns True if handled."""
    # ... existing commands ...

    if command == "articles":
        """List all articles: /articles"""
        result = tools.articles.list_articles(ctx.run_context)
        ctx.console.print(result["display"])
        return True

    if command == "article":
        """Show article details: /article <slug>"""
        if not rest:
            ctx.console.print("[error]Usage: /article <slug>[/]")
            return True

        # Load and display article
        articles_dir = ctx.deps.settings.articles_dir
        article_path = articles_dir / f"{rest}.md"

        if not article_path.exists():
            ctx.console.print(f"[error]Article not found: {rest}[/]")
            return True

        # Read and display
        content = article_path.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)

        # Format output
        ctx.console.print(f"\n[bold]{frontmatter['title']}[/]\n")
        ctx.console.print(f"Source: {frontmatter['source']}")
        ctx.console.print(f"Quality: {frontmatter['quality_score']:.2f}")
        ctx.console.print(f"Tags: {', '.join(frontmatter['tags'])}\n")
        ctx.console.print(body)

        return True

    if command == "forget-article":
        """Delete article: /forget-article <slug>"""
        if not rest:
            ctx.console.print("[error]Usage: /forget-article <slug>[/]")
            return True

        articles_dir = ctx.deps.settings.articles_dir
        article_path = articles_dir / f"{rest}.md"
        asset_dir = articles_dir / "assets" / rest

        if not article_path.exists():
            ctx.console.print(f"[error]Article not found: {rest}[/]")
            return True

        # Delete article and assets
        article_path.unlink()
        if asset_dir.exists():
            shutil.rmtree(asset_dir)
            ctx.console.print(f"[success]Deleted article '{rest}' and its assets[/]")
        else:
            ctx.console.print(f"[success]Deleted article '{rest}'[/]")

        return True

    return False
```

#### 6.3 Status Integration

**File**: `co_cli/status.py`

```python
def get_status() -> StatusInfo:
    """Get system health status."""
    # ... existing checks ...

    # Check articles
    articles_dir = Path.cwd() / ".co-cli/knowledge/articles"
    if articles_dir.exists():
        article_count = len(list(articles_dir.glob("*.md")))
        asset_dirs = articles_dir / "assets"
        asset_count = sum(1 for _ in asset_dirs.rglob("*") if _.is_file()) if asset_dirs.exists() else 0

        status.articles_count = article_count
        status.articles_assets_count = asset_count

    return status
```

---

### Part 7: Testing (2-3 hours)

#### 7.1 Article Storage Tests

**File**: `tests/test_articles_storage.py`

```python
def test_slug_generation():
    """Test article slug generation."""
    assert generate_slug("Python Async I/O Tutorial") == "python-async-io-tutorial"
    assert generate_slug("FastAPI: Dependency Injection") == "fastapi-dependency-injection"
    assert generate_slug("SQLAlchemy ORM (Best Practices)") == "sqlalchemy-orm-best-practices"

    # Truncation
    long_title = "A" * 100
    slug = generate_slug(long_title)
    assert len(slug) <= 80

def test_unique_slug(tmp_path):
    """Test slug uniqueness enforcement."""
    articles_dir = tmp_path / "articles"
    articles_dir.mkdir()

    # Create first article
    (articles_dir / "test-article.md").touch()

    # Ensure second gets -2 suffix
    slug = ensure_unique_slug("test-article", articles_dir)
    assert slug == "test-article-2"

    # Create -2, ensure third gets -3
    (articles_dir / "test-article-2.md").touch()
    slug = ensure_unique_slug("test-article", articles_dir)
    assert slug == "test-article-3"

def test_write_article(tmp_path):
    """Test article writing with frontmatter."""
    articles_dir = tmp_path / "articles"

    path = _write_article(
        articles_dir=articles_dir,
        title="Test Article",
        content="# Heading\n\nContent here.",
        source="https://example.com/article",
        tags=["test", "example"],
        quality_score=0.85,
        content_type="article",
    )

    assert path.exists()
    assert path.name == "test-article.md"

    # Verify frontmatter
    content = path.read_text()
    frontmatter, body = parse_frontmatter(content)

    assert frontmatter["title"] == "Test Article"
    assert frontmatter["source"] == "https://example.com/article"
    assert frontmatter["tags"] == ["test", "example"]
    assert frontmatter["quality_score"] == 0.85
    assert "fetched" in frontmatter

    assert "# Heading" in body

def test_write_article_with_assets(tmp_path):
    """Test article writing with asset files."""
    articles_dir = tmp_path / "articles"

    assets = {
        "diagram.png": b"fake-png-data",
        "example.py": b"print('hello')",
    }

    path = _write_article(
        articles_dir=articles_dir,
        title="Article With Assets",
        content="See diagram below.",
        source="https://example.com/tutorial",
        tags=["tutorial"],
        quality_score=0.9,
        content_type="tutorial",
        assets=assets,
    )

    # Verify article
    assert path.exists()

    # Verify assets
    asset_dir = articles_dir / "assets" / "article-with-assets"
    assert asset_dir.exists()
    assert (asset_dir / "diagram.png").read_bytes() == b"fake-png-data"
    assert (asset_dir / "example.py").read_bytes() == b"print('hello')"

    # Verify asset_count in frontmatter
    content = path.read_text()
    frontmatter, _ = parse_frontmatter(content)
    assert frontmatter["asset_count"] == 2
```

#### 7.2 Article Loading Tests

**File**: `tests/test_articles_loading.py`

```python
def test_load_empty_directory(tmp_path):
    """Test loading from empty directory."""
    articles_dir = tmp_path / "articles"
    articles = _load_all_articles(articles_dir)
    assert articles == []

def test_load_articles(tmp_path):
    """Test loading multiple articles."""
    articles_dir = tmp_path / "articles"
    articles_dir.mkdir()

    # Create test articles
    for i in range(3):
        _write_article(
            articles_dir=articles_dir,
            title=f"Article {i+1}",
            content=f"Content for article {i+1}",
            source=f"https://example.com/article-{i+1}",
            tags=["test"],
            quality_score=0.7 + i * 0.1,
            content_type="article",
        )

    articles = _load_all_articles(articles_dir)

    assert len(articles) == 3
    assert all(isinstance(a, ArticleEntry) for a in articles)

    # Verify sorting (newest first)
    assert articles[0].title == "Article 3"  # Last written = newest
    assert articles[2].title == "Article 1"  # First written = oldest

def test_load_skips_invalid(tmp_path):
    """Test that invalid files are skipped."""
    articles_dir = tmp_path / "articles"
    articles_dir.mkdir()

    # Valid article
    _write_article(
        articles_dir=articles_dir,
        title="Valid Article",
        content="Content",
        source="https://example.com",
        tags=["test"],
        quality_score=0.8,
        content_type="article",
    )

    # Invalid article (missing frontmatter)
    (articles_dir / "invalid.md").write_text("No frontmatter here")

    # Invalid article (bad frontmatter)
    (articles_dir / "bad.md").write_text("---\ntitle: Missing required fields\n---\nBody")

    articles = _load_all_articles(articles_dir)

    # Only valid article loaded
    assert len(articles) == 1
    assert articles[0].title == "Valid Article"

def test_load_with_assets(tmp_path):
    """Test loading articles with asset detection."""
    articles_dir = tmp_path / "articles"

    # Article with assets
    _write_article(
        articles_dir=articles_dir,
        title="Article With Assets",
        content="Content",
        source="https://example.com",
        tags=["test"],
        quality_score=0.8,
        content_type="article",
        assets={"image.png": b"fake-data"},
    )

    # Article without assets
    _write_article(
        articles_dir=articles_dir,
        title="Article Without Assets",
        content="Content",
        source="https://example.com",
        tags=["test"],
        quality_score=0.7,
        content_type="article",
    )

    articles = _load_all_articles(articles_dir)

    assert len(articles) == 2

    # Find articles by title
    with_assets = next(a for a in articles if a.title == "Article With Assets")
    without_assets = next(a for a in articles if a.title == "Article Without Assets")

    assert with_assets.has_assets is True
    assert with_assets.asset_dir is not None

    assert without_assets.has_assets is False
    assert without_assets.asset_dir is None
```

#### 7.3 Tool Tests

**File**: `tests/test_articles_tools.py`

```python
@pytest.mark.asyncio
async def test_save_article_basic(test_deps):
    """Test basic article saving."""
    ctx = create_test_context(test_deps)

    # Mock web_fetch_structured
    with patch("co_cli.tools.articles.web_fetch_structured") as mock_fetch:
        mock_fetch.return_value = WebFetchResult(
            url="https://example.com/article",
            content="# Tutorial\n\nContent here.",
            title="Test Tutorial",
            author="John Doe",
            published="2026-01-15",
            word_count=500,
            images=[],
            code_blocks=2,
        )

        # Mock quality assessment
        with patch("co_cli.tools.articles._assess_article_quality") as mock_assess:
            mock_assess.return_value = (0.85, "High quality tutorial")

            result = await save_article(
                ctx=ctx,
                source="https://example.com/article",
                tags=["python", "tutorial"],
            )

    assert result["quality_score"] == 0.85
    assert "slug" in result

    # Verify file created
    articles_dir = test_deps.settings.articles_dir
    article_files = list(articles_dir.glob("*.md"))
    assert len(article_files) == 1

    # Verify content
    content = article_files[0].read_text()
    assert "Test Tutorial" in content
    assert "# Tutorial" in content

@pytest.mark.asyncio
async def test_save_article_with_assets(test_deps):
    """Test article saving with asset fetching."""
    ctx = create_test_context(test_deps)

    with patch("co_cli.tools.articles.web_fetch_structured") as mock_fetch:
        mock_fetch.return_value = WebFetchResult(
            url="https://example.com/article",
            content="![Diagram](https://example.com/img.png)\n\nContent.",
            title="Visual Tutorial",
            author=None,
            published=None,
            word_count=300,
            images=["https://example.com/img.png"],
            code_blocks=0,
        )

        with patch("co_cli.tools.articles._assess_article_quality") as mock_assess:
            mock_assess.return_value = (0.75, "Good visual content")

            with patch("co_cli.tools.articles._fetch_assets") as mock_assets:
                mock_assets.return_value = {"img.png": b"fake-image-data"}

                result = await save_article(
                    ctx=ctx,
                    source="https://example.com/article",
                    tags=["tutorial"],
                    fetch_assets=True,
                )

    assert result["asset_count"] == 1

    # Verify asset directory created
    articles_dir = test_deps.settings.articles_dir
    slug = result["slug"]
    asset_dir = articles_dir / "assets" / slug
    assert asset_dir.exists()
    assert (asset_dir / "img.png").read_bytes() == b"fake-image-data"

def test_recall_article_empty(test_deps):
    """Test recall with no articles."""
    ctx = create_test_context(test_deps)

    result = recall_article(ctx, query="python")

    assert result["count"] == 0
    assert "No articles" in result["display"]

def test_recall_article_match(test_deps):
    """Test recall with matching articles."""
    articles_dir = test_deps.settings.articles_dir

    # Create test articles
    _write_article(
        articles_dir=articles_dir,
        title="Python Async Tutorial",
        content="Learn async/await in Python",
        source="https://example.com/python-async",
        tags=["python", "async"],
        quality_score=0.9,
        content_type="tutorial",
    )

    _write_article(
        articles_dir=articles_dir,
        title="FastAPI Guide",
        content="Build APIs with FastAPI",
        source="https://example.com/fastapi",
        tags=["python", "fastapi"],
        quality_score=0.8,
        content_type="guide",
    )

    ctx = create_test_context(test_deps)

    # Search by keyword
    result = recall_article(ctx, query="python")

    assert result["count"] == 2
    assert len(result["results"]) == 2

    # Verify sorting (quality score first)
    assert result["results"][0]["title"] == "Python Async Tutorial"  # 0.9 quality
    assert result["results"][1]["title"] == "FastAPI Guide"  # 0.8 quality

def test_recall_article_with_tags(test_deps):
    """Test recall with tag filtering."""
    articles_dir = test_deps.settings.articles_dir

    _write_article(
        articles_dir=articles_dir,
        title="Python Async",
        content="Content",
        source="https://example.com/1",
        tags=["python", "async"],
        quality_score=0.8,
        content_type="article",
    )

    _write_article(
        articles_dir=articles_dir,
        title="Python ORM",
        content="Content",
        source="https://example.com/2",
        tags=["python", "database"],
        quality_score=0.7,
        content_type="article",
    )

    ctx = create_test_context(test_deps)

    # Filter by tag
    result = recall_article(ctx, query="python", tags=["async"])

    assert result["count"] == 1
    assert result["results"][0]["title"] == "Python Async"

def test_list_articles(test_deps):
    """Test article listing."""
    articles_dir = test_deps.settings.articles_dir

    # Create multiple articles
    for i in range(3):
        _write_article(
            articles_dir=articles_dir,
            title=f"Article {i+1}",
            content=f"Content {i+1}",
            source=f"https://example.com/{i+1}",
            tags=["test"],
            quality_score=0.7 + i * 0.1,
            content_type="article",
        )

    ctx = create_test_context(test_deps)
    result = list_articles(ctx)

    assert result["count"] == 3
    assert len(result["articles"]) == 3

    # Verify display format
    assert "3 article(s)" in result["display"]
    assert "Article 1" in result["display"]
```

#### 7.4 Multimodal Tests

**File**: `tests/test_articles_multimodal.py`

```python
@pytest.mark.asyncio
async def test_fetch_assets_success():
    """Test successful asset fetching."""
    image_urls = [
        "https://example.com/image1.png",
        "https://example.com/image2.jpg",
    ]

    with patch("httpx.AsyncClient") as mock_client:
        mock_response = Mock()
        mock_response.content = b"fake-image-data"
        mock_response.headers = {"content-type": "image/png"}
        mock_response.raise_for_status = Mock()

        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_response
        )

        assets = await _fetch_assets(image_urls)

    assert len(assets) == 2
    assert "image1.png" in assets
    assert "image2.jpg" in assets

@pytest.mark.asyncio
async def test_fetch_assets_filters_invalid():
    """Test that invalid asset types are filtered."""
    image_urls = [
        "https://example.com/image.png",      # Valid
        "https://example.com/script.js",      # Invalid
        "https://example.com/doc.pdf",        # Valid
    ]

    with patch("httpx.AsyncClient") as mock_client:
        # Should only fetch png and pdf
        assets = await _fetch_assets(image_urls)

    # js file should be skipped
    assert "script.js" not in assets

def test_rewrite_asset_paths():
    """Test asset path rewriting in markdown."""
    markdown = """
# Tutorial

![Diagram 1](https://example.com/images/diagram1.png)

Some text.

![Diagram 2](https://cdn.example.com/img/diagram2.jpg)
"""

    rewritten = _rewrite_asset_paths(markdown, slug="test-tutorial")

    assert "assets/test-tutorial/diagram1.png" in rewritten
    assert "assets/test-tutorial/diagram2.jpg" in rewritten
    assert "https://example.com" not in rewritten
    assert "https://cdn.example.com" not in rewritten

def test_extract_pdf_text():
    """Test PDF text extraction."""
    # Create minimal PDF for testing
    # (In real test, use a fixture PDF file)
    pdf_bytes = b"%PDF-1.4\n..."  # Minimal valid PDF

    with patch("pypdf.PdfReader") as mock_reader:
        mock_page = Mock()
        mock_page.extract_text.return_value = "Sample PDF text content"

        mock_reader.return_value.pages = [mock_page]

        text = _extract_pdf_text(pdf_bytes)

    assert "Sample PDF text content" in text
    assert "## Page 1" in text
```

---

### Part 8: Documentation Updates (1 hour)

#### 8.1 Update DESIGN-14

**File**: `docs/DESIGN-14-memory-lifecycle-system.md`

Add new section after § 2 (Knowledge Addition Triggering):

```markdown
### 2.5 Article Knowledge (Phase 2.6)

**Extends memory system with web-fetched content.**

**Characteristics:**
- Larger than memories (500-5000 words vs 500 chars)
- Web-sourced (not learned from conversation)
- Quality-gated (LLM assessment before save)
- Multimodal (text + images + PDFs + code)
- Manually curated (no automatic decay)

**Tools:**
- `save_article(source, title, tags, fetch_assets)` — Fetch and save web content
- `recall_article(query, tags, max_results)` — Search articles
- `list_articles()` — List all articles

**Storage:**
- Articles: `.co-cli/knowledge/articles/*.md`
- Assets: `.co-cli/knowledge/articles/assets/{slug}/`

**Comparison with memories:**
| Aspect | Memory | Article |
|--------|--------|---------|
| Source | Conversation | Web fetch |
| Size | Small (500 chars) | Large (500-5000 words) |
| Lifecycle | Dedup + decay | Manual only |
| Multimodal | Text only | Text + images + PDFs |

See `docs/TODO-co-evolution-phase2.6-articles-knowledge.md` for implementation.
```

#### 8.2 Update GLOSSARY

**File**: `docs/GLOSSARY-knowledge-system.md`

Update line 18 to show implementation status:

```markdown
└── Articles (fetched, curated) — Phase 2.6 📝
    └── Articles (.co-cli/knowledge/articles/*.md + assets/)
```

#### 8.3 Update ROADMAP

**File**: `docs/ROADMAP-co-evolution.md`

Add Phase 2.6 to status table:

```markdown
| **2.6** | Articles Knowledge | 📝 DOCUMENTED | 12-16h | TODO-co-evolution-phase2.6-articles-knowledge.md | MEDIUM |
```

#### 8.4 Update CLAUDE.md

**File**: `CLAUDE.md`

Update "Internal Knowledge" section:

```markdown
## Internal Knowledge

Co loads persistent knowledge from markdown files:

**Files:**
- `~/.config/co-cli/knowledge/context.md` — Global context (3 KiB budget)
- `.co-cli/knowledge/context.md` — Project context (7 KiB budget, overrides global)
- `.co-cli/knowledge/memories/*.md` — On-demand memories (agent-searchable)
- `.co-cli/knowledge/articles/*.md` — Curated web content (multimodal) [Phase 2.6]

**Tools:**
- `save_memory(content, tags)` — Save persistent memory
- `recall_memory(query, max_results)` — Search memories
- `list_memories()` — List memories
- `save_article(source, title, tags)` — Save web article [Phase 2.6]
- `recall_article(query, tags)` — Search articles [Phase 2.6]
- `list_articles()` — List articles [Phase 2.6]
```

---

## Success Criteria

### Functional Requirements

- [ ] **F1**: Articles stored in `.co-cli/knowledge/articles/*.md` with YAML frontmatter
- [ ] **F2**: Assets stored in `.co-cli/knowledge/articles/assets/{slug}/` directory
- [ ] **F3**: `save_article` tool fetches web content and saves with approval
- [ ] **F4**: `recall_article` tool searches articles by keyword and tags
- [ ] **F5**: `list_articles` tool shows all articles with metadata
- [ ] **F6**: Quality assessment (0.0-1.0 score) before saving articles
- [ ] **F7**: Multimodal support: images, PDFs, code files in assets
- [ ] **F8**: Asset path rewriting (absolute URLs → local paths)
- [ ] **F9**: PDF text extraction for searchability
- [ ] **F10**: Slug generation from article titles (unique, filesystem-safe)

### Integration Requirements

- [ ] **I1**: Tools registered in `get_agent()` alongside memory tools
- [ ] **I2**: Slash commands: `/articles`, `/article <slug>`, `/forget-article <slug>`
- [ ] **I3**: Status check shows article count and asset count
- [ ] **I4**: Articles searchable alongside memories (unified knowledge base)
- [ ] **I5**: Tool return format matches memory tools (`display` + metadata)

### Quality Requirements

- [ ] **Q1**: 15+ functional tests (storage, loading, tools, multimodal)
- [ ] **Q2**: Test coverage >85% for new code
- [ ] **Q3**: All tests pass without mocks (functional tests only)
- [ ] **Q4**: Invalid articles skipped with warning (no crashes)
- [ ] **Q5**: Asset fetching handles errors gracefully (continues on failure)

### Documentation Requirements

- [ ] **D1**: DESIGN-14 updated with Article Knowledge section
- [ ] **D2**: GLOSSARY updated with implementation status
- [ ] **D3**: ROADMAP updated with Phase 2.6 entry
- [ ] **D4**: CLAUDE.md updated with article tools
- [ ] **D5**: Tool docstrings include "when to use" examples

### Performance Requirements

- [ ] **P1**: Article loading <100ms for <50 articles (grep-based search)
- [ ] **P2**: Asset fetching timeout: 30s per asset (fail gracefully)
- [ ] **P3**: Quality assessment: <5s per article (LLM call)
- [ ] **P4**: No blocking on asset fetching (async/await)

---

## Migration & Rollout

### No Breaking Changes

Phase 2.6 is additive — no changes to existing memory system.

**Zero migration needed**: Articles are a new knowledge type with separate tools and storage.

### Graceful Degradation

- If `.co-cli/knowledge/articles/` doesn't exist → tools return "no articles yet"
- If `web_fetch` fails → display error, don't save article
- If quality assessment fails → default score 0.5, warn user
- If asset fetching fails → save article without assets, continue

### Rollout Plan

1. Ship article storage and loading (Part 1-2)
2. Ship basic tools without multimodal (Part 4, `fetch_assets=False`)
3. Ship quality assessment (Part 3)
4. Ship multimodal support (Part 5)
5. Ship slash commands (Part 6)
6. Update documentation (Part 8)

**MVP cutline**: Parts 1-2-4 only (no quality gate, no multimodal) = 6-8 hours

---

## Future Enhancements (Post-MVP)

### Phase 3: Search Evolution

**Current (Phase 2.6)**: Grep-based keyword search
**Phase 3.1**: SQLite FTS5 (BM25 keyword search, 10K articles)
**Phase 3.2**: Hybrid FTS5 + vectors (semantic search, unlimited)

**Migration**: Add indices to existing markdown files, no schema change

### Phase 4: Article Lifecycle

**Automatic staleness detection**:
- Check if source URL returns 404 (article deleted)
- Check if source URL content changed significantly (article updated)
- Propose re-fetch or mark as stale

**Automatic tagging refinement**:
- LLM suggests additional tags based on content
- User approves tag additions

### Phase 5: Article Collections

**Group related articles**:
- Collections: `.co-cli/knowledge/collections/{name}.md`
- Frontmatter: `articles: [slug1, slug2, ...]`
- Tool: `list_collection(name)` shows all articles in collection

### Phase 6: Obsidian Integration

**Sync articles to Obsidian vault**:
- Setting: `obsidian_sync_articles: true`
- On `save_article`, also write to Obsidian vault
- Frontmatter includes `co_article: true` for identification

---

## Dependencies

### Python Packages

**Required**:
- `pyyaml` — YAML frontmatter parsing (already in project)
- `httpx` — Async HTTP for asset fetching (already in project)
- `datetime` — Timestamp handling (stdlib)

**Optional**:
- `pypdf` — PDF text extraction (optional, graceful degradation)
- `pillow` — Image processing/optimization (future, Phase 3+)

**Install optional deps**:
```bash
uv add pypdf  # PDF text extraction
```

### External APIs

**None** — All fetching uses existing `web_fetch` tool (no new API dependencies)

---

## Implementation Checklist

### Part 1: Core Data Model (2-3 hours)
- [ ] `ArticleFrontmatter` dataclass in `_frontmatter.py`
- [ ] `validate_article_frontmatter()` function
- [ ] `ArticleEntry` dataclass in `tools/articles.py`
- [ ] `generate_slug()` function
- [ ] `ensure_unique_slug()` function
- [ ] 5+ tests for slug generation

### Part 2: Article Storage (2-3 hours)
- [ ] `articles_dir` and `articles_assets_dir` in Settings
- [ ] `_load_all_articles()` scanner function
- [ ] `_write_article()` writer function
- [ ] 8+ tests for storage (write, load, assets, invalid)

### Part 3: Web Fetching & Quality (3-4 hours)
- [ ] `WebFetchResult` dataclass
- [ ] `web_fetch_structured()` function (extends existing `web_fetch`)
- [ ] `_assess_article_quality()` function (LLM-based)
- [ ] Quality assessment prompt in system.md
- [ ] 3+ tests for quality assessment

### Part 4: Agent Tools (3-4 hours)
- [ ] `save_article()` tool with approval
- [ ] `recall_article()` tool (search)
- [ ] `list_articles()` tool (list all)
- [ ] Tool registration in `agent.py`
- [ ] 10+ tests for tools

### Part 5: Multimodal Support (2-3 hours)
- [ ] `_fetch_assets()` async function
- [ ] `_rewrite_asset_paths()` function
- [ ] `_extract_pdf_text()` function
- [ ] 5+ tests for multimodal handling

### Part 6: Integration & Commands (1-2 hours)
- [ ] `/articles` slash command
- [ ] `/article <slug>` slash command
- [ ] `/forget-article <slug>` slash command
- [ ] Status integration (article count)

### Part 7: Testing (2-3 hours)
- [ ] `test_articles_storage.py` (8 tests)
- [ ] `test_articles_loading.py` (6 tests)
- [ ] `test_articles_tools.py` (10 tests)
- [ ] `test_articles_multimodal.py` (5 tests)
- [ ] All tests pass, >85% coverage

### Part 8: Documentation (1 hour)
- [ ] Update DESIGN-14 with Article Knowledge section
- [ ] Update GLOSSARY with implementation status
- [ ] Update ROADMAP with Phase 2.6
- [ ] Update CLAUDE.md with article tools
- [ ] Add examples to tool docstrings

---

## Time Tracking

**Estimated**: 12-16 hours
**Breakdown**:
- Part 1 (Data Model): 2-3h
- Part 2 (Storage): 2-3h
- Part 3 (Web + Quality): 3-4h
- Part 4 (Tools): 3-4h
- Part 5 (Multimodal): 2-3h
- Part 6 (Integration): 1-2h
- Part 7 (Testing): 2-3h
- Part 8 (Docs): 1h

**MVP cutline**: Parts 1-2-4 only = 6-8 hours (no quality gate, no multimodal)

---

## Related Documents

- `docs/DESIGN-14-memory-lifecycle-system.md` — Memory lifecycle (Phase 1c)
- `docs/GLOSSARY-knowledge-system.md` — Knowledge system terminology
- `docs/ROADMAP-co-evolution.md` — Evolution roadmap
- `docs/DESIGN-13-tool-web-search.md` — Web intelligence tools (web_fetch)
- `docs/TODO-co-evolution-phase1c-COMPLETE.md` — Memory implementation complete

---

## Notes

### Why Not SQLite from Day 1?

**Decision**: Start with markdown files, add SQLite indices later (Phase 3+).

**Rationale**:
1. Consistency with Phase 1c memory system (lakehouse pattern)
2. Version control friendly (git diff works on markdown)
3. Human-readable and editable
4. No database migration complexity
5. Performance acceptable for <200 articles (grep is fast enough)

**When to add SQLite**: When article count >200 or search latency >500ms.

### Why Quality Gate?

**Problem**: Not all web content is worth saving — noisy, low-quality, outdated, or irrelevant content clutters knowledge base.

**Solution**: LLM-based quality assessment (0.0-1.0 score) before proposing save.

**Threshold**: 0.6+ auto-propose, <0.6 warn user (they can override).

**Cost**: ~5 seconds per article (one LLM call), acceptable for curation workflow.

### Why Multimodal?

**Reality**: Technical content is often visual — architecture diagrams, code screenshots, flowcharts, PDFs.

**Solution**: Store assets in `assets/{slug}/` directory, reference via relative paths in markdown.

**Trade-off**: Increases storage size, but enables richer knowledge representation.

**Future**: Phase 3+ can add image compression, asset deduplication, lazy loading.

---

**Status**: 📝 Ready for implementation | **Priority**: MEDIUM | **Dependency**: Phase 1c ✅
