"""The core contract. Everything else depends on these types.

Finding, Fix and Run are the stable seam: checks produce Findings, the store
persists Runs, reports and diffs read them back. Change with care.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_run_id() -> str:
    # Sortable-ish: timestamp prefix + random suffix.
    return utcnow().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


class Severity(StrEnum):
    blocker = "blocker"
    warning = "warning"
    info = "info"

    @property
    def rank(self) -> int:
        return {"blocker": 0, "warning": 1, "info": 2}[self.value]


class Fix(BaseModel):
    """A proposed remediation. Never auto-applied."""

    model_config = ConfigDict(frozen=True)

    generator: str  # which fixes/ generator can produce the artifact, e.g. "robots"
    summary: str  # human instruction, e.g. "Allow OAI-SearchBot in robots.txt"
    artifact: str | None = None  # proposed/<file> this fix is staged into, if any


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    check_id: str  # "robots.blocks_oai_searchbot"
    severity: Severity
    title: str
    evidence: dict[str, Any] = Field(default_factory=dict)  # what was observed, raw
    fix: Fix | None = None
    raw_ref: str | None = None  # sha256 of the artifact this came from
    parser_version: str = "0"  # version of the check that derived it


class Locality(BaseModel):
    """Where a query is pretended to originate. Stored on every sample."""

    model_config = ConfigDict(frozen=True)

    city: str | None = None
    region: str | None = None  # state, e.g. "California"
    country: str = "US"
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None  # IANA, e.g. "America/Los_Angeles"
    serp_location: str | None = None  # SERP vendor canonical, e.g. "Orange,California,United States"


class Mention(BaseModel):
    """Derived: did a completion mention an entity, and how do we know."""

    model_config = ConfigDict(frozen=True)

    entity: str  # "client" or competitor name
    mentioned: bool
    cited: bool  # entity domain appears among the engine's cited URLs
    matched_on: list[str] = Field(default_factory=list)  # ["domain", "name", "phone", "address"]


class CitationSample(BaseModel):
    """One call to one engine for one query. Raw completion lives in the blob store."""

    query_id: str
    query_text: str
    locality: Locality
    engine: str  # "perplexity" | "openai" | "google_aio"
    model: str | None = None
    sample_idx: int
    raw_ref: str | None = None
    error: str | None = None
    answer_text: str = ""  # extracted; re-derivable from raw
    empty_reason: str | None = None  # e.g. "no_ai_overview": a valid observation, not an error
    cited_urls: list[str] = Field(default_factory=list)
    mentions: list[Mention] = Field(default_factory=list)
    parser_version: str = "0"

    def mention_for(self, entity: str) -> Mention | None:
        return next((m for m in self.mentions if m.entity == entity), None)


class RunKind(StrEnum):
    audit = "audit"
    citations = "citations"


class Run(BaseModel):
    """An immutable execution record. Written once, never updated."""

    id: str = Field(default_factory=new_run_id)
    client: str
    kind: RunKind
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    tool_version: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    samples: list[CitationSample] = Field(default_factory=list)


class Rate(BaseModel):
    """Visibility is a rate over N samples, never a checkmark."""

    query_id: str
    query_text: str
    engine: str
    entity: str
    n: int  # successful samples
    mentioned: int
    cited: int
    errors: int = 0

    @property
    def mention_rate(self) -> float | None:
        return self.mentioned / self.n if self.n else None

    @property
    def citation_rate(self) -> float | None:
        return self.cited / self.n if self.n else None

    @property
    def label(self) -> str:
        return f"{self.mentioned}/{self.n}" if self.n else "n/a"


def compute_rates(samples: list[CitationSample], entities: list[str]) -> list[Rate]:
    groups: dict[tuple[str, str], list[CitationSample]] = {}
    for s in samples:
        groups.setdefault((s.query_id, s.engine), []).append(s)
    out: list[Rate] = []
    for (qid, engine), group in groups.items():
        ok = [s for s in group if not s.error]
        for entity in entities:
            ms = [s.mention_for(entity) for s in ok]
            out.append(
                Rate(
                    query_id=qid,
                    query_text=group[0].query_text,
                    engine=engine,
                    entity=entity,
                    n=len(ok),
                    mentioned=sum(1 for m in ms if m and m.mentioned),
                    cited=sum(1 for m in ms if m and m.cited),
                    errors=len(group) - len(ok),
                )
            )
    return out
