"""Human-reviewed ChatGPT web observations. No browser credentials or network access."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from jinja2 import Environment, PackageLoader
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from geo import __version__
from geo.config import Client
from geo.models import CitationSample, Run, RunKind, new_run_id, utcnow

SURFACE = "ChatGPT web app — manually captured from a signed-in browser. Account, model, personalization and location can affect answers; not an API result."


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str
    sample_idx: int = Field(ge=0)
    prompt: str
    answer_text: str = ""
    cited_urls: list[str] = Field(default_factory=list)
    observed_at: datetime
    model_label: str = Field(min_length=1)
    conversation_url: str | None = None
    search_used: Literal["yes", "no", "unknown"]
    session: Literal["temporary", "new_chat"]
    personalization: Literal["off", "on", "unknown"]
    citations_reviewed: bool
    complete: bool
    error: str | None = None
    notes: str = ""

    @field_validator("observed_at")
    @classmethod
    def timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        if value > utcnow() + timedelta(minutes=5):
            raise ValueError("observed_at cannot be in the future")
        return value

    @field_validator("cited_urls")
    @classmethod
    def urls(cls, values):
        for value in values:
            p = urlsplit(value)
            if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
                raise ValueError("citations must be full public HTTP(S) URLs without credentials")
        return list(dict.fromkeys(values))

    @field_validator("conversation_url")
    @classmethod
    def conversation(cls, value):
        if value:
            p = urlsplit(value)
            if p.scheme != "https" or p.hostname not in ("chatgpt.com", "chat.openai.com") or not p.path.startswith(("/c/", "/share/")) or p.query or p.fragment or p.username or p.password:
                raise ValueError("use a ChatGPT conversation URL without query parameters, or leave blank")
        return value or None

    @model_validator(mode="after")
    def finished(self):
        if not self.model_label.strip() or (self.error is not None and not self.error.strip()):
            raise ValueError("model label and failure reason cannot be whitespace")
        if not self.complete:
            raise ValueError("unfinished observations cannot be imported")
        if self.error:
            if self.answer_text.strip() or self.cited_urls:
                raise ValueError("failed observations must not contain an answer or citations")
        elif not self.answer_text.strip() or not self.citations_reviewed:
            raise ValueError("a completed answer and reviewed citation list are required")
        return self


class Capture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["geo-chatgpt-browser-1"]
    batch_id: str = Field(pattern=r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")
    client: str
    observations: list[Observation] = Field(min_length=1)


def prepare(client: Client, root: Path, samples: int | None = None, queries: list[str] | None = None) -> dict:
    resolved = client.queries.resolved()
    ids = [q.id for q, *_ in resolved]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate query ids; fix queries.yaml first")
    if queries and set(queries) - set(ids):
        raise ValueError(f"unknown query ids: {sorted(set(queries) - set(ids))}")
    jobs = []
    for q, loc, _engines, n in resolved:
        if queries and q.id not in queries:
            continue
        for idx in range(samples if samples is not None else n):
            jobs.append({"query_id": q.id, "sample_idx": idx, "prompt": q.text,
                         "locality": loc.model_dump(mode="json")})
    if not jobs:
        raise ValueError("no queries to capture; populate queries.yaml first")
    batch = new_run_id()
    plan = {"format": "geo-chatgpt-browser-1", "batch_id": batch, "client": client.slug,
            "created_at": utcnow().isoformat(), "jobs": jobs,
            "profile": {"config": client.config.model_dump(mode="json"),
                        "facts": client.facts.model_dump(mode="json"),
                        "queries": client.queries.model_dump(mode="json")}}
    folder = root / "captures" / client.slug / batch
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    env = Environment(loader=PackageLoader("geo", "report/templates"), autoescape=True)
    (folder / "capture.html").write_text(env.get_template("capture.html.j2").render(plan=plan), encoding="utf-8")
    return {"batch_id": batch, "samples": len(jobs), "html": str(folder / "capture.html"), "plan": str(folder / "plan.json")}


def load_capture(client: Client, root: Path, path: Path, allow_partial: bool = False):
    raw = path.read_bytes()
    capture = Capture.model_validate_json(raw)
    if capture.client != client.slug:
        raise ValueError("capture belongs to a different client")
    plan_path = root / "captures" / client.slug / capture.batch_id / "plan.json"
    plan_raw = plan_path.read_bytes()
    plan = json.loads(plan_raw)
    expected = {(j["query_id"], j["sample_idx"]): j for j in plan["jobs"]}
    seen = set()
    for row in capture.observations:
        key = (row.query_id, row.sample_idx)
        if key in seen:
            raise ValueError(f"duplicate observation: {key}")
        seen.add(key)
        if key not in expected or row.prompt != expected[key]["prompt"]:
            raise ValueError(f"observation does not match the prepared prompt/sample: {key}")
        if row.observed_at < datetime.fromisoformat(plan["created_at"]) - timedelta(minutes=5):
            raise ValueError("observation predates this capture plan")
    if len(seen) != len(expected) and not allow_partial:
        raise ValueError(f"only {len(seen)}/{len(expected)} observations; finish the batch or explicitly use --allow-partial")
    return capture, plan, raw, plan_raw


def make_run(client: Client, store, capture: Capture, plan: dict, raw: bytes, plan_raw: bytes) -> Run:
    from geo.citations import PARSER_VERSION
    from geo.citations.mentions import Detector, entities_for
    from geo.config import ClientConfig, Facts, QueriesConfig

    # A batch is finalized once, including partial imports. The PK also prevents
    # racing importers from saving the same observations twice.
    run_id = "browser-" + capture.batch_id
    if store.conn.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone():
        raise ValueError("this batch was already imported; prepare a new batch for new observations")
    snapshot = client.model_copy(update={"config": ClientConfig(**plan["profile"]["config"]),
                                         "facts": Facts(**plan["profile"]["facts"]),
                                         "queries": QueriesConfig(**plan["profile"]["queries"])})
    detector = Detector(entities_for(snapshot))
    jobs = {(j["query_id"], j["sample_idx"]): j for j in plan["jobs"]}
    run = Run(id=run_id, client=client.slug, kind=RunKind.citations, tool_version=__version__,
              started_at=min(o.observed_at for o in capture.observations))
    run.meta = {"source": "chatgpt_browser", "surface": SURFACE, "batch_id": capture.batch_id,
                "capture_raw_ref": store.put_blob(raw), "plan_raw_ref": store.put_blob(plan_raw),
                "profile_snapshot": plan["profile"], "planned_samples": len(jobs),
                "captured_samples": len(capture.observations), "imported_at": utcnow().isoformat(),
                "locality_control": "query text only; browser location is not pinned",
                "observations": []}
    for row in sorted(capture.observations, key=lambda o: (o.query_id, o.sample_idx)):
        body = row.model_dump_json().encode()
        ref = store.put_blob(body)
        run.samples.append(CitationSample(
            query_id=row.query_id, query_text=row.prompt, sample_idx=row.sample_idx,
            locality=jobs[(row.query_id, row.sample_idx)]["locality"], engine="chatgpt_browser",
            model=row.model_label, raw_ref=ref, error=row.error, answer_text=row.answer_text,
            cited_urls=row.cited_urls, parser_version=PARSER_VERSION,
            mentions=detector.detect(row.answer_text, row.cited_urls) if not row.error else [],
        ))
        run.meta["observations"].append(row.model_dump(mode="json", exclude={"answer_text", "cited_urls"}) | {"raw_ref": ref})
        store.log_artifact(run.id, kind="browser_capture", method="IMPORT",
                           url=row.conversation_url or "https://chatgpt.com/", final_url=row.conversation_url,
                           status=None, request={"query_id": row.query_id, "sample_idx": row.sample_idx},
                           headers=None, raw_ref=ref, fetched_at=row.observed_at, error=row.error)
    run.meta["citation_status"] = ("failed" if all(s.error for s in run.samples) else
                                   "partial" if len(run.samples) < len(jobs) or any(s.error for s in run.samples) else "complete")
    return run
