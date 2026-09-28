"""Identity: JSON-LD presence and validity, LocalBusiness/Organization markup,
and NAP (name/address/phone) consistency against facts.yaml."""

from __future__ import annotations

from typing import Any

from geo import schemaorg
from geo.checks import Page, SiteContext, register
from geo.models import Finding, Fix, Severity
from geo.normalize import find_phones, norm_name, norm_phone, norm_postal, street_core

SCHEMA_FIX = Fix(
    generator="schema",
    summary="Stage validated LocalBusiness JSON-LD from facts.yaml with `geo fix <client> schema`",
    artifact="schema.jsonld",
)


def _jsonld_docs(page: Page) -> list[Any]:
    return list(page.structured.get("json-ld") or [])


def _business_nodes(docs: list[Any]) -> list[dict[str, Any]]:
    out = []
    for d in docs:
        for n in schemaorg.top_nodes(d):
            t = n.get("@type")
            ts = t if isinstance(t, list) else [t] if t else []
            if any(isinstance(x, str) and schemaorg.is_business_type(schemaorg._strip_ns(x)) for x in ts):
                out.append(n)
    return out


@register("schema", version="1")
def schema(ctx: SiteContext) -> list[Finding]:
    """JSON-LD parses, uses real schema.org types/properties, and identifies the business."""
    out: list[Finding] = []
    pages = ctx.html_pages()
    if not pages:
        return out
    all_business: list[tuple[str, dict[str, Any]]] = []
    for page in pages:
        url = page.resp.final_url
        for err in page.structured.get("jsonld_errors", []):
            out.append(
                Finding(
                    check_id="schema.jsonld_syntax",
                    severity=Severity.warning,
                    title=f"{url}: a JSON-LD block is not valid JSON and is ignored by every parser",
                    evidence={"url": url, **err},
                    raw_ref=page.resp.raw_ref,
                    fix=SCHEMA_FIX,
                )
            )
        for doc in _jsonld_docs(page):
            rep = schemaorg.validate(doc)
            if rep.errors:
                out.append(
                    Finding(
                        check_id="schema.invalid",
                        severity=Severity.warning,
                        title=f"{url}: JSON-LD has {len(rep.errors)} schema.org error(s)",
                        evidence={"url": url, "types": rep.types_found, "errors": [str(e) for e in rep.errors][:25]},
                        raw_ref=page.resp.raw_ref,
                        fix=SCHEMA_FIX,
                    )
                )
        all_business += [(url, n) for n in _business_nodes(_jsonld_docs(page))]
        # microdata/RDFa count as identity too
        for syn in ("microdata", "rdfa"):
            for item in page.structured.get(syn) or []:
                if isinstance(item, dict):
                    t = item.get("@type")
                    ts = t if isinstance(t, list) else [t]
                    if any(isinstance(x, str) and schemaorg.is_business_type(schemaorg._strip_ns(x)) for x in ts):
                        all_business.append((url, item))

    if not all_business:
        out.append(
            Finding(
                check_id="schema.no_localbusiness",
                severity=Severity.blocker,
                title="No LocalBusiness/Organization structured data on key pages; business identity is ambiguous to retrieval systems",
                evidence={"pages": [p.resp.final_url for p in pages]},
                fix=SCHEMA_FIX,
            )
        )
        return out

    url, node = all_business[0]
    t = node.get("@type")
    ts = [schemaorg._strip_ns(x) for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)]
    if ts and all(x in ("LocalBusiness", "Organization") for x in ts) and ctx.client.facts.schema_type not in (None, "LocalBusiness", "Organization"):
        out.append(
            Finding(
                check_id="schema.generic_type",
                severity=Severity.info,
                title=f"Markup uses generic {ts[0]}; {ctx.client.facts.schema_type} is more specific",
                evidence={"url": url, "types": ts, "suggested": ctx.client.facts.schema_type},
                fix=SCHEMA_FIX,
            )
        )
    rep = schemaorg.Report()
    schemaorg._rich_result_rules(node, "$", rep, schemaorg.vocab())
    missing_req = [i for i in rep.errors if "requires" in i.message]
    missing_rec = [i.path.split(".")[-1] for i in rep.warnings if "should have" in i.message]
    if missing_req:
        out.append(
            Finding(
                check_id="schema.localbusiness_incomplete",
                severity=Severity.warning,
                title=f"LocalBusiness markup missing required: {', '.join(i.path.split('.')[-1] for i in missing_req)}",
                evidence={"url": url, "missing": [str(i) for i in missing_req]},
                fix=SCHEMA_FIX,
            )
        )
    if missing_rec:
        out.append(
            Finding(
                check_id="schema.localbusiness_sparse",
                severity=Severity.info,
                title=f"LocalBusiness markup lacks recommended: {', '.join(missing_rec)}",
                evidence={"url": url, "missing": missing_rec},
                fix=SCHEMA_FIX,
            )
        )
    return out


