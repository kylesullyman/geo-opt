"""Deterministic mention detection. No model calls.

An entity is *mentioned* if the answer text contains its domain, its name
(or an alias), its phone number, or its street address, after normalization.
It is *cited* if one of the engine's cited URLs is on its domain.

PARSER_VERSION is stored on every sample. Bump it when matching changes and
run `geo reparse` to re-derive history from the raw completions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from geo.config import Client
from geo.models import Mention
from geo.normalize import domain_matches, find_phones, fold, norm_domain, norm_name, norm_phone, street_core

PARSER_VERSION = "mention-1"


@dataclass(frozen=True)
class Entity:
    key: str  # "client" or competitor name
    names: tuple[str, ...]
    domain: str | None = None
    phone: str | None = None  # 10 digits
    street: str | None = None  # street_core form

    @classmethod
    def for_client(cls, client: Client) -> Entity:
        f = client.facts
        names = [client.config.name, *client.config.aliases]
        if f.legal_name:
            names.append(f.legal_name)
        return cls(
            key="client",
            names=tuple(dict.fromkeys(n for n in names if n)),
            domain=client.config.domain,
            phone=norm_phone(f.telephone or ""),
            street=street_core(f.address.street) if f.address else None,
        )


def entities_for(client: Client) -> list[Entity]:
    out = [Entity.for_client(client)]
    for c in client.config.competitors:
        out.append(Entity(key=c.name, names=tuple([c.name, *c.aliases]), domain=c.domain))
    return out


def _name_re(name: str) -> re.Pattern[str] | None:
    n = norm_name(name)
    if len(n) < 4:  # too short to match safely
        return None
    return re.compile(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])")


def _domain_re(domain: str) -> re.Pattern[str]:
    d = norm_domain(domain)
    return re.compile(r"(?<![a-z0-9.\-])(?:www\.)?" + re.escape(d) + r"(?![a-z0-9\-])", re.I)


@dataclass
class Detector:
    entities: list[Entity]
    _name_res: dict[str, list[re.Pattern[str]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for e in self.entities:
            self._name_res[e.key] = [r for r in (_name_re(n) for n in e.names) if r]

    def detect(self, text: str, cited_urls: list[str]) -> list[Mention]:
        folded = fold(text)
        # fold() turns "." into space; keep a lowercase copy for domains.
        lower = text.lower()
        phones = find_phones(text)
        street_text = street_core(text) if text else ""
        from geo.normalize import norm_street

        street_full = norm_street(text) if text else ""
        out = []
        for e in self.entities:
            on: list[str] = []
            if e.domain and _domain_re(e.domain).search(lower):
                on.append("domain")
            if any(r.search(folded) for r in self._name_res[e.key]):
                on.append("name")
            if e.phone and e.phone in phones:
                on.append("phone")
            if e.street and len(e.street) >= 6 and (e.street in street_full or e.street in street_text):
                on.append("address")
            cited = bool(e.domain) and any(domain_matches(u, e.domain) for u in cited_urls)
            out.append(Mention(entity=e.key, mentioned=bool(on), cited=cited, matched_on=on))
        return out
