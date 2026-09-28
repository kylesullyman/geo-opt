"""Check registry and the SiteContext every check receives.

Adding a check = one new module in geo/checks/ with:

    @register("robots")
    def check(ctx: SiteContext) -> list[Finding]: ...

Modules in this package are auto-imported; no wiring.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from geo.config import Client
from geo.fetch import Fetcher, FetchError, Response
from geo.models import Finding
from geo.robots import Robots

CheckFn = Callable[["SiteContext"], list[Finding]]


@dataclass(frozen=True)
class CheckSpec:
    name: str
    fn: CheckFn
    version: str
    requires: tuple[str, ...]  # flags that must be on, e.g. ("probe_bots",)
    description: str


_REGISTRY: dict[str, CheckSpec] = {}


def register(name: str, *, version: str = "1", requires: tuple[str, ...] = ()) -> Callable[[CheckFn], CheckFn]:
    def deco(fn: CheckFn) -> CheckFn:
        if name in _REGISTRY and _REGISTRY[name].fn is not fn:
            raise ValueError(f"check {name!r} registered twice")
        _REGISTRY[name] = CheckSpec(
            name=name,
            fn=fn,
            version=f"{name}-{version}",
            requires=requires,
            description=(fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "",
        )
        return fn

    return deco


def _discover() -> None:
    for mod in pkgutil.iter_modules(__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"{__name__}.{mod.name}")


def registry() -> dict[str, CheckSpec]:
    _discover()
    return dict(sorted(_REGISTRY.items()))


class Page:
    def __init__(self, url: str, resp: Response):
        self.url = url
        self.resp = resp  # failed fetches carry status=None and .error

    @property
    def error(self) -> str | None:
        return self.resp.error

    @property
    def ok(self) -> bool:
        return self.resp.ok

    @property
    def is_html(self) -> bool:
        ctype = self.resp.headers.get("content-type", "")
        return "html" in ctype or (not ctype and self.resp.body.lstrip()[:1] == b"<")

    @cached_property
    def html(self) -> str:
        return self.resp.text

    @cached_property
    def tree(self) -> HTMLParser:
        return HTMLParser(self.html)

    @cached_property
    def structured(self) -> dict[str, Any]:
        """extruct output (json-ld, microdata, rdfa) plus JSON-LD parse errors."""
        import extruct

        errors: list[dict[str, Any]] = []
        for i, node in enumerate(self.tree.css('script[type="application/ld+json"]')):
            raw = node.text(deep=True, separator="", strip=False)
            try:
                json.loads(raw)
            except json.JSONDecodeError as e:
                errors.append({"index": i, "error": str(e), "snippet": raw.strip()[:200]})
        try:
            data = extruct.extract(
                self.html,
                base_url=self.resp.final_url or self.url,
                syntaxes=["json-ld", "microdata", "rdfa"],
                uniform=True,
                errors="ignore",
            )
        except Exception as e:  # extruct can raise on pathological markup
            data = {"json-ld": [], "microdata": [], "rdfa": [], "error": str(e)}
        data["jsonld_errors"] = errors
        return data

    @cached_property
    def main_text(self) -> str:
        import trafilatura

        return trafilatura.extract(self.html, include_comments=False, include_tables=True) or ""

    @cached_property
    def visible_text(self) -> str:
        t = HTMLParser(self.html)
        for sel in ("script", "style", "noscript", "template"):
            for n in t.css(sel):
                n.decompose()
        body = t.body
        return (body.text(separator=" ") if body else t.text(separator=" ")) or ""


@dataclass
class SiteContext:
    client: Client
    fetcher: Fetcher
    flags: dict[str, bool] = field(default_factory=dict)
    _pages: dict[str, Page] = field(default_factory=dict)
    _robots: tuple[Response, Robots | None] | None = None

    @property
    def base_url(self) -> str:
        return self.client.config.base_url

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).netloc

    def url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/")) if path != "/" else self.base_url + "/"

    def key_urls(self) -> list[str]:
        return [self.url(p) for p in self.client.config.pages]

    def page(self, url_or_path: str) -> Page:
        url = url_or_path if "://" in url_or_path else self.url(url_or_path)
        if url not in self._pages:
            try:
                self._pages[url] = Page(url, self.fetcher.get(url))
            except FetchError as e:
                self._pages[url] = Page(url, Response.failed(url, str(e)))
        return self._pages[url]

    @property
    def home(self) -> Page:
        return self.page("/")

    def html_pages(self) -> list[Page]:
        return [p for p in (self.page(u) for u in self.key_urls()) if p.ok and p.is_html]

    def robots(self) -> tuple[Response, Robots | None]:
        if self._robots is None:
            resp = self.fetcher.get(self.url("/robots.txt"), _skip_robots=True)
            parsed = Robots.parse(resp.text) if resp.ok else None
            self._robots = (resp, parsed)
        return self._robots


@dataclass
class CheckResult:
    findings: list[Finding]
    errors: list[dict[str, str]]
    ran: list[str]
    skipped: list[str]


def run_checks(
    ctx: SiteContext, only: list[str] | None = None, skip: list[str] | None = None
) -> CheckResult:
    findings: list[Finding] = []
    errors: list[dict[str, str]] = []
    ran: list[str] = []
    skipped: list[str] = []
    for name, spec in registry().items():
        if (only and name not in only) or (skip and name in skip):
            continue
        if any(not ctx.flags.get(req) for req in spec.requires):
            skipped.append(name)
            continue
        try:
            out = spec.fn(ctx)
        except Exception as e:
            # A crashing check must never masquerade as a finding about the site.
            errors.append(
                {"check": name, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()}
            )
            continue
        ran.append(name)
        for f in out:
            findings.append(f if f.parser_version != "0" else f.model_copy(update={"parser_version": spec.version}))
    return CheckResult(findings=findings, errors=errors, ran=ran, skipped=skipped)