def _addr_parts(node: dict[str, Any]) -> dict[str, str]:
    a = node.get("address")
    if isinstance(a, list):
        a = a[0] if a else None
    if isinstance(a, dict):
        return {k: str(a.get(k) or "") for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")}
    if isinstance(a, str):
        return {"raw": a}
    return {}


@register("nap", version="1")
def nap(ctx: SiteContext) -> list[Finding]:
    """Name/address/phone on the site agree with facts.yaml (the ground truth)."""
    facts = ctx.client.facts
    out: list[Finding] = []
    pages = ctx.html_pages()
    if not pages:
        return out
    want_phone = norm_phone(facts.telephone or "")
    want_name = norm_name(ctx.client.config.name)

    # 1) Phone numbers visible on the site that are not the client's number.
    if want_phone:
        seen: dict[str, list[str]] = {}
        for p in pages:
            text = p.visible_text
            for tel in p.tree.css('a[href^="tel:"]'):
                text += " " + (tel.attributes.get("href") or "")[4:]
            for ph in find_phones(text):
                seen.setdefault(ph, []).append(p.resp.final_url)
        if want_phone not in seen:
            out.append(
                Finding(
                    check_id="nap.phone_not_on_site",
                    severity=Severity.warning,
                    title=f"The phone number in facts.yaml ({facts.telephone}) does not appear on key pages",
                    evidence={"expected": want_phone, "found": sorted(seen)},
                )
            )
        others = {ph: urls for ph, urls in seen.items() if ph != want_phone}
        if others and want_phone in seen:
            out.append(
                Finding(
                    check_id="nap.multiple_phones",
                    severity=Severity.info,
                    title=f"{len(others)} other phone number(s) appear alongside the main number; confirm they are intentional",
                    evidence={"expected": want_phone, "others": {k: sorted(set(v)) for k, v in others.items()}},
                )
            )

    # 2) Structured data vs facts.
    for p in pages:
        for node in _business_nodes(_jsonld_docs(p)):
            url = p.resp.final_url
            mism: dict[str, dict[str, str]] = {}
            if node.get("name") and norm_name(str(node["name"])) not in {want_name, *(norm_name(a) for a in ctx.client.config.aliases)}:
                mism["name"] = {"site": str(node["name"]), "facts": ctx.client.config.name}
            if want_phone and node.get("telephone") and norm_phone(str(node["telephone"])) != want_phone:
                mism["telephone"] = {"site": str(node["telephone"]), "facts": facts.telephone or ""}
            if facts.address:
                a = _addr_parts(node)
                if a.get("streetAddress") and street_core(a["streetAddress"]) != street_core(facts.address.street):
                    mism["streetAddress"] = {"site": a["streetAddress"], "facts": facts.address.street}
                if a.get("postalCode") and norm_postal(a["postalCode"]) != norm_postal(facts.address.postal_code):
                    mism["postalCode"] = {"site": a["postalCode"], "facts": facts.address.postal_code}
                if a.get("addressLocality") and norm_name(a["addressLocality"]) != norm_name(facts.address.city):
                    mism["addressLocality"] = {"site": a["addressLocality"], "facts": facts.address.city}
            if mism:
                out.append(
                    Finding(
                        check_id="nap.schema_mismatch",
                        severity=Severity.warning,
                        title=f"{url}: structured data disagrees with facts.yaml on {', '.join(mism)}",
                        evidence={"url": url, "mismatches": mism},
                        raw_ref=p.resp.raw_ref,
                        fix=SCHEMA_FIX,
                    )
                )

    # 3) Street address visible somewhere on the site.
    if facts.address:
        core = street_core(facts.address.street)
        if core and not any(core in street_core(p.visible_text) or core in _fold_ws(p.visible_text) for p in pages):
            out.append(
                Finding(
                    check_id="nap.address_not_on_site",
                    severity=Severity.info,
                    title=f"Street address '{facts.address.street}' not found in key pages' text",
                    evidence={"expected_core": core},
                )
            )
    return out


def _fold_ws(s: str) -> str:
    from geo.normalize import norm_street

    return norm_street(s)
