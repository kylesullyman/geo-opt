"""geo: the CLI. Every caller (cron, CI, a future dashboard, a live sales demo)
goes through these commands; nothing here holds business logic of its own.

Exit codes:
    0  success
    1  runtime/config error
    2  usage error
    3  regression detected (geo diff --fail-on-regression / geo audit --fail-on-regression)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from geo import __version__
from geo.config import Client, ConfigError, clients_dir, geo_home, lint_client, load_client, scaffold_client
from geo.models import CitationSample, Run, RunKind, Severity, compute_rates, utcnow

app = typer.Typer(
    name="geo",
    help="AI search visibility audits for local businesses.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

EXIT_REGRESSION = 3

browser_app = typer.Typer(help="Capture and import actual ChatGPT web answers; no API key required.", no_args_is_help=True)
app.add_typer(browser_app, name="browser")


# ------------------------------------------------------------------------------ plumbing


class State:
    home: Path = Path.cwd()
    json_out: bool = False
    quiet: bool = False


state = State()


def _load_dotenv(path: Path) -> None:
    """Minimal .env support (KEY=VALUE lines). Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def data_dir() -> Path:
    return state.home / "data"


def open_store():
    from geo.store import Store

    return Store(data_dir())


def info(msg: str = "", **kw) -> None:
    if not state.quiet and not state.json_out:
        typer.echo(msg, **kw)


def err(msg: str) -> None:
    typer.echo(msg, err=True)


def emit_json(obj) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


def die(msg: str, code: int = 1) -> NoReturn:
    err(f"error: {msg}")
    raise typer.Exit(code)


def client_or_die(slug: str) -> Client:
    try:
        return load_client(slug, state.home)
    except ConfigError as e:
        die(str(e))


SEV_STYLE = {
    Severity.blocker: typer.colors.RED,
    Severity.warning: typer.colors.YELLOW,
    Severity.info: typer.colors.BLUE,
}


def _sev(s: Severity) -> str:
    return typer.style(f"{s.value:<7}", fg=SEV_STYLE[s], bold=s == Severity.blocker)


def _version_cb(v: bool) -> None:
    if v:
        typer.echo(f"geo {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    home: Annotated[
        Path | None,
        typer.Option("--home", envvar="GEO_HOME", help="Agency directory holding clients/ and data/. Default: cwd."),
    ] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output on stdout.")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="Only print errors and final paths.")] = False,
    version: Annotated[
        bool, typer.Option("--version", callback=_version_cb, is_eager=True, help="Show version and exit.")
    ] = False,
) -> None:
    state.home = (home or geo_home()).resolve()
    os.environ["GEO_HOME"] = str(state.home)
    state.json_out = json_out
    state.quiet = quiet
    _load_dotenv(state.home / ".env")


# ------------------------------------------------------------------------------ setup


@browser_app.command("prepare")
def browser_prepare(
    client: Annotated[str, typer.Argument()],
    samples: Annotated[int | None, typer.Option("--samples", "-n", min=1)] = None,
    queries: Annotated[list[str] | None, typer.Option("--query", "-q")] = None,
) -> None:
    """Create a local capture page with exact questions and sample slots."""
    from geo.citations.browser import prepare

    c = client_or_die(client)
    try:
        result = prepare(c, data_dir(), samples, queries)
    except (ValueError, OSError) as e:
        die(str(e))
    if state.json_out:
        emit_json(result)
    else:
        typer.echo(f"Capture page: {result['html']}")
        info(f"{result['samples']} samples planned. Open this page in your browser; follow its capture/import instructions.")


@browser_app.command("import")
def browser_import(
    client: Annotated[str, typer.Argument()],
    capture_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    allow_partial: Annotated[bool, typer.Option(help="Finalize an incomplete batch (cannot add samples after import).")] = False,
) -> None:
    """Validate and save a downloaded capture as an immutable citation run."""
    from geo.citations.browser import load_capture, make_run

    c = client_or_die(client)
    try:
        capture, plan, raw, plan_raw = load_capture(c, data_dir(), capture_file, allow_partial)
        store = open_store()
        run = make_run(c, store, capture, plan, raw, plan_raw)
    except (ValueError, OSError, KeyError) as e:
        die(str(e))
    _print_rates(run.samples, c)
    _finish(store, c, run, False, {})


