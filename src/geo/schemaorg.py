"""Programmatic schema.org validation for JSON-LD, against the vendored official
vocabulary (src/geo/data/schemaorg_vocab.json; rebuild with scripts/build_schema_vocab.py).

Used both to audit a client's existing markup and to gate generated markup
before a human reads it: models invent plausible-looking properties constantly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

VALIDATOR_VERSION = "schemaorg-1"

# Google rich-result requirements for LocalBusiness (required / recommended).
LOCALBUSINESS_REQUIRED = ("name", "address")
LOCALBUSINESS_RECOMMENDED = ("telephone", "url", "openingHoursSpecification", "geo", "image", "priceRange")
POSTAL_REQUIRED = ("streetAddress", "addressLocality", "addressRegion", "postalCode")
FAQ_REQUIRED = ("mainEntity",)

DATATYPES = {"Text", "URL", "Number", "Integer", "Float", "Boolean", "Date", "DateTime", "Time", "DataType", "CssSelectorType", "XPathType", "PronounceableText"}


@dataclass
class Vocab:
    types: dict[str, list[str]]
    properties: dict[str, dict[str, list[str]]]
    enum_members: dict[str, list[str]]

    @cache  # noqa: B019 - vocab is a process-wide singleton
    def ancestors(self, t: str) -> frozenset[str]:
        seen: set[str] = set()
        stack = [t]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(self.types.get(cur, []))
        return frozenset(seen)

    def is_type(self, t: str) -> bool:
        return t in self.types

    def is_a(self, t: str, parent: str) -> bool:
        return parent in self.ancestors(t)

    def prop_allowed_on(self, prop: str, t: str) -> bool:
        spec = self.properties.get(prop)
        if spec is None:
            return False
        anc = self.ancestors(t)
        return any(d in anc for d in spec["domain"])

    @property
    def enum_classes(self) -> frozenset[str]:
        if not hasattr(self, "_enum_classes"):
            self._enum_classes = frozenset(c for cs in self.enum_members.values() for c in cs)
        return self._enum_classes

    def __hash__(self) -> int:
        return id(self)


@cache
def vocab() -> Vocab:
    raw = json.loads(resources.files("geo.data").joinpath("schemaorg_vocab.json").read_text())
    return Vocab(types=raw["types"], properties=raw["properties"], enum_members=raw["enum_members"])


def _strip_ns(v: str) -> str:
    for p in ("https://schema.org/", "http://schema.org/", "schema:"):
        if v.startswith(p):
            return v[len(p):]
    return v


def _types_of(node: dict[str, Any]) -> list[str]:
    t = node.get("@type")
    if t is None:
        return []
    ts = t if isinstance(t, list) else [t]
    return [_strip_ns(str(x)) for x in ts]


@dataclass
class Issue:
    level: str  # error | warning
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.path}: {self.message}"


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    types_found: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "types_found": self.types_found,
            "errors": [str(i) for i in self.errors],
            "warnings": [str(i) for i in self.warnings],
        }


def top_nodes(doc: Any) -> list[dict[str, Any]]:
    """Flatten a JSON-LD document (object, array, or @graph) into top-level nodes."""
    out: list[dict[str, Any]] = []
    items = doc if isinstance(doc, list) else [doc]
    for it in items:
        if not isinstance(it, dict):
            continue
        if "@graph" in it and isinstance(it["@graph"], list):
            out.extend(n for n in it["@graph"] if isinstance(n, dict))
        else:
            out.append(it)
    return out


def _check_context(doc: Any, rep: Report) -> None:
    items = doc if isinstance(doc, list) else [doc]
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            rep.issues.append(Issue("error", f"$[{i}]", "top-level item is not an object"))
            continue
        ctx = it.get("@context")
        ctx_s = json.dumps(ctx) if ctx is not None else ""
        if "schema.org" not in ctx_s:
            rep.issues.append(Issue("error", f"$[{i}].@context", f"@context is not schema.org ({ctx!r})"))


def _validate_node(node: dict[str, Any], path: str, rep: Report, v: Vocab, depth: int = 0) -> None:
    if depth > 12:
        return
    types = _types_of(node)
    if not types:
        if depth == 0:
            rep.issues.append(Issue("error", path, "node has no @type"))
        return
    known = [t for t in types if v.is_type(t)]
    for t in types:
        if not v.is_type(t):
            rep.issues.append(Issue("error", f"{path}.@type", f"unknown schema.org type {t!r}"))
    rep.types_found.extend(known)
    if not known:
        return
    for prop, val in node.items():
        if prop.startswith("@"):
            continue
        p = _strip_ns(prop)
        # schema.org Potential Actions annotations: "<property>-input" / "<property>-output"
        # (e.g. SearchAction "query-input"). Valid when the base property exists.
        for suffix in ("-input", "-output"):
            if p.endswith(suffix) and p[: -len(suffix)] in v.properties:
                break
        else:
            suffix = None
        if suffix:
            continue
        if p not in v.properties:
            rep.issues.append(Issue("error", f"{path}.{prop}", f"unknown schema.org property {p!r}"))
            continue
        if not any(v.prop_allowed_on(p, t) for t in known):
            rep.issues.append(
                Issue("error", f"{path}.{prop}", f"property {p!r} is not defined for type {'/'.join(known)}")
            )
            continue
        rng = v.properties[p]["range"]
        vals = val if isinstance(val, list) else [val]
        for j, item in enumerate(vals):
            ipath = f"{path}.{prop}" + (f"[{j}]" if isinstance(val, list) else "")
            if isinstance(item, dict):
                if "@id" in item and len(item) == 1:
                    continue  # reference
                itypes = _types_of(item)
                if itypes and rng and not any(
                    it_ in DATATYPES or any(v.is_a(it_, r) for r in rng) for it_ in itypes if v.is_type(it_)
                ):
                    if all(v.is_type(it_) for it_ in itypes):
                        rep.issues.append(
                            Issue(
                                "warning",
                                ipath,
                                f"{'/'.join(itypes)} is not an expected type for {p} (expects {', '.join(rng)})",
                            )
                        )
                _validate_node(item, ipath, rep, v, depth + 1)
            elif isinstance(item, str) and rng and not (set(rng) & DATATYPES):
                # Range is only entity types; a string is tolerated by Google for many
                # (e.g. address), but enumerations must be valid members.
                enum_ranges = [r for r in rng if r in v.enum_classes]
                if enum_ranges:
                    member = _strip_ns(item)
                    if not any(r in v.enum_members.get(member, []) for r in enum_ranges):
                        rep.issues.append(Issue("error", ipath, f"{item!r} is not a valid {'/'.join(enum_ranges)}"))


def _rich_result_rules(node: dict[str, Any], path: str, rep: Report, v: Vocab) -> None:
    types = [t for t in _types_of(node) if v.is_type(t)]
    if any(v.is_a(t, "LocalBusiness") for t in types):
        for p in LOCALBUSINESS_REQUIRED:
            if not node.get(p):
                rep.issues.append(Issue("error", f"{path}.{p}", f"LocalBusiness requires {p}"))
        for p in LOCALBUSINESS_RECOMMENDED:
            if not node.get(p):
                rep.issues.append(Issue("warning", f"{path}.{p}", f"LocalBusiness should have {p}"))
        addr = node.get("address")
        if isinstance(addr, dict):
            for p in POSTAL_REQUIRED:
                if not addr.get(p):
                    rep.issues.append(Issue("warning", f"{path}.address.{p}", f"PostalAddress should have {p}"))
        elif isinstance(addr, str):
            rep.issues.append(Issue("warning", f"{path}.address", "address is a plain string; use PostalAddress"))
    if "FAQPage" in types:
        me = node.get("mainEntity")
        qs = me if isinstance(me, list) else [me] if me else []
        if not qs:
            rep.issues.append(Issue("error", f"{path}.mainEntity", "FAQPage requires mainEntity Questions"))
        for i, q in enumerate(qs):
            if not isinstance(q, dict) or "Question" not in _types_of(q):
                rep.issues.append(Issue("error", f"{path}.mainEntity[{i}]", "must be a Question"))
                continue
            if not q.get("name"):
                rep.issues.append(Issue("error", f"{path}.mainEntity[{i}].name", "Question requires name"))
            ans = q.get("acceptedAnswer")
            if not isinstance(ans, dict) or not ans.get("text"):
                rep.issues.append(
                    Issue("error", f"{path}.mainEntity[{i}].acceptedAnswer", "Question requires acceptedAnswer.text")
                )


def validate(doc: Any) -> Report:
    v = vocab()
    rep = Report()
    _check_context(doc, rep)
    for i, node in enumerate(top_nodes(doc)):
        path = f"$[{i}]"
        _validate_node(node, path, rep, v)
        _rich_result_rules(node, path, rep, v)
    return rep


def is_business_type(t: str) -> bool:
    v = vocab()
    return v.is_type(t) and (v.is_a(t, "LocalBusiness") or v.is_a(t, "Organization"))
