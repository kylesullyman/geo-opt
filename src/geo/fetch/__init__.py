"""THE only network layer. Nothing outside geo.fetch imports httpx.

- Per-host rate limiting (min interval between requests to the same host).
- In-run memoization (the same GET in one run is fetched once).
- One honest user-agent for site fetches.
- robots.txt respect for our own user-agent (override only for client-authorized audits).
- Every response body is stored verbatim in the blob store and logged as an artifact.
- Secrets never reach the artifact log: headers are redacted before logging.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from geo import __version__
from geo.robots import Robots
from geo.models import utcnow
from geo.store import Store

USER_AGENT = f"GEOAudit/{__version__} (+https://github.com/; site visibility audit)"
UA_TOKEN = "GEOAudit"
REDACT_HEADERS = {"authorization", "x-api-key", "api-key", "cookie", "set-cookie"}
REDACT_PARAMS = {"api_key", "key", "token", "access_token"}


# Test seam: tests install an httpx.MockTransport here so the whole CLI runs offline.
DEFAULT_TRANSPORT: httpx.BaseTransport | None = None


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


@dataclass
class Response:
    url: str
    final_url: str
    status: int | None
    headers: dict[str, str]
    body: bytes
    raw_ref: str | None
    elapsed_s: float
    error: str | None = None
    redirects: list[str] = field(default_factory=list)

    @classmethod
    def failed(cls, url: str, error: str) -> Response:
        return cls(url=url, final_url=url, status=None, headers={}, body=b"", raw_ref=None, elapsed_s=0.0, error=error)

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300

    @property
    def text(self) -> str:
        ctype = self.headers.get("content-type", "")
        enc = "utf-8"
        if "charset=" in ctype:
            enc = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
        try:
            return self.body.decode(enc, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)


def _redact_headers(h: dict[str, str]) -> dict[str, str]:
    return {k: ("<redacted>" if k.lower() in REDACT_HEADERS else v) for k, v in h.items()}


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    q = []
    for kv in parts.query.split("&"):
        k, _, _v = kv.partition("=")
        q.append(f"{k}=<redacted>" if k.lower() in REDACT_PARAMS else kv)
    return parts._replace(query="&".join(q)).geturl()


class _RateLimiter:
    def __init__(self, min_interval_s: float):
        self.min_interval_s = min_interval_s
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def wait(self, host: str) -> None:
        if self.min_interval_s <= 0:
            return
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            now = time.monotonic()
            delay = self._last.get(host, 0) + self.min_interval_s - now
            if delay > 0:
                time.sleep(delay)
            self._last[host] = time.monotonic()


class Fetcher:
    """Bound to one Run: every artifact it produces is logged under run_id."""

    def __init__(
        self,
        store: Store,
        run_id: str,
        *,
        respect_robots: bool = True,
        site_interval_s: float = 1.0,
        api_interval_s: float = 0.0,
        timeout_s: float = 20.0,
        api_timeout_s: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.store = store
        self.run_id = run_id
        self.respect_robots = respect_robots
        self.timeout_s = timeout_s
        self.api_timeout_s = api_timeout_s
        self._site_rl = _RateLimiter(site_interval_s)
        self._api_rl = _RateLimiter(api_interval_s)
        self._memo: dict[tuple[str, str, str | None], Response] = {}
        self._memo_lock = threading.Lock()
        self._robots: dict[str, Robots | None] = {}
        self.client = httpx.Client(
            transport=transport or DEFAULT_TRANSPORT,
            follow_redirects=False,
            timeout=timeout_s,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- internals ------------------------------------------------------------------

    def _log(
        self,
        *,
        kind: str,
        method: str,
        url: str,
        resp: Response,
        request: dict[str, Any] | None = None,
    ) -> None:
        self.store.log_artifact(
            self.run_id,
            kind=kind,
            method=method,
            url=_redact_url(url),
            final_url=_redact_url(resp.final_url),
            status=resp.status,
            request=request,
            headers=_redact_headers(resp.headers),
            raw_ref=resp.raw_ref,
            fetched_at=utcnow(),
            error=resp.error,
        )

    def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        follow_redirects: bool = True,
        max_redirects: int = 10,
    ) -> Response:
        start = time.monotonic()
        redirects: list[str] = []
        current = url
        try:
            for _ in range(max_redirects + 1):
                r = self.client.request(
                    method,
                    current,
                    headers=headers,
                    json=json_body,
                    params=params,
                    timeout=timeout or self.timeout_s,
                )
                if follow_redirects and r.is_redirect and "location" in r.headers:
                    redirects.append(current)
                    current = str(r.url.join(r.headers["location"]))
                    params = None
                    continue
                body = r.content
                return Response(
                    url=url,
                    final_url=str(r.url),
                    status=r.status_code,
                    headers={k.lower(): v for k, v in r.headers.items()},
                    body=body,
                    raw_ref=self.store.put_blob(body),
                    elapsed_s=time.monotonic() - start,
                    redirects=redirects,
                )
            return Response(
                url=url,
                final_url=current,
                status=None,
                headers={},
                body=b"",
                raw_ref=None,
                elapsed_s=time.monotonic() - start,
                error=f"too many redirects (>{max_redirects})",
                redirects=redirects,
            )
        except httpx.HTTPError as e:
            return Response(
                url=url,
                final_url=current,
                status=None,
                headers={},
                body=b"",
                raw_ref=None,
                elapsed_s=time.monotonic() - start,
                error=f"{type(e).__name__}: {e}",
                redirects=redirects,
            )

    def _robots_for(self, url: str) -> Robots | None:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            resp = self.get(origin + "/robots.txt", _skip_robots=True)
            if resp.status and 200 <= resp.status < 300:
                self._robots[origin] = Robots.parse(resp.text)
            else:
                # 4xx/unreachable: nothing to respect. (5xx is reported by the robots
                # check; audits are client-authorized so we proceed.)
                self._robots[origin] = None
        return self._robots[origin]

    # --- public API -----------------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        _skip_robots: bool = False,
        follow_redirects: bool = True,
        user_agent: str | None = None,
    ) -> Response:
        """Fetch a page from a (client or competitor) website.

        user_agent overrides our honest UA; only the opt-in WAF probe uses it, to
        see what a given AI crawler UA is served on a client-authorized audit.
        """
        key = ("GET" if follow_redirects else "GET-noredirect", url, user_agent)
        with self._memo_lock:
            if key in self._memo:
                return self._memo[key]
        if self.respect_robots and not _skip_robots:
            rp = self._robots_for(url)
            if rp is not None and not rp.allowed(UA_TOKEN, url):
                raise RobotsDisallowed(f"robots.txt disallows {UA_TOKEN} from {url}")
        self._site_rl.wait(urlsplit(url).netloc)
        headers = {"User-Agent": user_agent} if user_agent else None
        resp = self._send("GET", url, headers=headers, follow_redirects=follow_redirects)
        self._log(
            kind="site",
            method="GET",
            url=url,
            resp=resp,
            request={"user_agent": user_agent} if user_agent else None,
        )
        with self._memo_lock:
            self._memo[key] = resp
        return resp

    def api(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        retries: int = 2,
    ) -> Response:
        """Call a third-party API (LLM/SERP). Never memoized: each sample is a real call."""
        host = urlsplit(url).netloc
        attempt = 0
        while True:
            self._api_rl.wait(host)
            resp = self._send(
                method,
                url,
                headers=headers,
                json_body=json_body,
                params=params,
                timeout=self.api_timeout_s,
            )
            self._log(
                kind="api",
                method=method,
                url=url + ("?" + "&".join(f"{k}={v}" for k, v in (params or {}).items()) if params else ""),
                resp=resp,
                request={"json": json_body, "headers": _redact_headers(headers or {})},
            )
            retryable = resp.error is not None or (resp.status in (429, 500, 502, 503, 504))
            if not retryable or attempt >= retries:
                return resp
            attempt += 1
            ra = resp.headers.get("retry-after")
            try:
                delay = float(ra) if ra else 2.0 * (2**attempt)
            except ValueError:
                delay = 2.0 * (2**attempt)
            time.sleep(min(delay, 30.0))
