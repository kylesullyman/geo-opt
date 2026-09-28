"""Proposed LocalBusiness JSON-LD, built strictly from facts.yaml and validated
against schema.org before a human sees it."""

from __future__ import annotations

import json
import re
from typing import Any

from geo import schemaorg
from geo.config import Client
from geo.fixes import NEEDS_INPUT, Artifact, generator

DAY = {
    "mo": "Monday", "tu": "Tuesday", "we": "Wednesday", "th": "Thursday", "fr": "Friday", "sa": "Saturday", "su": "Sunday",
}


def _day(d: str) -> str:
    k = d.strip().lower()[:2]
    if k not in DAY:
        raise ValueError(f"unrecognized day {d!r}")
    return DAY[k]


def build(client: Client) -> tuple[dict[str, Any], list[str]]:
    f = client.facts
    c = client.config
    missing: list[str] = []

    def need(name: str, value: Any) -> Any:
        if value in (None, "", []):
            missing.append(name)
            return NEEDS_INPUT
        return value

    stype = f.schema_type or "LocalBusiness"
    doc: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": stype,
        "@id": f"{c.base_url}/#business",
        "name": c.name,
        "url": c.base_url + "/",
        "telephone": need("telephone", f.telephone),
    }
    if f.legal_name:
        doc["legalName"] = f.legal_name
    if f.address:
        doc["address"] = {
            "@type": "PostalAddress",
            "streetAddress": f.address.street,
            "addressLocality": f.address.city,
            "addressRegion": f.address.region,
            "postalCode": f.address.postal_code,
            "addressCountry": f.address.country,
        }
    else:
        doc["address"] = need("address", None)
    if f.geo:
        doc["geo"] = {"@type": "GeoCoordinates", "latitude": f.geo["latitude"], "longitude": f.geo["longitude"]}
    if f.hours:
        doc["openingHoursSpecification"] = [
            {"@type": "OpeningHoursSpecification", "dayOfWeek": [_day(d) for d in h.days], "opens": h.opens, "closes": h.closes}
            for h in f.hours
        ]
    else:
        missing.append("hours")
    if f.service_area:
        doc["areaServed"] = [{"@type": "City", "name": a} for a in f.service_area]
    if f.price_range:
        doc["priceRange"] = f.price_range
    if f.payment_accepted:
        doc["paymentAccepted"] = ", ".join(f.payment_accepted)
    if f.email:
        doc["email"] = f.email
    if f.logo:
        doc["logo"] = f.logo
    if f.image:
        doc["image"] = f.image
    if f.same_as:
        doc["sameAs"] = f.same_as
    if f.founded:
        doc["foundingDate"] = f.founded
    if f.languages:
        doc["knowsLanguage"] = f.languages
    if f.services:
        doc["hasOfferCatalog"] = {
            "@type": "OfferCatalog",
            "name": "Services",
            "itemListElement": [{"@type": "Offer", "itemOffered": {"@type": "Service", "name": s}} for s in f.services],
        }
    return doc, missing


@generator("schema")
def generate(client: Client) -> Artifact:
    notes: list[str] = []
    stype = client.facts.schema_type or "LocalBusiness"
    if not schemaorg.is_business_type(stype):
        return Artifact(
            filename="schema.jsonld",
            content=json.dumps({"error": f"facts.yaml schema_type {stype!r} is not a schema.org LocalBusiness/Organization type"}, indent=2) + "\n",
            ready=False,
            needs_input=["schema_type"],
        )
    doc, missing = build(client)
    # Validate a version with placeholders removed, so structure errors surface separately from gaps.
    probe = {k: v for k, v in doc.items() if v != NEEDS_INPUT}
    rep = schemaorg.validate(probe)
    if not client.facts.image:
        notes.append("image missing: Google recommends one for LocalBusiness")
    content = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    if re.search(re.escape(NEEDS_INPUT), content):
        notes.append(f"fill {', '.join(missing)} in facts.yaml and re-run; do not hand-edit this file")
    html = f'<script type="application/ld+json">\n{content}</script>\n'
    notes.append("Paste the <script> block into the site <head> (proposed/schema.html). Then run Google's Rich Results Test on the live page.")
    return Artifact(
        filename="schema.jsonld",
        content=content,
        ready=rep.ok and not missing,
        needs_input=missing,
        validation={**rep.as_dict(), "html_snippet": html},
        notes=notes,
    )