@app.command("setup-api")
def setup_api(
    engine: Annotated[str, typer.Argument(help="openai, perplexity, or google_aio")] = "openai",
) -> None:
    """Optional future API setup: hidden key prompt, saved to GEO_HOME/.env."""
    import re
    import tempfile
    from geo.citations.engines import get_engine, EngineError

    if engine == "chatgpt_browser":
        die("browser capture needs no API key; use `geo browser prepare <client>`", 2)
    try:
        provider = get_engine(engine)
    except EngineError as e:
        die(str(e), 2)
    if state.json_out:
        die("setup-api requires an interactive hidden key prompt; omit --json", 2)
    key = typer.prompt(f"{provider.env_key} (stored locally, never printed)", hide_input=True).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
        die("invalid key characters; no changes made")
    env_path = state.home / ".env"
    if env_path.is_symlink():
        die(".env is a symlink; configure its target manually")
    old = env_path.read_text() if env_path.exists() else ""
    pattern = re.compile(r"^\s*(?:export\s+)?" + provider.env_key + r"\s*=.*$", re.M)
    line = provider.env_key + "=" + key
    updated = pattern.sub(lambda _m: line, old) if pattern.search(old) else old + ("\n" if old and not old.endswith("\n") else "") + line + "\n"
    with tempfile.NamedTemporaryFile(mode="w", dir=state.home, prefix=".geo-env-", delete=False) as f:
        f.write(updated)
        tmp = Path(f.name)
    tmp.replace(env_path)  # NamedTemporaryFile is private (0600).
    info(f"Saved {provider.env_key} to {env_path}. No API request was made.")
    info(f"Next: geo validate <client>; geo citations <client> -e {engine} --dry-run")
    if os.environ.get(provider.env_key):
        info("An existing environment variable overrides .env; unset it to use the saved key.")


@app.command()
def init(
    client: Annotated[str, typer.Argument(help="Client slug, e.g. smiles-dental")],
    name: Annotated[str, typer.Option(help="Business name as customers say it")],
    domain: Annotated[str, typer.Option(help="Primary domain, e.g. smilesdental.com")],
    city: Annotated[str, typer.Option(help="City used for query locality")] = "Orange",
) -> None:
    """Scaffold clients/<client>/ (client.yaml, facts.yaml, queries.yaml, proposed/)."""
    try:
        d = scaffold_client(client, name, domain, city, state.home)
    except ConfigError as e:
        die(str(e))
    if state.json_out:
        emit_json({"created": str(d)})
        return
    info(f"created {d}")
    info("next: fill facts.yaml from the intake call, write queries.yaml, then `geo validate " + client + "`")


@app.command("clients")
def list_clients() -> None:
    """List configured clients."""
    d = clients_dir(state.home)
    slugs = sorted(p.name for p in d.iterdir() if (p / "client.yaml").exists()) if d.exists() else []
    if state.json_out:
        emit_json(slugs)
        return
    if not slugs:
        info(f"no clients under {d}")
    for s in slugs:
        try:
            c = load_client(s, state.home)
            info(f"{s:<24} {c.config.name}  ({c.config.domain})  {len(c.queries.queries)} queries")
        except ConfigError as e:
            info(f"{s:<24} " + typer.style(f"INVALID: {str(e).splitlines()[0]}", fg=typer.colors.RED))


@app.command()
def validate(client: Annotated[str, typer.Argument()]) -> None:
    """Load and lint a client's config (the judgment layer)."""
    c = client_or_die(client)
    warnings = lint_client(c)
    engines = sorted({e for _q, _l, es, _n in c.queries.resolved() for e in es})
    from geo.citations.engines import get_engine

    keys = {}
    for e in engines:
        try:
            eng = get_engine(e)
            keys[e] = eng.available()
        except Exception as ex:
            warnings.append(str(ex))
    if state.json_out:
        emit_json({"client": client, "ok": True, "warnings": warnings, "engine_keys": keys})
        return
    info(f"{client}: config OK ({len(c.queries.queries)} queries, {len(c.config.competitors)} competitors)")
    for e, ok in keys.items():
        info(f"  engine {e:<11} " + ("key set" if ok else typer.style("key missing", fg=typer.colors.YELLOW)))
    for w in warnings:
        info(typer.style("  warn ", fg=typer.colors.YELLOW) + w)


