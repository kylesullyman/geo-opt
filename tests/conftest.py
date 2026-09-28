"""Shared fixtures. Zero network: every test routes httpx through FakeWeb."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest

import geo.fetch

FIX = Path(__file__).parent / "fixtures"


class FakeWeb:
    """A tiny in-memory internet. Routes are (host, path) -> response spec."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], dict] = {}
        self.api: dict[str, list] = {}  # host -> queue of bodies (cycled)
        self.calls: list[httpx.Request] = []

    def page(self, host: str, path: str, body: str | bytes = "", status: int = 200,
             ctype: str = "text/html; charset=utf-8", headers: dict | None = None, ua_status: dict | None = None):
        self.routes[(host, path)] = {"body": body, "status": status, "ctype": ctype, "headers": headers or {}, "ua_status": ua_status or {}}

    def redirect(self, host: str, path: str, to: str, status: int = 301):
        self.routes[(host, path)] = {"redirect": to, "status": status}

    def api_responses(self, host: str, bodies: list):
        self.api[host] = list(bodies)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        host, path = request.url.host, request.url.path
        if request.url.scheme == "http" and ("http:" + host, path) not in self.routes:
            return httpx.Response(301, headers={"location": str(request.url.copy_with(scheme="https"))})
        if ("http:" + host, path) in self.routes and request.url.scheme == "http":
            host = "http:" + host
        if host in self.api:
            q = self.api[host]
            body = q[(sum(1 for c in self.calls if c.url.host == host) - 1) % len(q)]
            if isinstance(body, tuple):
                status, body = body
            else:
                status = 200
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            return httpx.Response(status, content=data, headers={"content-type": "application/json"})
        spec = self.routes.get((host, path))
        if spec is None:
            return httpx.Response(404, text="not found", headers={"content-type": "text/html"})
        if "redirect" in spec:
            return httpx.Response(spec["status"], headers={"location": spec["redirect"]})
        ua = request.headers.get("user-agent", "")
        for token, st in spec["ua_status"].items():
            if token in ua:
                return httpx.Response(st, text="Access denied", headers={"content-type": "text/html"})
        body = spec["body"]
        return httpx.Response(
            spec["status"],
            content=body if isinstance(body, bytes) else body.encode(),
            headers={"content-type": spec["ctype"], **spec["headers"]},
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def web(monkeypatch) -> FakeWeb:
    w = FakeWeb()
    monkeypatch.setattr(geo.fetch, "DEFAULT_TRANSPORT", w.transport())
    # Tests must never sleep on rate limits.
    monkeypatch.setattr(geo.fetch._RateLimiter, "wait", lambda self, host: None)
    monkeypatch.setattr(geo.fetch.time, "sleep", lambda s: None)
    return w


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """An agency dir with the smiles-dental client copied from fixtures."""
    shutil.copytree(FIX / "clients", tmp_path / "clients")
    monkeypatch.setenv("GEO_HOME", str(tmp_path))
    for k in ("PERPLEXITY_API_KEY", "OPENAI_API_KEY", "SERPAPI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def fixture_text(name: str) -> str:
    return (FIX / name).read_text()


def fixture_json(name: str):
    return json.loads((FIX / name).read_text())


HOST = "smilesdental.example"


def serve_site(web: FakeWeb, *, robots: str | None = None, home_html: str | None = None,
               services_html: str | None = None, sitemap: bool = True) -> None:
    web.page(HOST, "/", home_html if home_html is not None else fixture_text("sites/good_home.html"))
    web.page(HOST, "/services", services_html if services_html is not None else fixture_text("sites/good_services.html"))
    if robots is not None:
        web.page(HOST, "/robots.txt", robots, ctype="text/plain")
    if sitemap:
        web.page(HOST, "/sitemap.xml", '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://smilesdental.example/</loc></url></urlset>', ctype="application/xml")
    # www consolidates onto the apex; plain http upgrades to https (see handler).
    web.redirect("www." + HOST, "/", f"https://{HOST}/")
