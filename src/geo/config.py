"""Per-client configuration: the judgment layer.

clients/<slug>/client.yaml   domain, pages, competitors, CMS, contacts
clients/<slug>/facts.yaml    ground truth; the ONLY source fix generators may use
clients/<slug>/queries.yaml  test queries + locality
clients/<slug>/proposed/     generated fixes awaiting review
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from geo.models import Locality


class ConfigError(Exception):
    pass


def geo_home() -> Path:
    return Path(os.environ.get("GEO_HOME") or Path.cwd()).resolve()


class Competitor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    domain: str | None = None
    aliases: list[str] = Field(default_factory=list)


class ClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str  # business name as customers say it
    domain: str  # "smilesdental.com" (no scheme)
    aliases: list[str] = Field(default_factory=list)  # other names the business goes by
    pages: list[str] = Field(default_factory=lambda: ["/"])  # key paths to audit
    competitors: list[Competitor] = Field(default_factory=list)
    cms: str | None = None
    contacts: list[dict[str, Any]] = Field(default_factory=list)
    training_bots: Literal["unchanged", "allow", "block"] = "unchanged"
    notes: str | None = None

    @field_validator("domain")
    @classmethod
    def _bare_domain(cls, v: str) -> str:
        v = v.strip().lower()
        v = re.sub(r"^https?://", "", v).rstrip("/")
        return v

    @field_validator("pages")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        out = []
        for p in v:
            if not p.startswith("/"):
                raise ValueError(f"page {p!r} must be a path starting with '/'")
            out.append(p)
        return out or ["/"]

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}"


class Hours(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[str]  # ["Monday", "Tuesday"] or ["Mo", "Tu"]
    opens: str  # "08:00"
    closes: str  # "17:00"


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")
    street: str
    city: str
    region: str  # "CA"
    postal_code: str
    country: str = "US"


class Facts(BaseModel):
    """Ground-truth business facts from the intake call. Everything optional:
    a missing fact must surface as [NEEDS INPUT], never be guessed."""

    model_config = ConfigDict(extra="forbid")

    legal_name: str | None = None
    schema_type: str | None = None  # "Dentist", "Plumber", "Restaurant"...
    telephone: str | None = None
    email: str | None = None
    address: Address | None = None
    geo: dict[str, float] | None = None  # {latitude, longitude}
    hours: list[Hours] | None = None
    service_area: list[str] | None = None
    services: list[str] | None = None
    price_range: str | None = None
    payment_accepted: list[str] | None = None
    insurance_accepted: list[str] | None = None
    languages: list[str] | None = None
    licenses: list[str] | None = None
    founded: str | None = None
    same_as: list[str] | None = None  # GBP, Yelp, Facebook URLs
    logo: str | None = None
    image: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class QueryDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    samples: int = Field(default=5, ge=1)
    engines: list[str] = Field(default_factory=lambda: ["perplexity", "openai", "google_aio"])
    locality: Locality = Field(default_factory=Locality)


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    intent: str | None = None
    samples: int | None = Field(default=None, ge=1)
    engines: list[str] | None = None
    locality: Locality | None = None

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", v):
            raise ValueError(f"query id {v!r} must be a lowercase slug")
        return v


class QueriesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    defaults: QueryDefaults = Field(default_factory=QueryDefaults)
    queries: list[Query] = Field(default_factory=list)

    def resolved(self) -> list[tuple[Query, Locality, list[str], int]]:
        out = []
        for q in self.queries:
            out.append(
                (
                    q,
                    q.locality or self.defaults.locality,
                    q.engines or self.defaults.engines,
                    q.samples or self.defaults.samples,
                )
            )
        return out


class Client(BaseModel):
    slug: str
    dir: Path
    config: ClientConfig
    facts: Facts
    queries: QueriesConfig

    @property
    def proposed_dir(self) -> Path:
        return self.dir / "proposed"

    def entities(self) -> list[str]:
        return ["client"] + [c.name for c in self.config.competitors]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at top level")
    return data


def clients_dir(home: Path | None = None) -> Path:
    return (home or geo_home()) / "clients"


def load_client(slug: str, home: Path | None = None) -> Client:
    d = clients_dir(home) / slug
    if not (d / "client.yaml").exists():
        raise ConfigError(f"no client config at {d / 'client.yaml'} (run `geo init {slug}`)")
    try:
        return Client(
            slug=slug,
            dir=d,
            config=ClientConfig(**_load_yaml(d / "client.yaml")),
            facts=Facts(**_load_yaml(d / "facts.yaml")),
            queries=QueriesConfig(**_load_yaml(d / "queries.yaml")),
        )
    except ValidationError as e:
        raise ConfigError(f"client {slug!r}: {e}") from e


def lint_client(client: Client) -> list[str]:
    """Judgment-layer sanity warnings. Not errors: the config still loads."""
    warnings: list[str] = []
    qs = client.queries
    if not qs.queries:
        warnings.append("queries.yaml has no queries; citation runs will do nothing")
    ids = [q.id for q in qs.queries]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        warnings.append(f"duplicate query ids: {sorted(dupes)}")
    for q, loc, engines, n in qs.resolved():
        if n < 5:
            warnings.append(f"query {q.id!r}: samples={n} (<5); rates will be noisy")
        if loc.city and loc.city.lower() not in q.text.lower():
            warnings.append(
                f"query {q.id!r}: city {loc.city!r} not in query text; encode locality in the text too"
            )
        if not loc.city:
            warnings.append(f"query {q.id!r}: no locality.city pinned")
        if "google_aio" in engines and not loc.serp_location:
            warnings.append(f"query {q.id!r}: google_aio without locality.serp_location")
    if not client.facts.telephone:
        warnings.append("facts.yaml: telephone missing (NAP checks and schema fix will be limited)")
    if not client.facts.address:
        warnings.append("facts.yaml: address missing (NAP checks and schema fix will be limited)")
    return warnings


CLIENT_TEMPLATE = """\
# Judgment layer: edit by hand, commit to git.
name: {name}
domain: {domain}
aliases: []            # other names customers/engines use for the business
pages:                 # key pages to audit (paths)
  - /