@app.command("checks")
def list_checks() -> None:
    """List registered site checks."""
    from geo.checks import registry

    reg = registry()
    if state.json_out:
        emit_json([{"name": s.name, "version": s.version, "requires": s.requires, "description": s.description} for s in reg.values()])
        return
    for s in reg.values():
        req = f"  [needs --{s.requires[0].replace('_', '-')}]" if s.requires else ""
        info(f"{s.name:<14} {s.description}{req}")


# ------------------------------------------------------------------------------ runs


def _print_findings(findings) -> None:
    for f in sorted(findings, key=lambda f: (f.severity.rank, f.check_id)):
        info(f"  {_sev(f.severity)} {f.title}")
        if f.fix:
            info(typer.style(f"          fix: {f.fix.summary}", dim=True))


def _print_rates(samples: list[CitationSample], client: Client) -> None:
    rates = compute_rates(samples, client.entities())
    mine = [r for r in rates if r.entity == "client"]
    if not mine:
        return
    w = max(len(r.query_text) for r in mine)
    info(f"  {'query':<{w}}  {'engine':<11} mentioned  cited")
    for r in sorted(mine, key=lambda r: (r.query_id, r.engine)):
        lab = r.label
        color = typer.colors.RED if r.n and r.mentioned == 0 else None
        extra = f"  ({r.errors} failed)" if r.errors else ""
        empties = sum(1 for s in samples if s.query_id == r.query_id and s.engine == r.engine and s.empty_reason)
        if empties:
            extra += f"  ({empties} no AI Overview)"
        info(f"  {r.query_text:<{w}}  {r.engine:<11} {typer.style(f'{lab:<9}', fg=color)}  {r.cited}/{r.n}{extra}")


def _citation_phase(c: Client, fetcher, engines, queries, samples, concurrency) -> tuple[list[CitationSample], dict]:
    from geo.citations import plan, run_citations

    jobs = plan(c, engines=engines, queries=queries, samples=samples)
    if not jobs:
        info("  no citation jobs (queries.yaml empty or filtered out)")
        return [], {}
    done = [0]

    def tick(_s):
        done[0] += 1
        if not state.quiet and not state.json_out and sys.stderr.isatty():
            sys.stderr.write(f"\r  sampling {done[0]}/{len(jobs)}")
            sys.stderr.flush()

    out, skipped = run_citations(c, fetcher, engines=engines, queries=queries, samples=samples, concurrency=concurrency, on_sample=tick)
    if done[0] and sys.stderr.isatty() and not state.quiet and not state.json_out:
        sys.stderr.write("\n")
    for e, why in skipped.items():
        info(typer.style(f"  skipped engine {e}: {why}", fg=typer.colors.YELLOW))
    errs = [s for s in out if s.error]
    if errs:
        info(typer.style(f"  {len(errs)}/{len(out)} samples failed; first: {(errs[0].error or '')[:200]}", fg=typer.colors.YELLOW))
    return out, skipped


def _regressions(store, c: Client, run: Run):
    from geo.diff import diff_findings, diff_rates

    prior = [r for r in store.list_runs(c.slug, kind=run.kind) if r.id != run.id]
    prev = prior[0] if prior else None
    fd = diff_findings(prev, run) if prev and run.kind == RunKind.audit else None
    drops = []
    if prev:
        for ch in diff_rates(prev, run, ["client"]):
            if ch.delta is not None and ch.delta < 0:
                drops.append(ch)
    return prev, fd, drops


