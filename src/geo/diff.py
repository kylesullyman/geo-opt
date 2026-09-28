"""Monitoring = diff(run_n, run_n-1). Trends = group-by over run timestamps.
Pure functions of stored Runs."""

from __future__ import annotations

from dataclasses import dataclass, field

from geo.models import Finding, Rate, Run, Severity, compute_rates


def finding_key(f: Finding) -> str:
    """Stable identity of a finding across runs (titles contain counts; don't use them)."""
    subj = f.evidence.get("url") or f.evidence.get("bot") or ""
    return f"{f.check_id}|{subj}"


@dataclass
class FindingDiff:
    new: list[Finding] = field(default_factory=list)
    resolved: list[Finding] = field(default_factory=list)
    persisting: list[Finding] = field(default_factory=list)
    severity_changed: list[tuple[Finding, Finding]] = field(default_factory=list)

    @property
    def regressions(self) -> list[Finding]:
        """New blockers/warnings, or findings that got more severe."""
        worse = [b for a, b in self.severity_changed if b.severity.rank < a.severity.rank]
        return [f for f in self.new if f.severity != Severity.info] + worse


def diff_findings(prev: Run | None, cur: Run) -> FindingDiff:
    a = {finding_key(f): f for f in (prev.findings if prev else [])}
    b = {finding_key(f): f for f in cur.findings}
    d = FindingDiff()
    for k, f in b.items():
        if k not in a:
            d.new.append(f)
        else:
            d.persisting.append(f)
            if a[k].severity != f.severity:
                d.severity_changed.append((a[k], f))
    d.resolved = [f for k, f in a.items() if k not in b]
    for lst in (d.new, d.resolved, d.persisting):
        lst.sort(key=lambda f: (f.severity.rank, f.check_id))
    return d


@dataclass
class RateChange:
    query_id: str
    query_text: str
    engine: str
    entity: str
    before: Rate | None
    after: Rate | None

    @property
    def delta(self) -> float | None:
        if not self.before or not self.after or self.before.mention_rate is None or self.after.mention_rate is None:
            return None
        return self.after.mention_rate - self.before.mention_rate


def diff_rates(prev: Run | None, cur: Run, entities: list[str]) -> list[RateChange]:
    a = {(r.query_id, r.engine, r.entity): r for r in compute_rates(prev.samples if prev else [], entities)}
    b = {(r.query_id, r.engine, r.entity): r for r in compute_rates(cur.samples, entities)}
    out = []
    for k in sorted(set(a) | set(b)):
        ref = b.get(k) or a[k]
        out.append(RateChange(k[0], ref.query_text, k[1], k[2], a.get(k), b.get(k)))
    return out


@dataclass
class TrendPoint:
    run_id: str
    at: str
    engine: str
    entity: str
    n: int
    mentioned: int
    cited: int

    @property
    def rate(self) -> float | None:
        return self.mentioned / self.n if self.n else None


def trend(runs: list[Run], entities: list[str]) -> list[TrendPoint]:
    """Aggregate mention rate per (run, engine, entity), oldest first. Pooled over queries."""
    pts: list[TrendPoint] = []
    for run in sorted(runs, key=lambda r: r.started_at):
        agg: dict[tuple[str, str], list[int]] = {}
        for r in compute_rates(run.samples, entities):
            v = agg.setdefault((r.engine, r.entity), [0, 0, 0])
            v[0] += r.n
            v[1] += r.mentioned
            v[2] += r.cited
        for (engine, entity), (n, m, c) in sorted(agg.items()):
            pts.append(TrendPoint(run.id, run.started_at.isoformat(), engine, entity, n, m, c))
    return pts
