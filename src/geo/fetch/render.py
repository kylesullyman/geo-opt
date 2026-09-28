"""Optional JS rendering (feature flag --js). Requires the `js` extra:

    uv sync --extra js && uv run playwright install chromium
"""

from __future__ import annotations

from selectolax.parser import HTMLParser


class RenderUnavailable(RuntimeError):
    pass


def rendered_html(url: str, timeout_ms: int = 20000) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RenderUnavailable("playwright not installed: uv sync --extra js && uv run playwright install chromium") from e
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            return page.content()
        finally:
            browser.close()


def rendered_word_count(url: str) -> int | None:
    try:
        html = rendered_html(url)
    except RenderUnavailable:
        raise
    except Exception:
        return None
    t = HTMLParser(html)
    for sel in ("script", "style", "noscript", "template"):
        for n in t.css(sel):
            n.decompose()
    body = t.body
    return len(((body.text(separator=" ") if body else "") or "").split())