def _finish(store, c: Client, run: Run, fail_on_regression: bool, extra: dict) -> None:
    run.finished_at = utcnow()
    store.save_run(run)
    prev, fd, drops = _regressions(store, c, run)
    regressions = (fd.regressions if fd else []) + drops
    if state.json_out:
        emit_json(
            {
                "run_id": run.id,
                "client": c.slug,
                "kind": run.kind.value,
                "meta": run.meta,
                "findings": [f.model_dump() for f in run.findings],
                "rates": [r.model_dump() | {"mention_rate": r.mention_rate} for r in compute_rates(run.samples, c.entities())],
                "previous_run": prev.id if prev else None,
                "regressions": {
                    "findings": [f.model_dump() for f in (fd.regressions if fd else [])],
                    "rate_drops": [
                        {"query_id": d.query_id, "engine": d.engine, "before": d.before.label, "after": d.after.label}
                        for d in drops if d.before and d.after
                    ],
                },
                **extra,
            }
        )
    else:
        info("")
        info(f"run {typer.style(run.id, bold=True)} saved")
        if prev:
            if fd:
                info(f"  vs {prev.id}: {len(fd.new)} new, {len(fd.resolved)} resolved findings")
            for d in drops:
                if d.before and d.after:
                    info(typer.style(f"  rate drop: {d.query_text} [{d.engine}] {d.before.label} -> {d.after.label}", fg=typer.colors.RED))
    if run.meta.get("check_errors") or run.meta.get("citation_status") in ("unavailable", "partial", "failed"):
        raise typer.Exit(1)
    if fail_on_regression and regressions:
        raise typer.Exit(EXIT_REGRESSION)


def _citation_status(samples: list[CitationSample], skipped: dict) -> str:
    if not samples:
        return "unavailable"
    if all(s.error for s in samples):
        return "failed"
    if skipped or any(s.error for s in samples):
        return "partial"
    return "complete"


@app.command()
def audit(
    client: Annotated[str, typer.Argument()],
    citations: Annotated[bool, typer.Option("--citations/--no-citations", help="Also run the query tester.")] = True,
    only: Annotated[list[str] | None, typer.Option("--only", help="Run only these checks (repeatable).")] = None,
    skip: Annotated[list[str] | None, typer.Option("--skip", help="Skip these checks (repeatable).")] = None,
    probe_bots: Annotated[bool, typer.Option("--probe-bots", help="Fetch the homepage with AI-bot user-agents to detect WAF blocks (client-authorized audits only).")] = False,
    js: Annotated[bool, typer.Option("--js", help="Render pages with Playwright to confirm JS-dependence (needs `uv sync --extra js`).")] = False,
    engines: Annotated[list[str] | None, typer.Option("--engine", "-e", help="Limit citation engines (repeatable).")] = None,
    samples: Annotated[int | None, typer.Option("--samples", "-n", min=1, help="Override samples per (query, engine).")] = None,
    concurrency: Annotated[int, typer.Option(min=1, help="Parallel engine calls.")] = 4,
    ignore_robots: Annotated[bool, typer.Option("--ignore-robots", help="Do not apply robots.txt to our own crawler (client-authorized).")] = False,
    fail_on_regression: Annotated[bool, typer.Option("--fail-on-regression", help=f"Exit {EXIT_REGRESSION} if anything regressed vs the previous audit.")] = False,
) -> None:
    """Full run: site checks (+ citation tests). Writes one immutable Run."""
    from geo.checks import SiteContext, registry, run_checks
    from geo.fetch import Fetcher

    c = client_or_die(client)
    known = set(registry())
    for name in (only or []) + (skip or []):
        if name not in known:
            die(f"unknown check {name!r}; see `geo checks`", 2)
    store = open_store()
    run = Run(client=c.slug, kind=RunKind.audit, tool_version=__version__)
    run.meta["citation_status"] = "not_requested"
    info(f"auditing {c.config.name} ({c.config.base_url})  run {run.id}")
    with Fetcher(store, run.id, respect_robots=not ignore_robots) as fetcher:
        ctx = SiteContext(client=c, fetcher=fetcher, flags={"probe_bots": probe_bots, "js": js})
        res = run_checks(ctx, only=only, skip=skip)
        run.findings = res.findings
        run.meta.update({"checks_ran": res.ran, "checks_skipped": res.skipped, "check_errors": res.errors, "flags": ctx.flags})
        info(f"site checks: {len(res.ran)} ran, {len(res.findings)} findings")
        _print_findings(res.findings)
        for e in res.errors:
            info(typer.style(f"  check {e['check']} crashed: {e['error']}", fg=typer.colors.RED))
        if citations:
            info("citations:")
            out, skipped = _citation_phase(c, fetcher, engines, None, samples, concurrency)
            run.samples = out
            run.meta["engines_skipped"] = skipped
            run.meta["citation_status"] = _citation_status(out, skipped)
            info(f"  citation status: {run.meta['citation_status']}")
            _print_rates(out, c)
    _finish(store, c, run, fail_on_regression, {"check_errors": res.errors})
    if res.errors:
        raise typer.Exit(1)


