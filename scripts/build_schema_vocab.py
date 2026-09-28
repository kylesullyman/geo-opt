"""Compact the official schema.org vocabulary into src/geo/data/schemaorg_vocab.json.

Usage:
    curl -sSL -o /tmp/schemaorg.jsonld https://schema.org/version/latest/schemaorg-current-https.jsonld
    uv run python scripts/build_schema_vocab.py /tmp/schemaorg.jsonld
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _ids(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, dict):
        v = [v]
    return [x["@id"].removeprefix("schema:") for x in v if isinstance(x, dict) and "@id" in x]


def _types(v) -> list[str]:
    if isinstance(v, str):
        v = [v]
    return list(v or [])


def main(src: str) -> None:
    graph = json.loads(Path(src).read_text())["@graph"]
    types: dict[str, list[str]] = {}
    props: dict[str, dict[str, list[str]]] = {}
    enum_members: dict[str, list[str]] = {}
    for node in graph:
        nid = node["@id"]
        if not nid.startswith("schema:"):
            continue
        name = nid.removeprefix("schema:")
        t = _types(node.get("@type"))
        if "rdfs:Class" in t:
            types[name] = _ids(node.get("rdfs:subClassOf"))
        elif "rdf:Property" in t:
            props[name] = {
                "domain": _ids(node.get("schema:domainIncludes")),
                "range": _ids(node.get("schema:rangeIncludes")),
            }
        else:
            members = [x.removeprefix("schema:") for x in t if x.startswith("schema:")]
            if members:
                enum_members[name] = members
    out = {"types": types, "properties": props, "enum_members": enum_members}
    dest = Path(__file__).resolve().parent.parent / "src/geo/data/schemaorg_vocab.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, separators=(",", ":"), sort_keys=True))
    print(f"wrote {dest} ({len(types)} types, {len(props)} properties, {len(enum_members)} members)")


if __name__ == "__main__":
    main(sys.argv[1])