competitors: []        # - {{name: Other Dental, domain: otherdental.com}}
cms: null              # wordpress | squarespace | wix | shopify | custom
contacts: []
training_bots: unchanged   # unchanged | allow | block  (client's choice; retrieval bots are always allowed by fixes)
"""

FACTS_TEMPLATE = """\
# Ground truth from the intake call. Fix generators may ONLY use what is here.
# Leave unknowns null: they become [NEEDS INPUT], never guesses.
legal_name: null
schema_type: LocalBusiness   # most specific schema.org type, e.g. Dentist, Plumber, Restaurant
telephone: null              # "+1-714-555-0100"
email: null
address: null
#  street: 123 Main St, Suite 4
#  city: Orange
#  region: CA
#  postal_code: "92866"
geo: null                    # {{latitude: 33.787, longitude: -117.853}}
hours: null
#  - {{days: [Monday, Tuesday, Wednesday, Thursday, Friday], opens: "08:00", closes: "17:00"}}
service_area: null           # [Orange, Tustin, Villa Park]
services: null
price_range: null            # "$$"
payment_accepted: null
insurance_accepted: null
languages: null
licenses: null
founded: null
same_as: null                # [Google Business Profile URL, Yelp URL, ...]
logo: null
image: null
"""

QUERIES_TEMPLATE = """\
# Which customer questions matter for THIS business. Judgment, not mechanics.
defaults:
  samples: 5                 # N >= 5: visibility is a rate, not a checkmark
  engines: [perplexity, openai, google_aio]
  locality:
    city: {city}
    region: California
    country: US
    timezone: America/Los_Angeles
    serp_location: "{city},California,United States"
queries: []
#  - id: best-dentist
#    text: best dentist in {city} CA
#    intent: discovery
"""


def scaffold_client(slug: str, name: str, domain: str, city: str, home: Path | None = None) -> Path:
    d = clients_dir(home) / slug
    if d.exists():
        raise ConfigError(f"{d} already exists")
    (d / "proposed").mkdir(parents=True)
    (d / "client.yaml").write_text(CLIENT_TEMPLATE.format(name=name, domain=domain))
    (d / "facts.yaml").write_text(FACTS_TEMPLATE)
    (d / "queries.yaml").write_text(QUERIES_TEMPLATE.format(city=city))
    (d / "proposed" / ".gitkeep").write_text("")
    return d
