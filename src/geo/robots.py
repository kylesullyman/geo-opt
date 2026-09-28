"""RFC 9309 robots.txt parser.

Deliberately not urllib.robotparser: that uses first-match and substring UA
semantics, which gives wrong answers for exactly the files we care about.

Semantics implemented:
- A crawler obeys the group(s) whose user-agent line equals its product token
  (case-insensitive). Multiple matching groups are merged. Otherwise the '*'
  group. Otherwise everything is allowed.
- Within a group the longest matching path pattern wins; on a tie, Allow wins.
- '*' wildcard and '$' end anchor in patterns. Percent-encoding normalized.
- /robots.txt itself is always allowed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

PARSER_VERSION = "robots-1"


@dataclass
class Rule:
    allow: bool
    pattern: str
    line: int

    def regex(self) -> re.Pattern[str]:
        p = _norm(self.pattern)
        anchored = p.endswith("$")
        if anchored:
            p = p[:-1]
        body = ".*".join(re.escape(part) for part in p.split("*"))
        return re.compile("^" + body + ("$" if anchored else ""))

    @property
    def specificity(self) -> int:
        return len(_norm(self.pattern))


@dataclass
class RobotsGroup:
    agents: list[str]
    rules: list[Rule] = field(default_factory=list)
    start_line: int = 0


@dataclass
class Verdict:
    allowed: bool
    matched_group: str  # "explicit" | "*" | "none"
    rule: Rule | None

    def as_evidence(self) -> dict:
        return {
            "allowed": self.allowed,
            "matched_group": self.matched_group,
            "rule": (
                f"{'Allow' if self.rule.allow else 'Disallow'}: {self.rule.pattern} (line {self.rule.line})"
                if self.rule
                else None
            ),
        }


def _norm(path: str) -> str:
    # Decode then re-encode is overkill; decoding unreserved chars is enough for matching.
    try:
        return unquote(path)
    except Exception:
        return path


def _agent_token(value: str) -> str:
    # "ChatGPT-User/1.0" -> "chatgpt-user"
    return re.split(r"[/\s]", value.strip(), maxsplit=1)[0].lower()


@dataclass
class Robots:
    groups: list[RobotsGroup]
    sitemaps: list[str]
    errors: list[str]

    @classmethod
    def parse(cls, text: str) -> Robots:
        groups: list[RobotsGroup] = []
        sitemaps: list[str] = []
        errors: list[str] = []
        current: RobotsGroup | None = None
        last_was_agent = False
        if text.startswith("\ufeff"):
            text = text[1:]
        for n, raw in enumerate(text.splitlines(), start=1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" not in line:
                errors.append(f"line {n}: no ':' separator: {raw.strip()!r}")
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower().replace("_", "-")
            value = value.strip()
            if key in ("user-agent", "useragent"):
                if current is None or not last_was_agent:
                    current = RobotsGroup(agents=[], start_line=n)
                    groups.append(current)
                current.agents.append(value)
                last_was_agent = True
            elif key in ("allow", "disallow"):
                last_was_agent = False
                if current is None:
                    errors.append(f"line {n}: {key} before any user-agent")
                    continue
                if key == "disallow" and value == "":
                    continue  # empty Disallow means allow everything; no rule
                if not value.startswith("/") and not value.startswith("*"):
                    value = "/" + value
                current.rules.append(Rule(allow=(key == "allow"), pattern=value, line=n))
            elif key == "sitemap":
                sitemaps.append(value)
            else:
                last_was_agent = False
                # crawl-delay, host, etc.: ignored by RFC 9309
        return cls(groups=groups, sitemaps=sitemaps, errors=errors)

    def rules_for(self, token: str) -> tuple[list[Rule], str]:
        t = token.lower()
        explicit = [g for g in self.groups if any(_agent_token(a) == t for a in g.agents)]
        if explicit:
            return [r for g in explicit for r in g.rules], "explicit"
        star = [g for g in self.groups if any(a.strip() == "*" for a in g.agents)]
        if star:
            return [r for g in star for r in g.rules], "*"
        return [], "none"

    def verdict(self, token: str, url_or_path: str) -> Verdict:
        parts = urlsplit(url_or_path)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        if path == "/robots.txt":
            return Verdict(True, "none", None)
        rules, which = self.rules_for(token)
        best: Rule | None = None
        target = _norm(path)
        for r in rules:
            if r.regex().match(target):
                if (
                    best is None
                    or r.specificity > best.specificity
                    or (r.specificity == best.specificity and r.allow and not best.allow)
                ):
                    best = r
        return Verdict(allowed=best.allow if best else True, matched_group=which, rule=best)

    def allowed(self, token: str, url_or_path: str) -> bool:
        return self.verdict(token, url_or_path).allowed


def render(groups: list[RobotsGroup], sitemaps: list[str], header: list[str] | None = None) -> str:
    out: list[str] = [f"# {h}" if h else "#" for h in (header or [])]
    if out:
        out.append("")
    for g in groups:
        for a in g.agents:
            out.append(f"User-agent: {a}")
        if not g.rules:
            out.append("Disallow:")
        for r in g.rules:
            out.append(f"{'Allow' if r.allow else 'Disallow'}: {r.pattern}")
        out.append("")
    for s in sitemaps:
        out.append(f"Sitemap: {s}")
    return "\n".join(out).rstrip() + "\n"