@app.command("citations")
def citations_cmd(
    client: Annotated[str, typer.Argument()],
    engines: Annotated[list[str] | None, typer.Option("--engine", "-e", help="Limit engines (repeatable).")] = None,
    queries: Annotated[list[str] | None, typer.Option("--query", "-q", help="Limit to query ids (repeatable).")] = None,
    samples: Annotated[int | None, typer.Option("--samples", "-n", min=1, help="Override samples per (query, engine).")] = None,
    concurrency: Annotated[int, typer.Option(min=1)] = 4,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the call plan and exit.")] = False,
    fail_on_regression: Annotated[bool, typer.Option("--fail-on-regression")] = False,
) -> None:
    """Just the query tester: N samples per (query, engine), mention rates."""
    from geo.citations import plan
    from geo.fetch import Fetcher

    c = client_or_die(client)
    if dry_run:
        jobs = plan(c, engines=engines, queries=queries, samples=samples)
        if state.json_out:
            emit_json([j.__dict__ | {"locality": j.locality.model_dump()} for j in jobs])
            return
        by: dict[tuple[str, str], int] = {}
        for j in jobs:
            by[(j.query_text, j.engine)] = by.get((j.query_text, j.engine), 0) + 1
        for (q, e), n in sorted(by.items()):
            info(f"  {e:<11} x{n}  {q}")
        info(f"{len(jobs)} calls planned")
        return
    store = open_store()
    run = Run(client=c.slug, kind=RunKind.citations, tool_version=__version__)
    info(f"citation test: {c.config.name}  run {run.id}")
    with Fetcher(store, run.id) as fetcher:
        out, skipped = _citation_phase(c, fetcher, engines, queries, samples, concurrency)
    if not out:
        die("no samples collected; nothing saved (check API keys with `geo validate`)")
    run.samples = out
    run.meta["engines_skipped"] = skipped
    run.meta["citation_status"] = _citation_status(out, skipped)
    _print_rates(out, c)
    _finish(store, c, run, fail_on_regression, {"engines_skipped": skipped})


# ------------------------------------------------------------------------------ history


@app.command()
def runs(
    client: Annotated[str, typer.Argument()],
    last: Annotated[int, typer.Option("--last", "-n", min=1)] = 20,
) -> None:
    """List stored runs, newest first."""
    store = open_store()
    rs = store.list_runs(client, limit=last)
    if state.json_out:
        emit_json([
            {"id": r.id, "kind": r.kind.value, "started_at": r.started_at, "findings": len(r.findings), "samples": len(r.samples)}
            for r in rs
        ])
        return
    if not rs:
        info(f"no runs for {client}")
    for r in rs:
        sev = {s: sum(1 for f in r.findings if f.severity == s) for s in Severity}
        fs = f"{sev[Severity.blocker]}B {sev[Severity.warning]}W {sev[Severity.info]}I" if r.kind == RunKind.audit else ""
        info(f"{r.id}  {r.kind.value:<9} {r.started_at:%Y-%m-%d %H:%M}  {fs:<12} {len(r.samples)} samples")


