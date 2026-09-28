"""Crawl access: robots.txt per bot group, sitemaps, status codes, indexing
directives, canonicals, host consolidation, JS-dependence, WAF/UA blocking."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from geo.bots import RETRIEVAL, TRAINING, Bot, slug
from geo.checks import SiteContext, register
from geo.models import Finding, Fix, Severity

ROBOTS_FIX = Fix(generator="robots", summary="Stage a corrected robots.txt with `geo fix <client> robots`", artifact="robots.txt")


def _host_match(a: str, b: str) -> bool:
    strip = lambda h: h.lower().removeprefix("www.")  # noqa: E731
    return strip(a) == strip(b)


# ---------------------------------------------------------------------------- robots


@register("robots", version="2")
def robots(ctx: SiteContext) -> list[Finding]:
    """robots.txt verdicts for AI retrieval bots (blockers) and training bots (policy)."""
    resp, parsed = ctx.robots()
    out: list[Finding] = []
    urls = ctx.key_urls()

    if resp.error or resp.status is None:
        return [
            Finding(
                check_id="robots.unreachable",
                severity=Severity.warning,
                title="robots.txt could not be fetched",
                evidence={"url": resp.url, "error": resp.error},
            )
        ]
    if resp.status >= 500:
        return [
            Finding(
                check_id="robots.server_error",
                severity=Severity.blocker,
                title=f"robots.txt returns HTTP {resp.status}; major crawlers treat this as 'disallow everything'",
                evidence={"url": resp.url, "status": resp.status},
                raw_ref=resp.raw_ref,
                fix=Fix(generator="robots", summary="Serve robots.txt with 200 (or 404 if none is wanted)", artifact="robots.txt"),
            )
        ]
    if resp.status in (401, 403):
        out.append(
            Finding(
                check_id="robots.forbidden",
                severity=Severity.warning,
                title=f"robots.txt returns HTTP {resp.status} to our crawler; a WAF may be filtering bots",
                evidence={"url": resp.url, "status": resp.status},
                raw_ref=resp.raw_ref,
            )
        )
        return out
    if parsed is None:
        return [
            Finding(
                check_id="robots.absent",
                severity=Severity.info,
                title=f"No robots.txt (HTTP {resp.status}); all crawlers are allowed by default",
                evidence={"url": resp.url, "status": resp.status},
                raw_ref=resp.raw_ref,
            )
        ]

    if "html" in resp.headers.get("content-type", "") or re.search(r"(?i)<html|<!doctype", resp.text[:500]):
        out.append(
            Finding(
                check_id="robots.served_html",
                severity=Severity.warning,
                title="robots.txt returns an HTML page (soft 404); crawlers will ignore it or misparse it",
                evidence={"content_type": resp.headers.get("content-type"), "head": resp.text[:200]},
                raw_ref=resp.raw_ref,
            )
        )
    if parsed.errors:
        out.append(
            Finding(
                check_id="robots.parse_errors",
                severity=Severity.info,
                title=f"robots.txt has {len(parsed.errors)} unparseable line(s)",
                evidence={"errors": parsed.errors[:20]},
                raw_ref=resp.raw_ref,
            )
        )

    def verdicts(bot: Bot) -> dict[str, dict]:
        return {u: parsed.verdict(bot.token, u).as_evidence() for u in urls}

    # Retrieval bots: each block is its own finding. This is the demo finding.
    for bot in RETRIEVAL:
        v = verdicts(bot)
        blocked = [u for u, e in v.items() if not e["allowed"]]
        if not blocked:
            continue
        home_blocked = urls[0] in blocked
        sev = Severity.blocker if (bot.honors_robots and home_blocked) else Severity.warning
        scope = "the whole site" if len(blocked) == len(urls) else f"{len(blocked)}/{len(urls)} key pages"
        out.append(
            Finding(
                check_id=f"robots.blocks_{slug(bot.token)}",
                severity=sev,
                title=f"robots.txt blocks {bot.token} ({bot.operator} {bot.purpose}) from {scope}",
                evidence={
                    "bot": bot.token,
                    "group": "retrieval",
                    "operator": bot.operator,
                    "honors_robots": bot.honors_robots,
                    "note": bot.note or None,
                    "blocked_urls": blocked,
                    "verdicts": v,
                },
                fix=ROBOTS_FIX,
                raw_ref=resp.raw_ref,
            )
        )

    # Training bots: one policy summary. Blocking them is legitimate; report, don't alarm.
    training = {}
    for bot in TRAINING:
        v = verdicts(bot)
        training[bot.token] = "blocked" if not v[urls[0]]["allowed"] else "allowed"
    blocked_t = sorted(k for k, s in training.items() if s == "blocked")
    out.append(
        Finding(
            check_id="robots.training_policy",
            severity=Severity.info,
            title=(
                f"Training crawlers: {len(blocked_t)}/{len(training)} blocked"
                + (f" ({', '.join(blocked_t)})" if blocked_t else "")
                + ". Policy choice; does not affect current AI answers."
            ),
            evidence={"group": "training", "bots": training},
            raw_ref=resp.raw_ref,
        )
    )
    return out


# ---------------------------------------------------------------------------- status/indexing


_NOINDEX = re.compile(r"\b(noindex|none)\b", re.I)
_NOSNIPPET = re.compile(r"\b(nosnippet|max-snippet\s*:\s*0)\b", re.I)


@register("status")
def status(ctx: SiteContext) -> list[Finding]:
    """Key pages: HTTP status, redirect chains, off-domain redirects, noindex/nosnippet."""
    out: list[Finding] = []
    for url in ctx.key_urls():
        page = ctx.page(url)
        r = page.resp
        if r.status is None:
            out.append(
                Finding(
                    check_id="status.unreachable",
                    severity=Severity.blocker,
                    title=f"{url} is unreachable",
                    evidence={"url": url, "error": page.error},
                )
            )
            continue
        if r.status >= 400:
            out.append(
                Finding(
                    check_id="status.http_error",
                    severity=Severity.blocker,
                    title=f"{url} returns HTTP {r.status}",
                    evidence={"url": url, "status": r.status, "final_url": r.final_url},
                    raw_ref=r.raw_ref,
                )
            )
            continue
        if len(r.redirects) > 1:
            out.append(
                Finding(
                    check_id="status.redirect_chain",
                    severity=Severity.warning,
                    title=f"{url} goes through {len(r.redirects)} redirects",
                    evidence={"url": url, "chain": r.redirects + [r.final_url]},
                )
            )
        final_host = urlsplit(r.final_url).netloc
        if not _host_match(final_host, ctx.host):
            out.append(
                Finding(
                    check_id="status.offsite_redirect",
                    severity=Severity.warning,
                    title=f"{url} redirects off-domain to {final_host}",
                    evidence={"url": url, "final_url": r.final_url},
                )
            )

        directives = []
        xrt = r.headers.get("x-robots-tag")
        if xrt:
            directives.append(("X-Robots-Tag header", xrt))
        if page.is_html:
            for m in page.tree.css("meta[name]"):
                name = (m.attributes.get("name") or "").lower()
                if name in ("robots", "googlebot"):
                    directives.append((f'<meta name="{name}">', m.attributes.get("content") or ""))
        for source, value in directives:
            if _NOINDEX.search(value):
                out.append(
                    Finding(
                        check_id="status.noindex",
                        severity=Severity.blocker,
                        title=f"{url} is marked noindex ({source}); it cannot appear in search or AI Overviews",
                        evidence={"url": url, "source": source, "value": value},
                        raw_ref=r.raw_ref,
                    )
                )
            elif _NOSNIPPET.search(value):
                out.append(
                    Finding(
                        check_id="status.nosnippet",
                        severity=Severity.warning,
                        title=f"{url} forbids snippets ({source}); Google will not quote it in AI Overviews",
                        evidence={"url": url, "source": source, "value": value},
                        raw_ref=r.raw_ref,
                    )
                )
    return out


# ---------------------------------------------------------------------------- canonical


@register("canonical")
def canonical(ctx: SiteContext) -> list[Finding]:
    """rel=canonical present, single, and pointing at this page on this host."""
    out: list[Finding] = []
    for page in ctx.html_pages():
        links = [
            n.attributes.get("href") or ""
            for n in page.tree.css("link[rel]")
            if "canonical" in (n.attributes.get("rel") or "").lower().split()
        ]
        final = page.resp.final_url
        if not links:
            out.append(
                Finding(
                    check_id="canonical.missing",
                    severity=Severity.info,
                    title=f"{final} has no rel=canonical",
                    evidence={"url": final},
                    raw_ref=page.resp.raw_ref,
                )
            )
            continue
        uniq = sorted(set(links))
        if len(uniq) > 1:
            out.append(
                Finding(
                    check_id="canonical.multiple",
                    severity=Severity.warning,
                    title=f"{final} declares {len(uniq)} different canonicals; engines may ignore all of them",
                    evidence={"url": final, "canonicals": uniq},
                    raw_ref=page.resp.raw_ref,
                )
            )
            continue
        from urllib.parse import urljoin

        target = urljoin(final, uniq[0])
        th = urlsplit(target).netloc
        if not _host_match(th, ctx.host):
            out.append(
                Finding(
                    check_id="canonical.offsite",
                    severity=Severity.warning,
                    title=f"{final} canonicalizes to another host ({th}); citations will credit that URL",
                    evidence={"url": final, "canonical": target},
                    raw_ref=page.resp.raw_ref,
                )
            )
        elif urlsplit(target).path.rstrip("/") != urlsplit(final).path.rstrip("/"):
            out.append(
                Finding(
                    check_id="canonical.other_page",
                    severity=Severity.info,
                    title=f"{final} canonicalizes to a different page ({target})",
                    evidence={"url": final, "canonical": target},
                    raw_ref=page.resp.raw_ref,
                )
            )
    return out


# ---------------------------------------------------------------------------- host consolidation


@register("hosts")
def hosts(ctx: SiteContext) -> list[Finding]:
    """http->https and www/apex variants consolidate on one origin."""
    out: list[Finding] = []
    bare = ctx.host.removeprefix("www.")
    variants = [f"http://{bare}/", f"http://www.{bare}/", f"https://{bare}/", f"https://www.{bare}/"]
    finals: dict[str, str | None] = {}
    for v in variants:
        r = ctx.fetcher.get(v, _skip_robots=True)
        finals[v] = (r.final_url if r.status and r.status < 400 else None)
    live = {v: f for v, f in finals.items() if f}
    origins = {urlsplit(f).scheme + "://" + urlsplit(f).netloc for f in live.values()}
    if any(urlsplit(f).scheme == "http" for f in live.values()):
        out.append(
            Finding(
                check_id="hosts.no_https_redirect",
                severity=Severity.warning,
                title="Site is served over plain http without redirecting to https",
                evidence={"finals": finals},
            )
        )
    if len(origins) > 1:
        out.append(
            Finding(
                check_id="hosts.split_origin",
                severity=Severity.warning,
                title=f"Site answers on {len(origins)} origins without consolidating; signals are split",
                evidence={"origins": sorted(origins), "finals": finals},
            )
        )
    return out


# ---------------------------------------------------------------------------- sitemap


@register("sitemap", version="2")
def sitemap(ctx: SiteContext) -> list[Finding]:
    """A reachable XML sitemap exists (declared in robots.txt or at /sitemap.xml)."""
    resp, parsed = ctx.robots()
    declared = parsed.sitemaps if parsed else []
    candidates = declared or [ctx.url("/sitemap.xml"), ctx.url("/sitemap_index.xml")]
    out: list[Finding] = []
    found = None
    tried = {}
    for sm in candidates:
        original = sm
        try:
            parts = urlsplit(sm)
            absolute = parts.scheme in ("http", "https") and bool(parts.netloc)
            sm = urljoin(resp.final_url, sm)
            resolved = urlsplit(sm)
            usable = resolved.scheme in ("http", "https") and bool(resolved.netloc)
        except ValueError:
            absolute = usable = False
        if not absolute:
            out.append(Finding(
                check_id="sitemap.invalid_url", severity=Severity.warning,
                title=f"robots.txt Sitemap directive must use an absolute HTTP(S) URL: {original}",
                evidence={"declared": original, "resolved": sm if usable else None},
                raw_ref=resp.raw_ref,
                fix=Fix(generator="manual", summary="Use the full https:// URL in the Sitemap directive"),
            ))
        if not usable:
            tried[original] = None
            continue
        r = ctx.fetcher.get(sm, _skip_robots=True)
        tried[sm] = r.status
        head = r.body[:2000].decode("utf-8", "replace") if r.ok else ""
        if r.ok and re.search(r"<(urlset|sitemapindex)\b", head):
            found = (sm, r)
            break
    if not found:
        return out + [
            Finding(
                check_id="sitemap.missing",
                severity=Severity.warning,
                title="No reachable XML sitemap" + (" (robots.txt declares one that fails)" if declared else ""),
                evidence={"tried": tried, "declared_in_robots": declared},
                fix=Fix(generator="manual", summary="Publish sitemap.xml and add a Sitemap: line to robots.txt"),
            )
        ]
    if not declared:
        out.append(
            Finding(
                check_id="sitemap.not_in_robots",
                severity=Severity.info,
                title=f"Sitemap exists at {found[0]} but robots.txt does not declare it",
                evidence={"sitemap": found[0]},
                fix=ROBOTS_FIX,
            )
        )
    return out


# ---------------------------------------------------------------------------- JS dependence


_SPA_ROOT = re.compile(r'<div[^>]+id=["\'](root|app|__next|__nuxt|svelte)["\'][^>]*>\s*</div>', re.I)
MIN_WORDS = 80


@register("js_dependence")
def js_dependence(ctx: SiteContext) -> list[Finding]:
    """Content present in raw HTML (most AI fetchers do not execute JavaScript)."""
    out: list[Finding] = []
    for page in ctx.html_pages():
        words = len(page.visible_text.split())
        scripts = len(page.tree.css("script[src]"))
        shell = bool(_SPA_ROOT.search(page.html))
        if words < MIN_WORDS and (shell or scripts >= 3):
            ev = {"url": page.resp.final_url, "raw_html_words": words, "external_scripts": scripts, "spa_root": shell}
            rendered = None
            if ctx.flags.get("js"):
                from geo.fetch.render import rendered_word_count

                rendered = rendered_word_count(page.resp.final_url)
                ev["rendered_words"] = rendered
            if rendered is not None and rendered < MIN_WORDS:
                continue  # genuinely thin page, not a JS problem; content check reports thinness
            out.append(
                Finding(
                    check_id="crawl.js_required",
                    severity=Severity.blocker if rendered else Severity.warning,
                    title=(
                        f"{page.resp.final_url}: only {words} words in raw HTML"
                        + (f" vs {rendered} after JavaScript" if rendered else "; content likely needs JavaScript")
                        + ". AI fetchers generally do not run JS."
                    ),
                    evidence=ev,
                    raw_ref=page.resp.raw_ref,
                    fix=Fix(generator="manual", summary="Enable server-side rendering / prerendering for key pages"),
                )
            )
    return out


# ---------------------------------------------------------------------------- UA probe (opt-in)

_CHALLENGE = re.compile(r"(?i)just a moment|cf-chl|attention required|access denied|captcha")


@register("waf_probe", requires=("probe_bots",))
def waf_probe(ctx: SiteContext) -> list[Finding]:
    """Homepage served to AI retrieval-bot user-agents (opt-in: --probe-bots)."""
    base = ctx.home
    if not base.ok:
        return []
    out: list[Finding] = []
    results = {}
    for bot in RETRIEVAL:
        if not bot.user_agent or bot.token == "Googlebot":
            continue
        r = ctx.fetcher.get(ctx.url("/"), _skip_robots=True, user_agent=bot.user_agent)
        challenged = bool(r.body and _CHALLENGE.search(r.body[:5000].decode("utf-8", "replace")))
        cf = r.headers.get("cf-mitigated")
        results[bot.token] = {"status": r.status, "challenge": challenged, "cf_mitigated": cf}
        if (r.status and r.status in (401, 403, 406, 429, 503)) or cf or challenged:
            out.append(
                Finding(
                    check_id=f"waf.blocks_{slug(bot.token)}",
                    severity=Severity.warning,
                    title=f"Homepage refuses the {bot.token} user-agent (HTTP {r.status}); likely a CDN/WAF bot rule",
                    evidence={
                        "bot": bot.token,
                        "status": r.status,
                        "cf_mitigated": cf,
                        "challenge_page": challenged,
                        "baseline_status": base.resp.status,
                        "caveat": "UA-only probe from our IP. Real crawlers are verified by IP, so confirm in the CDN's bot settings before reporting as fact.",
                    },
                    raw_ref=r.raw_ref,
                    fix=Fix(generator="manual", summary="Allow verified AI search crawlers in CDN/WAF bot settings"),
                )
            )
    return out
