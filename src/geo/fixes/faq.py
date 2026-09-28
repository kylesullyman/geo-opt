"""FAQ draft built ONLY from facts.yaml, with a fact trace on every answer.

Deterministic templates, not an LLM: every sentence is traceable to a fact.
Questions whose facts are missing are emitted with [NEEDS INPUT] instead of an
answer. The draft is for human review and rewriting in the business's voice;
the FAQPage JSON-LD is validated but should only be published once the visible
page text matches it.
"""

from __future__ import annotations

import json
from typing import Any

from geo import schemaorg
from geo.config import Client
from geo.fixes import NEEDS_INPUT, Artifact, generator
from geo.fixes.schema import _day


def _join(items: list[str]) -> str:
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _hours_text(client: Client) -> str | None:
    hs = client.facts.hours
    if not hs:
        return None
    parts = []
    for h in hs:
        days = [_day(d) for d in h.days]
        span = f"{days[0]} through {days[-1]}" if len(days) > 2 else _join(days)
        parts.append(f"{span}, {h.opens} to {h.closes}")
    return "; ".join(parts)


def questions(client: Client) -> list[tuple[str, str | None, list[str]]]:
    """(question, answer or None, facts used)."""
    f, n = client.facts, client.config.name
    city = f.address.city if f.address else None
    out: list[tuple[str, str | None, list[str]]] = []

    out.append((
        f"Where is {n} located?",
        f"{n} is at {f.address.street}, {f.address.city}, {f.address.region} {f.address.postal_code}." if f.address else None,
        ["address"],
    ))
    h = _hours_text(client)
    out.append((f"What are {n}'s hours?", f"{n} is open {h}." if h else None, ["hours"]))
    out.append((
        f"How do I contact {n}?",
        (f"Call {f.telephone}" + (f" or email {f.email}" if f.email else "") + ".") if f.telephone else None,
        ["telephone", "email"] if f.email else ["telephone"],
    ))
    out.append((
        f"What services does {n} offer?",
        f"{n} offers {_join(f.services)}." if f.services else None,
        ["services"],
    ))
    out.append((
        f"What areas does {n} serve?" if not city else f"Does {n} serve areas outside {city}?",
        f"{n} serves {_join(f.service_area)}." if f.service_area else None,
        ["service_area"],
    ))
    if f.insurance_accepted is not None or (f.schema_type or "").lower() in {"dentist", "physician", "medicalclinic", "optician"}:
        out.append((
            f"What insurance does {n} accept?",
            f"{n} accepts {_join(f.insurance_accepted)}." if f.insurance_accepted else None,
            ["insurance_accepted"],
        ))
    if f.payment_accepted:
        out.append((f"What payment methods does {n} accept?", f"{n} accepts {_join(f.payment_accepted)}.", ["payment_accepted"]))
    if f.languages:
        out.append((f"What languages are spoken at {n}?", f"Staff at {n} speak {_join(f.languages)}.", ["languages"]))
    if f.licenses:
        out.append((f"Is {n} licensed?", f"{n} holds the following: {_join(f.licenses)}.", ["licenses"]))
    return out


@generator("faq")
def generate(client: Client) -> Artifact:
    qs = questions(client)
    missing = sorted({fact for _q, a, facts in qs if a is None for fact in facts[:1]})
    lines = [
        f"# FAQ draft: {client.config.name}",
        "",
        "> DRAFT for human review. Every answer is built only from facts.yaml; the",
        "> `facts:` trace shows which. Rewrite in the business's voice, but do not add",
        "> claims that are not in facts.yaml. Questions marked [NEEDS INPUT] must be",
        "> answered in facts.yaml (or dropped) before anything is published.",
        "",
    ]
    answered: list[dict[str, Any]] = []
    for q, a, facts in qs:
        lines.append(f"## {q}")
        lines.append("")
        lines.append(a if a else f"{NEEDS_INPUT} add `{facts[0]}` to facts.yaml")
        lines.append("")
        lines.append(f"<!-- facts: {', '.join(facts)} -->")
        lines.append("")
        if a:
            answered.append({"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}})

    validation = None
    if answered:
        doc = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": answered}
        rep = schemaorg.validate(doc)
        validation = rep.as_dict()
        lines += [
            "---",
            "",
            "FAQPage JSON-LD for the answered questions (publish only with matching visible text):",
            "",
            "```json",
            json.dumps(doc, indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    ok = bool(validation and validation["ok"])
    return Artifact(
        filename="faq-draft.md",
        content="\n".join(lines),
        ready=ok and not missing,
        needs_input=missing,
        validation=validation,
        notes=["Always a draft: requires human review before publishing."],
    )