@app.command()
def show(
    run_id: Annotated[str, typer.Argument(help="Run id (or unique prefix)")],
    evidence: Annotated[bool, typer.Option("--evidence", help="Print finding evidence.")] = False,
    answers: Annotated[bool, typer.Option("--answers", help="Print each sample's answer text.")] = False,
) -> None:
    """Show one run's findings and citation rates."""
    store = open_store()
    try:
        run = store.get_run(run_id)
    except KeyError as e:
        die(str(e))
    if state.json_out:
        emit_json(run.model_dump())
        return
    info(f"run {run.id}  {run.kind.value}  client={run.client}  {run.started_at:%Y-%m-%d %H:%M} UTC  geo {run.tool_version}")
    for f in sorted(run.findings, key=lambda f: (f.severity.rank, f.check_id)):
        info(f"  {_sev(f.severity)} {f.title}  " + typer.style(f"[{f.check_id}]", dim=True))
        if evidence and f.evidence:
            for line in json.dumps(f.evidence, indent=2, default=str).splitlines():
                info("            " + line)
    if run.samples:
        try:
            c = load_client(run.client, state.home)
            _print_rates(run.samples, c)
        except ConfigError:
            pass
        if answers:
            for s in run.samples:
                m = s.mention_for("client")
                flag = "MENTIONED" if m and m.mentioned else "-"
                info(f"\n--- {s.engine} #{s.sample_idx} [{s.query_text}] {flag} raw={s.raw_ref and s.raw_ref[:12]}")
                info(s.error or s.empty_reason or s.answer_text.strip()[:2000])


@app.command()
def diff(
    client: Annotated[str, typer.Argument()],
    run_a: Annotated[str | None, typer.Argument(help="Older run (default: second newest)")] = None,
    run_b: Annotated[str | None, typer.Argument(help="Newer run (default: newest)")] = None,
    kind: Annotated[RunKind, typer.Option(help="Which run kind to compare by default.")] = RunKind.audit,
    fail_on_regression: Annotated[bool, typer.Option("--fail-on-regression", help=f"Exit {EXIT_REGRESSION} on regressions (for cron alerts).")] = False,
) -> None:
    """Compare two runs: findings new/resolved, mention-rate changes."""
    from geo.diff import diff_findings, diff_rates

    store = open_store()
    if run_b or run_a:
        try:
            a = store.get_run(run_a) if run_a else None
            b = store.get_run(run_b) if run_b else store.list_runs(client, kind=kind, limit=1)[0]
        except (KeyError, IndexError) as e:
            die(str(e))
    else:
        rs = store.list_runs(client, kind=kind, limit=2) if kind == RunKind.audit else store.runs_with_samples(client, limit=2)
        if len(rs) < 2:
            die(f"need two {kind.value} runs to diff; have {len(rs)}")
        b, a = rs[0], rs[1]
    c = client_or_die(client)
    fd = diff_findings(a, b)
    rd = [ch for ch in diff_rates(a, b, c.entities()) if ch.entity == "client"]
    drops = [ch for ch in rd if ch.delta is not None and ch.delta < 0]
    if state.json_out:
        emit_json({
            "from": a.id if a else None, "to": b.id,
            "new": [f.model_dump() for f in fd.new], "resolved": [f.model_dump() for f in fd.resolved],
            "regressions": [f.model_dump() for f in fd.regressions],
            "rates": [{"query_id": ch.query_id, "engine": ch.engine, "before": ch.before.label if ch.before else None,
                       "after": ch.after.label if ch.after else None, "delta": ch.delta} for ch in rd],
        })
    else:
        info(f"{a.id if a else '(none)'} -> {b.id}")
        if fd.resolved:
            info(typer.style(f"resolved ({len(fd.resolved)}):", fg=typer.colors.GREEN))
            _print_findings(fd.resolved)
        if fd.new:
            info(typer.style(f"new ({len(fd.new)}):", fg=typer.colors.RED))
            _print_findings(fd.new)
        for x, y in fd.severity_changed:
            info(f"  severity {x.severity.value} -> {y.severity.value}: {y.title}")
        for ch in rd:
            if ch.before and ch.after and ch.before.label != ch.after.label:
                arrow = typer.style("down", fg=typer.colors.RED) if (ch.delta or 0) < 0 else typer.style("up", fg=typer.colors.GREEN)
                info(f"  {ch.query_text} [{ch.engine}] {ch.before.label} -> {ch.after.label} {arrow}")
        if not (fd.new or fd.resolved or fd.severity_changed or any(ch.delta for ch in rd)):
            info("no changes")
    if fail_on_regression and (fd.regressions or drops):
        raise typer.Exit(EXIT_REGRESSION)


