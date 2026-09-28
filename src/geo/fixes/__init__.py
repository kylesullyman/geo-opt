"""Fix generators. Each produces a proposed artifact under clients/<slug>/proposed/.

There is no write path to a client's site anywhere in this codebase. Output is
always a file staged for human review.

Generators may only use facts.yaml (and the site's own current robots.txt).
A missing required fact is emitted as [NEEDS INPUT] and the artifact is marked
not-ready; it is never guessed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from geo.config import Client

NEEDS_INPUT = "[NEEDS INPUT]"


@dataclass
class Artifact:
    filename: str
    content: str
    ready: bool  # False if any required fact is missing or validation failed
    needs_input: list[str] = field(default_factory=list)
    validation: dict | None = None
    notes: list[str] = field(default_factory=list)


GenFn = Callable[..., Artifact]
GENERATORS: dict[str, GenFn] = {}


def generator(name: str) -> Callable[[GenFn], GenFn]:
    def deco(fn: GenFn) -> GenFn:
        GENERATORS[name] = fn
        return fn

    return deco


def stage(client: Client, art: Artifact) -> Path:
    d = client.proposed_dir
    d.mkdir(parents=True, exist_ok=True)
    p = d / art.filename
    p.write_text(art.content)
    return p


from geo.fixes import faq, robots, schema  # noqa: E402,F401  (self-register)
