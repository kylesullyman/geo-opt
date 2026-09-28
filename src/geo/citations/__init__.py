"""The query runner. Samples N times per (query, engine), stores every raw
completion, derives mentions deterministically, and reports rates."""

from __future__ import annotations

import concurrent.futures as cf
from collections.abc import Callable
from dataclasses import dataclass

from geo.citations.engines import ANSWER_PARSER_VERSION, Engine, EngineError, get_engine
from geo.citations.mentions import PARSER_VERSION as MENTION_PARSER_VERSION
from geo.citations.mentions import Detector, entities_for
from geo.config import Client
from geo.fetch import Fetcher
from geo.models import CitationSample, Locality
from geo.store import Store

PARSER_VERSION = f"{ANSWER_PARSER_VERSION}+{MENTION_PARSER_VERSION}"


@dataclass
class Job:
    query_id: str
    query_text: str
    locality: Locality
    engine: str
    sample_idx: int


def plan(
    client: Client,
    *,
    engines: list[str] | None = None,
    queries: list[str] | None = None,
    samples: int | None = None,
) -> list[Job]:
    jobs: list[Job] = []
    for q, loc, q_engines, n in client.queries.resolved():
        if queries and q.id not in queries:
            continue
        for e in q_engines:
            if engines and e not in engines:
                continue
            for i in range(samples or n):
                jobs.append(Job(q.id, q.text, loc, e, i))
    return jobs


def derive(engine: Engine, detector: Detector, sample: CitationSample, body: bytes) -> CitationSample:
    """Pure: raw body -> derived fields. Used on first run and by reparse."""
    try:
        parsed = engine.parse(body)
    except Exception as e:
        return sample.model_copy(update={"error": f"parse: {type(e).__name__}: {e}", "parser_version": PARSER_VERSION})
    return sample.model_copy(
        update={
            "answer_text": parsed.answer_text,
            "cited_urls": parsed.cited_urls,
            "empty_reason": parsed.empty_reason,
            "model": parsed.model or sample.model,
            "mentions": detector.detect(parsed.answer_text, parsed.cited_urls),
            "parser_version": PARSER_VERSION,
        }
    )


def _one(fetcher: Fetcher, engine: Engine, detector: Detector, job: Job) -> CitationSample:
    base = CitationSample(
        query_id=job.query_id,
        query_text=job.query_text,
        locality=job.locality,
        engine=job.engine,
        sample_idx=job.sample_idx,
        parser_version=PARSER_VERSION,
    )
    try:
        resp = engine.call(fetcher, job.query_text, job.locality)
    except EngineError as e:
        return base.model_copy(update={"error": str(e)})
    if not resp.ok:
        detail = resp.error or f"HTTP {resp.status}: {resp.body[:300].decode('utf-8', 'replace')}"
        return base.model_copy(update={"error": detail, "raw_ref": resp.raw_ref})
    return derive(engine, detector, base.model_copy(update={"raw_ref": resp.raw_ref}), resp.body)


def run_citations(
    client: Client,
    fetcher: Fetcher,
    *,
    engines: list[str] | None = None,
    queries: list[str] | None = None,
    samples: int | None = None,
    concurrency: int = 4,
    on_sample: Callable[[CitationSample], None] | None = None,
) -> tuple[list[CitationSample], dict[str, str]]:
    """Returns (samples, skipped_engines{name: reason})."""
    jobs = plan(client, engines=engines, queries=queries, samples=samples)
    detector = Detector(entities_for(client))
    eng: dict[str, Engine] = {}
    skipped: dict[str, str] = {}
    for name in sorted({j.engine for j in jobs}):
        try:
            e = get_engine(name)
        except EngineError as ex:
            skipped[name] = str(ex)
            continue
        if not e.available():
            skipped[name] = f"{e.env_key} not set" if e.env_key else "import-only: use geo browser prepare/import"
            continue
        eng[name] = e
    jobs = [j for j in jobs if j.engine in eng]
    out: list[CitationSample] = []
    with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futs = [ex.submit(_one, fetcher, eng[j.engine], detector, j) for j in jobs]
        for f in cf.as_completed(futs):
            s = f.result()
            out.append(s)
            if on_sample:
                on_sample(s)
    out.sort(key=lambda s: (s.query_id, s.engine, s.sample_idx))
    return out, skipped


def reparse_run(store: Store, client: Client, run_id: str) -> tuple[int, int]:
    """Re-derive every sample in a stored run from its raw blob with the current
    parser. Appends new derivations; never modifies the original rows.
    Returns (rederived, skipped)."""
    run = store.get_run(run_id)
    new: list[tuple[int, CitationSample]] = []
    skipped = 0
    if run.meta.get("profile_snapshot"):
        from geo.config import ClientConfig, Facts, QueriesConfig
        profile = run.meta["profile_snapshot"]
        client = client.model_copy(update={"config": ClientConfig(**profile["config"]),
                                           "facts": Facts(**profile["facts"]),
                                           "queries": QueriesConfig(**profile["queries"])})
    detector = Detector(entities_for(client))
    for idx, s in enumerate(run.samples):
        if not s.raw_ref or (s.error and not s.error.startswith("parse:")):
            skipped += 1
            continue
        if s.parser_version == PARSER_VERSION:
            skipped += 1
            continue
        body = store.blobs.get(s.raw_ref)
        new.append((idx, derive(get_engine(s.engine), detector, s.model_copy(update={"error": None}), body)))
    written = store.append_derivations(run.id, new) if new else 0
    return written, skipped
