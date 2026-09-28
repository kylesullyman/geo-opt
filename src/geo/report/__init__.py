"""Reports are a pure function of stored Runs: Jinja -> HTML, headless Chrome -> PDF."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from jinja2 import Environment, PackageLoader

from geo import __version__
from geo.citations.engines import ENGINES
from geo.config import Client
from geo.diff import diff_findings, trend
from geo.models import Run, Severity, compute_rates


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("geo", "report/templates"),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["pct"] = lambda x: "n/a" if x is None else f"{round(x * 100)}%"
    return env


def build_context(client: Client, audits: list[Run], citation_runs: list[Run]) -> dict:
    """audits / citation_runs: newest first."""
    latest = audits[0] if audits else None
    prev = audits[1] if len(audits) > 1 else None
    entities = client.entities()
    latest_c = citation_runs[0] if citation_runs else None
    if latest_c and latest_c.meta.get("profile_snapshot"):
        from geo.config import ClientConfig
        config = ClientConfig(**latest_c.meta["profile_snapshot"]["config"])
        entities = ["client"] + [c.name for c in config.competitors]
    rates = compute_rates(latest_c.samples, entities) if latest_c else []
    rows: dict[tuple[str, str], dict] = {}
    for r in rates:
        row = rows.setdefault((r.query_id, r.engine), {"query_id": r.query_id, "query": r.query_text, "engine": r.engine, "by": {}})
        row["by"][r.entity] = r
    empty = {}
    if latest_c:
        for s in latest_c.samples:
            if s.empty_reason:
                k = (s.query_id, s.engine)
                empty[k] = empty.get(k, 0) + 1
    for k, row in rows.items():
        row["empty"] = empty.get(k, 0)
    findings = sorted(latest.findings, key=lambda f: (f.severity.rank, f.check_id)) if latest else []
    counts = {s.value: sum(1 for f in findings if f.severity == s) for s in Severity}
    engines_used = sorted({s.engine for s in latest_c.samples}) if latest_c else []
    return {
        "client": client,
        "latest": latest,
        "latest_citations": latest_c,
        "findings": findings,
        "counts": counts,
        "diff": diff_findings(prev, latest) if latest and prev else None,
        "rate_rows": sorted(rows.values(), key=lambda r: (r["query_id"], r["engine"])),
        "entities": entities,
        "surfaces": {e: ENGINES[e].surface for e in engines_used if e in ENGINES},
        "trend": trend(list(reversed(citation_runs)), ["client"]) if len(citation_runs) > 1 else [],
        "n_citation_runs": len(citation_runs),
        "tool_version": __version__,
        "locality": latest_c.samples[0].locality if latest_c and latest_c.samples else None,
        "browser_capture": latest_c.meta if latest_c and latest_c.meta.get("source") == "chatgpt_browser" else None,
    }


def render_html(client: Client, audits: list[Run], citation_runs: list[Run]) -> str:
    return _env().get_template("report.html.j2").render(**build_context(client, audits, citation_runs))


def find_chrome() -> str | None:
    cands = [
        os.environ.get("GEO_CHROME"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome"),
    ]
    return next((c for c in cands if c and Path(c).exists()), None)


def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("headless Chrome not found; set GEO_CHROME to its path")
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    if not pdf_path.exists():
        raise RuntimeError("Chrome exited without writing the PDF")