@app.command()
def trend(
    client: Annotated[str, typer.Argument()],
    last: Annotated[int, typer.Option("--last", "-n", min=1)] = 12,
) -> None:
    """Mention rate per run (pooled over queries), oldest first. The retainer's time series."""
    from geo.diff import trend as trend_fn

    c = client_or_die(client)
    store = open_store()
    rs = store.runs_with_samples(client, limit=last)
    pts = trend_fn(rs, ["client"])
    if state.json_out:
        emit_json([p.__dict__ | {"rate": p.rate} for p in pts])
        return
    if not pts:
        info(f"no citation data for {client}")
        return
    info(f"{c.config.name}: mention rate across {len(c.queries.queries)} tracked queries")
    for p in pts:
        bar = "#" * round((p.rate or 0) * 30)
        info(f"  {p.at[:16].replace('T', ' ')}  {p.engine:<11} {p.mentioned:>3}/{p.n:<3} {(p.rate or 0) * 100:5.1f}%  {bar}")


# ------------------------------------------------------------------------------ outputs


@app.command()
def report(
    client: Annotated[str, typer.Argument()],
    last: Annotated[int, typer.Option("--last", "-n", min=1, help="Runs of history to include (diff + trend).")] = 3,
    run_id: Annotated[str | None, typer.Option("--run", help="Report a specific audit run instead of the newest.")] = None,
    pdf: Annotated[bool, typer.Option("--pdf/--no-pdf", help="Also render PDF with headless Chrome.")] = False,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Output directory (default: data/reports/<client>/).")] = None,
) -> None:
    """Render an HTML (and optionally PDF) report from stored runs. No network."""
    from geo.report import html_to_pdf, render_html

    c = client_or_die(client)
    store = open_store()
    audits = store.runs_with_findings(client, limit=last + 1)
    if run_id:
        try:
            pinned = store.get_run(run_id)
        except KeyError as e:
            die(str(e))
        audits = [pinned] + [r for r in audits if r.started_at < pinned.started_at][:last]
    cit = store.runs_with_samples(client, limit=max(last, 12))
    if not audits and not cit:
        die(f"no runs stored for {client}; run `geo audit {client}` first")
    html = render_html(c, audits, cit)
    d = out or (data_dir() / "reports" / client)
    d.mkdir(parents=True, exist_ok=True)
    stem = (audits[0].id if audits else cit[0].id)
    hp = d / f"{client}-{stem}.html"
    hp.write_text(html)
    paths = {"html": str(hp)}
    if pdf:
        pp = hp.with_suffix(".pdf")
        try:
            html_to_pdf(hp, pp)
            paths["pdf"] = str(pp)
        except Exception as e:
            die(f"PDF render failed: {e}")
    if state.json_out:
        emit_json(paths)
    else:
        for v in paths.values():
            typer.echo(v)


@app.command()
def fix(
    client: Annotated[str, typer.Argument()],
    kinds: Annotated[list[str] | None, typer.Argument(help="robots | schema | faq (default: all)")] = None,
    offline: Annotated[bool, typer.Option("--offline", help="Use robots.txt from the latest stored audit instead of fetching.")] = False,
) -> None:
    """Generate proposed fixes into clients/<client>/proposed/. Never touches the live site."""
    from geo.fixes import GENERATORS, stage

    c = client_or_die(client)
    kinds = kinds or ["robots", "schema", "faq"]
    for k in kinds:
        if k not in GENERATORS:
            die(f"unknown fix {k!r}; choose from {', '.join(GENERATORS)}", 2)
    results = []
    for k in kinds:
        if k == "robots":
            current, sitemap = _current_robots(c, offline)
            art = GENERATORS[k](c, current, sitemap)
        else:
            art = GENERATORS[k](c)
        p = stage(c, art)
        extra = None
        if k == "schema" and art.validation and art.validation.get("html_snippet"):
            extra = p.with_suffix(".html")
            extra.write_text(art.validation.pop("html_snippet"))
        results.append({"kind": k, "path": str(p), "ready": art.ready, "needs_input": art.needs_input,
                        "validation": art.validation, "notes": art.notes, "extra": str(extra) if extra else None})
    if state.json_out:
        emit_json(results)
        return
    for r in results:
        status = typer.style("ready for review", fg=typer.colors.GREEN) if r["ready"] else typer.style("NOT READY", fg=typer.colors.RED)
        info(f"{r['kind']:<7} {r['path']}  {status}")
        if r["needs_input"]:
            info(f"        [NEEDS INPUT] {', '.join(r['needs_input'])} (fill facts.yaml)")
        v = r["validation"] or {}
        for e in v.get("errors") or v.get("failures") or []:
            info(typer.style(f"        invalid: {e}", fg=typer.colors.RED))
        for w in (v.get("warnings") or [])[:5]:
            info(typer.style(f"        warn: {w}", fg=typer.colors.YELLOW))
        for n in r["notes"]:
            info(typer.style(f"        {n}", dim=True))
    info("\nNothing was published. Review the files in proposed/ and apply them by hand.")


