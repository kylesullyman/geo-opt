"""Engine adapters. Each turns (query, locality) into one raw API call, and
separately parses a stored raw body into (answer_text, cited_urls).

Parsing is a pure function of the raw bytes so history can be reparsed.

Surfaces, stated honestly (these labels go into every report):
- perplexity: Sonar API. Returns search_results/citations. Close to the product.
- openai:     Responses API + web_search tool. A different distribution than
              a person using the ChatGPT app.
- google_aio: SerpApi scrape of Google's AI Overview. Present only when Google
              shows one for that query/location. There is no official API.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from geo.fetch import Fetcher, Response
from geo.models import Locality

ANSWER_PARSER_VERSION = "answer-2"

SYSTEM_PROMPT = None  # never steer the engine; ask exactly what a customer would


class EngineError(Exception):
    pass


@dataclass
class Parsed:
    answer_text: str
    cited_urls: list[str]
    model: str | None = None
    empty_reason: str | None = None  # e.g. "no_ai_overview"


class Engine(Protocol):
    name: str
    surface: str
    env_key: str

    def available(self) -> bool: ...
    def call(self, fetcher: Fetcher, query: str, loc: Locality) -> Response: ...
    def parse(self, body: bytes) -> Parsed: ...


def _dedupe(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(u for u in urls if u))


def _key(env: str) -> str:
    v = os.environ.get(env)
    if not v:
        raise EngineError(f"{env} is not set")
    return v


# ----------------------------------------------------------------------------- Perplexity


class Perplexity:
    name = "perplexity"
    surface = "Perplexity Sonar API (web search on). Close to perplexity.ai answers, not identical."
    env_key = "PERPLEXITY_API_KEY"
    url = "https://api.perplexity.ai/chat/completions"

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("GEO_PERPLEXITY_MODEL", "sonar")

    def available(self) -> bool:
        return bool(os.environ.get(self.env_key))

    def payload(self, query: str, loc: Locality) -> dict[str, Any]:
        ul: dict[str, Any] = {"country": loc.country}
        if loc.city:
            ul["city"] = loc.city
        if loc.region:
            ul["region"] = loc.region
        if loc.latitude is not None and loc.longitude is not None:
            ul["latitude"], ul["longitude"] = loc.latitude, loc.longitude
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": query}],
            "web_search_options": {"user_location": ul},
        }

    def call(self, fetcher: Fetcher, query: str, loc: Locality) -> Response:
        return fetcher.api(
            "POST",
            self.url,
            headers={"Authorization": f"Bearer {_key(self.env_key)}", "Content-Type": "application/json"},
            json_body=self.payload(query, loc),
        )

    def parse(self, body: bytes) -> Parsed:
        d = json.loads(body)
        text = ""
        for ch in d.get("choices") or []:
            text += (ch.get("message") or {}).get("content") or ""
        # Retrieved results are not necessarily cited by the answer.
        urls = [u for u in d.get("citations") or [] if isinstance(u, str)]
        return Parsed(answer_text=text, cited_urls=_dedupe(urls), model=d.get("model"))


# ----------------------------------------------------------------------------- OpenAI


class OpenAI:
    name = "openai"
    surface = "OpenAI Responses API with web_search. NOT the ChatGPT app: different model/prompting/distribution."
    env_key = "OPENAI_API_KEY"
    url = "https://api.openai.com/v1/responses"

    def __init__(self, model: str | None = None):
        self.model = model or os.environ.get("GEO_OPENAI_MODEL", "gpt-5-mini")

    def available(self) -> bool:
        return bool(os.environ.get(self.env_key))

    def payload(self, query: str, loc: Locality) -> dict[str, Any]:
        ul: dict[str, Any] = {"type": "approximate", "country": loc.country}
        if loc.city:
            ul["city"] = loc.city
        if loc.region:
            ul["region"] = loc.region
        if loc.timezone:
            ul["timezone"] = loc.timezone
        return {
            "model": self.model,
            "input": query,
            "tools": [{"type": "web_search", "user_location": ul}],
            "tool_choice": "auto",
            "include": ["web_search_call.action.sources"],
        }

    def call(self, fetcher: Fetcher, query: str, loc: Locality) -> Response:
        return fetcher.api(
            "POST",
            self.url,
            headers={"Authorization": f"Bearer {_key(self.env_key)}", "Content-Type": "application/json"},
            json_body=self.payload(query, loc),
        )

    def parse(self, body: bytes) -> Parsed:
        d = json.loads(body)
        text_parts: list[str] = []
        urls: list[str] = []
        for item in d.get("output") or []:
            if item.get("type") == "message":
                for c in item.get("content") or []:
                    if c.get("type") == "output_text":
                        text_parts.append(c.get("text") or "")
                        for a in c.get("annotations") or []:
                            if a.get("type") == "url_citation":
                                urls.append(a.get("url", ""))
            # Search-call sources remain in raw evidence; only answer annotations
            # count as citations, not everything the model retrieved.
        return Parsed(answer_text="\n".join(text_parts), cited_urls=_dedupe(urls), model=d.get("model"))


# ----------------------------------------------------------------------------- Google AIO via SerpApi


def _aio_text(blocks: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for b in blocks or []:
        for k in ("title", "snippet"):
            if b.get(k):
                out.append(str(b[k]))
        for li in b.get("list") or []:
            out.extend(_aio_text([li]))
        if b.get("table"):
            out.extend(" | ".join(map(str, row)) for row in b["table"])
        if b.get("text_blocks"):
            out.extend(_aio_text(b["text_blocks"]))
    return out


class GoogleAIO:
    name = "google_aio"
    surface = "Google AI Overview, scraped via SerpApi at a pinned location. No official API exists; absent when Google shows no overview."
    env_key = "SERPAPI_API_KEY"
    url = "https://serpapi.com/search"

    def available(self) -> bool:
        return bool(os.environ.get(self.env_key))

    def params(self, query: str, loc: Locality) -> dict[str, Any]:
        p = {"engine": "google", "q": query, "gl": loc.country.lower(), "hl": "en", "no_cache": "true"}
        if loc.serp_location:
            p["location"] = loc.serp_location
        return p

    def call(self, fetcher: Fetcher, query: str, loc: Locality) -> Response:
        key = _key(self.env_key)
        r = fetcher.api("GET", self.url, params={**self.params(query, loc), "api_key": key})
        # SerpApi sometimes defers the overview behind a short-lived page_token.
        try:
            d = json.loads(r.body) if r.ok else {}
        except json.JSONDecodeError:
            return r
        aio = d.get("ai_overview") or {}
        if aio.get("page_token") and not aio.get("text_blocks"):
            r2 = fetcher.api(
                "GET",
                self.url,
                params={"engine": "google_ai_overview", "page_token": aio["page_token"], "api_key": key},
            )
            if r2.ok:
                # Store a single combined body so parse() stays a pure function of one blob.
                merged = {"search": d, "ai_overview_followup": json.loads(r2.body)}
                body = json.dumps(merged).encode()
                return Response(
                    url=r.url, final_url=r2.final_url, status=200, headers=r2.headers, body=body,
                    raw_ref=fetcher.store.put_blob(body), elapsed_s=r.elapsed_s + r2.elapsed_s,
                )
        return r

    def parse(self, body: bytes) -> Parsed:
        d = json.loads(body)
        if "search" in d:
            aio = (d.get("ai_overview_followup") or {}).get("ai_overview") or {}
        else:
            aio = d.get("ai_overview") or {}
        if not aio or not (aio.get("text_blocks") or aio.get("references")):
            return Parsed(answer_text="", cited_urls=[], model="google-aio", empty_reason="no_ai_overview")
        text = "\n".join(_aio_text(aio.get("text_blocks") or []))
        urls = [r.get("link", "") for r in aio.get("references") or []]
        return Parsed(answer_text=text, cited_urls=_dedupe(urls), model="google-aio")


class ChatGPTBrowser:
    """Import-only surface. Never substitutes an API or Codex response."""
    from geo.citations.browser import SURFACE as surface
    name = "chatgpt_browser"
    env_key = ""

    def available(self) -> bool:
        return False

    def call(self, fetcher, query, loc):
        raise EngineError("use geo browser prepare/import for ChatGPT web observations")

    def parse(self, body: bytes) -> Parsed:
        from geo.citations.browser import Observation
        row = Observation.model_validate_json(body)
        return Parsed(answer_text=row.answer_text, cited_urls=row.cited_urls, model=row.model_label)


ENGINES: dict[str, type] = {"perplexity": Perplexity, "openai": OpenAI, "google_aio": GoogleAIO, "chatgpt_browser": ChatGPTBrowser}


def get_engine(name: str) -> Engine:
    if name not in ENGINES:
        raise EngineError(f"unknown engine {name!r}; known: {', '.join(ENGINES)}")
    return ENGINES[name]()
