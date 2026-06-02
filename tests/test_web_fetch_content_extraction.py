"""Tests for web_fetch main-content extraction (trafilatura) and its empty-result fallback.

Guards the defect web_fetch's extraction fixes: a raw HTML page run through the
markdown path must return the article body, not nav/header/footer chrome, so the
model receives content rather than boilerplate. Both tests drive the real
pure-function extraction path with real HTML — no network, no LLM, no fakes. The
exception fail-open branch (try/except → None) is unreachable with real input and
is left to code review, since forcing it would require a fake (testing.md bans mocks).
"""

from co_cli.tools.web.fetch import _extract_main_content, _html_to_markdown

_ARTICLE_HTML = """<!doctype html>
<html>
  <head><title>Resolution Concepts</title></head>
  <body>
    <header><nav>
      <a href="/">Home</a> <a href="/docs">Docs</a> <a href="/pricing">Pricing</a>
      <a href="/login">Sign in to your account</a>
    </nav></header>
    <aside class="sidebar">
      <ul>
        <li><a href="/a">Installation</a></li>
        <li><a href="/b">Configuration</a></li>
        <li><a href="/c">Troubleshooting the sidebar widget</a></li>
      </ul>
    </aside>
    <main>
      <article>
        <h1>Dependency Resolution</h1>
        <p>Resolution is the process of taking a list of requirements and
        converting them into a concrete list of package versions that satisfy
        every constraint simultaneously. The resolver walks the dependency
        graph and backtracks whenever it encounters a conflict between two
        transitive requirements.</p>
        <p>A universal resolution produces a single lockfile that is valid
        across every supported platform, which is what makes reproducible
        installs possible across machines and operating systems.</p>
      </article>
    </main>
    <footer>Copyright 2026 Example Corp. All rights reserved. Terms of service.</footer>
  </body>
</html>"""

_CONTENTLESS_HTML = (
    "<!doctype html><html><head><title>x</title></head><body><div></div></body></html>"
)


def test_extraction_returns_article_body_and_drops_chrome() -> None:
    """A chrome-heavy page yields the article prose, not the nav/footer boilerplate."""
    extracted = _extract_main_content(_ARTICLE_HTML, "https://example.com/concepts/resolution/")
    assert extracted is not None
    assert "process of taking a list of requirements" in extracted
    assert "universal resolution produces a single lockfile" in extracted
    assert "Sign in to your account" not in extracted
    assert "Troubleshooting the sidebar widget" not in extracted
    assert "All rights reserved" not in extracted


def test_contentless_html_returns_none_and_full_converter_is_the_fallback() -> None:
    """Contentless HTML → None (the empty-result fail-open branch), and the fallback still works.

    Mirrors the web_fetch markdown branch: when extraction returns None the caller
    falls back to _html_to_markdown, which must convert a real page to non-empty markdown.
    """
    assert _extract_main_content(_CONTENTLESS_HTML, "https://example.com/empty") is None
    fallback = _html_to_markdown(_ARTICLE_HTML)
    assert "Dependency Resolution" in fallback