def _current_robots(c: Client, offline: bool) -> tuple[str | None, str | None]:
    store = open_store()
    robots_url = c.config.base_url + "/robots.txt"
    if offline:
        for r in store.runs_with_findings(c.slug, limit=5):
            for a in store.artifacts(r.id):
                if a["url"].rstrip("/") == robots_url and a["status"] == 200 and a["raw_ref"]:
                    return store.blobs.get(a["raw_ref"]).decode("utf-8", "replace"), None
        info("  no stored robots.txt; generating from scratch")
        return None, None
    from geo.fetch import Fetcher
    from geo.models import new_run_id

    # Fix generation fetches are logged under a pseudo run id so raw bodies are still retained.
    with Fetcher(store, "fix-" + new_run_id()) as f:
        r = f.get(robots_url, _skip_robots=True)
        sm = None
        for cand in ("/sitemap.xml", "/sitemap_index.xml"):
            s = f.get(c.config.base_url + cand, _skip_robots=True)
            head = s.body[:2000]
            if s.ok and (b"<urlset" in head or b"<sitemapindex" in head):
                sm = c.config.base_url + cand
                break
    return (r.text if r.ok else None), sm


# ------------------------------------------------------------------------------ raw-first tooling


@app.command()
def reparse(
    client: Annotated[str, typer.Argument()],
    run_id: Annotated[str | None, typer.Option("--run", help="Only this run (default: all runs with samples).")] = None,
) -> None:
    """Re-derive citation mentions from stored raw completions with the current parser.
    Appends new derivations; original rows are never modified."""
    from geo.citations import PARSER_VERSION, reparse_run

    c = client_or_die(client)
    store = open_store()
    targets = [store.get_run(run_id)] if run_id else store.runs_with_samples(client)
    total = 0
    for r in targets:
        n, skipped = reparse_run(store, c, r.id)
        total += n
        info(f"{r.id}: {n} re-derived, {skipped} already current/unparseable")
    if state.json_out:
        emit_json({"parser_version": PARSER_VERSION, "rederived": total})
    else:
        info(f"parser {PARSER_VERSION}: {total} samples re-derived")


@app.command()
def raw(
    ref: Annotated[str, typer.Argument(help="sha256 (or unique prefix) of a stored artifact")],
    meta: Annotated[bool, typer.Option("--meta", help="Show where this artifact came from instead of its bytes.")] = False,
) -> None:
    """Print a stored raw artifact verbatim (proof of what an engine or site returned)."""
    store = open_store()
    root = store.blobs.root
    if len(ref) < 64:
        matches = [p.name for p in root.glob(ref + "*") if len(p.name) == 64]
        if len(matches) != 1:
            die(f"prefix {ref!r} matches {len(matches)} blobs")
        ref = matches[0]
    if meta:
        emit_json({"sha256": ref, "artifacts": store.artifacts_for_ref(ref)})
        return
    try:
        sys.stdout.buffer.write(store.blobs.get(ref))
    except (KeyError, ValueError) as e:
        die(str(e))


def run() -> None:
    app()


if __name__ == "__main__":
    run()
