"""Chunkability: does the content survive chunk -> embed -> rerank?

Heuristic, and labelled as such in evidence. Checks heading hierarchy,
question-shaped headings, self-contained sections, and thin content."""

from __future__ import annotations

import re

from geo.checks import Page, SiteContext, register
from geo.models import Finding, Fix, Severity

Q_WORDS = re.compile(
    r"^(how|what|why|when|where|who|which|can|do|does|is|are|should|will|much|cost)\b", re.I
)
DANGLING = re.compile(r"^(this|that|these|those|it|they|he|she|as mentioned|as noted|above|below)\b", re.I)
THIN_WORDS = 250
LONG_SECTION_WORDS = 400

FAQ_FIX = Fix(
    generator="faq",
    summary="Draft question-shaped FAQ content from facts.yaml with `geo fix <client> faq` (human review required)",
    artifact="faq-draft.md",
)


def _headings(page: Page) -> list[tuple[int, str]]:
    return [
        (int(n.tag[1]), (n.text(separator=" ") or "").strip())
        for n in page.tree.css("h1, h2, h3, h4, h5, h6")
    ]


def _sections(page: Page) -> list[tuple[str, list[str]]]:
    """(heading, paragraphs) pairs in document order."""
    body = page.tree.body
    if body is None:
        return []
    out: list[tuple[str, list[str]]] = [("", [])]
    for n in body.traverse():
        if n.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            out.append(((n.text(separator=" ") or "").strip(), []))
        elif n.tag in ("p", "li"):
            t = (n.text(separator=" ") or "").strip()
            if t:
                out[-1][1].append(t)
    return [s for s in out if s[0] or s[1]]


@register("content", version="1")
def content(ctx: SiteContext) -> list[Finding]:
    """Heading structure, question-shaped headings, self-contained sections, thin pages."""
    out: list[Finding] = []
    pages = ctx.html_pages()
    total_q = 0
    for page in pages:
        url = page.resp.final_url
        hs = _headings(page)
        h1s = [t for lvl, t in hs if lvl == 1]
        if not h1s:
            out.append(Finding(check_id="content.no_h1", severity=Severity.warning,
                               title=f"{url}: no <h1>", evidence={"url": url}, raw_ref=page.resp.raw_ref))
        elif len(h1s) > 1:
            out.append(Finding(check_id="content.multiple_h1", severity=Severity.info,
                               title=f"{url}: {len(h1s)} <h1> elements", evidence={"url": url, "h1": h1s[:10]},
                               raw_ref=page.resp.raw_ref))
        skips = []
        prev = 0
        for lvl, t in hs:
            if prev and lvl > prev + 1:
                skips.append(f"h{prev} -> h{lvl} ({t[:60]})")
            prev = lvl
        if skips:
            out.append(Finding(check_id="content.heading_skips", severity=Severity.info,
                               title=f"{url}: heading levels skip {len(skips)} time(s)",
                               evidence={"url": url, "skips": skips[:10]}, raw_ref=page.resp.raw_ref))

        qs = [t for lvl, t in hs if lvl >= 2 and (t.endswith("?") or Q_WORDS.match(t))]
        total_q += len(qs)

        words = len((page.main_text or page.visible_text).split())
        if words < THIN_WORDS:
            out.append(Finding(check_id="content.thin", severity=Severity.warning,
                               title=f"{url}: ~{words} words of main content; little for a retrieval system to quote",
                               evidence={"url": url, "main_content_words": words, "threshold": THIN_WORDS,
                                         "extractor": "trafilatura"},
                               raw_ref=page.resp.raw_ref, fix=FAQ_FIX))

        dangling, long_secs = [], []
        for heading, paras in _sections(page):
            if paras and DANGLING.match(paras[0]):
                dangling.append({"heading": heading[:80], "opens_with": paras[0][:100]})
            w = sum(len(p.split()) for p in paras)
            if w > LONG_SECTION_WORDS:
                long_secs.append({"heading": heading[:80] or "(before first heading)", "words": w})
        if dangling:
            out.append(Finding(check_id="content.dangling_references", severity=Severity.info,
                               title=f"{url}: {len(dangling)} section(s) open with a back-reference ('this', 'it'...) and won't stand alone as a chunk",
                               evidence={"url": url, "sections": dangling[:10], "heuristic": True},
                               raw_ref=page.resp.raw_ref))
        if long_secs:
            out.append(Finding(check_id="content.long_sections", severity=Severity.info,
                               title=f"{url}: {len(long_secs)} section(s) over {LONG_SECTION_WORDS} words with no subheading",
                               evidence={"url": url, "sections": long_secs[:10], "heuristic": True},
                               raw_ref=page.resp.raw_ref))

    if pages and total_q == 0:
        out.append(Finding(check_id="content.no_question_headings", severity=Severity.warning,
                           title="No question-shaped headings on key pages; content is not phrased the way customers ask",
                           evidence={"pages": [p.resp.final_url for p in pages]}, fix=FAQ_FIX))
    return out
