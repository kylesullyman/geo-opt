"""End-to-end CLI: init -> validate -> audit -> citations -> diff/trend -> report -> fix -> reparse -> raw.
Everything offline via the FakeWeb transport."""

import json

from typer.testing import CliRunner

from geo.cli import EXIT_REGRESSION, app

from .conftest import HOST, fixture_json, fixture_text, serve_site

ALLOW_ALL = "User-agent: *\nAllow: /\n"
runner = CliRunner()


def geo(home, *args):
    r = runner.invoke(app, ["--home", str(home), *args], catch_exceptions=False)
    return r


def gjson(home, *args):
    r = geo(home, "--json", *args)
    assert r.exit_code == 0, r.output
    return json.loads(r.stdout)


def test_init_validate_clients(tmp_path):
    r = geo(tmp_path, "init", "new-co", "--name", "New Co Plumbing", "--domain", "https://newco.example/", "--city", "Tustin")
    assert r.exit_code == 0, r.output
    assert (tmp_path / "clients/new-co/proposed").is_dir()
    v = gjson(tmp_path, "validate", "new-co")
    assert any("no queries" in w for w in v["warnings"])
    assert gjson(tmp_path, "clients") == ["new-co"]
    assert geo(tmp_path, "init", "new-co", "--name", "x", "--domain", "x").exit_code == 1


def test_bad_config_is_a_clean_error(home):
    (home / "clients/smiles-dental/facts.yaml").write_text("telephone: [unclosed\n")
    r = geo(home, "validate", "smiles-dental")
    assert r.exit_code == 1 and "invalid YAML" in r.stderr
    (home / "clients/smiles-dental/facts.yaml").write_text("made_up_fact: 24/7 service\n")
    r = geo(home, "validate", "smiles-dental")
    assert r.exit_code == 1 and "made_up_fact" in r.stderr


def test_full_audit_then_fix_then_regression_loop(home, web, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "k")
    web.api_responses("api.perplexity.ai", [fixture_json("engines/pplx_hit.json")])

    # Month 1: WordPress "discourage search engines" accident
    serve_site(web, robots="User-agent: *\nDisallow: /\n")
    out1 = gjson(home, "audit", "smiles-dental", "-e", "perplexity", "-n", "5")
    fids = {f["check_id"] for f in out1["findings"]}
    assert "robots.blocks_oai_searchbot" in fids
    assert out1["previous_run"] is None
    rates = {(r["query_id"], r["entity"]): r for r in out1["rates"]}
    assert rates[("best-dentist", "client")]["mentioned"] == 5 and rates[("best-dentist", "client")]["n"] == 5

    # human-readable output also works
    r = geo(home, "show", out1["run_id"][:12])
    assert r.exit_code == 0 and "robots.txt blocks OAI-SearchBot" in r.stdout and "5/5" in r.stdout

    # Stage fixes (offline: uses stored robots.txt from the run)
    fx = gjson(home, "fix", "smiles-dental", "--offline")
    assert {x["kind"]: x["ready"] for x in fx} == {"robots": True, "schema": True, "faq": True}
    proposed = home / "clients/smiles-dental/proposed"
    assert "User-agent: OAI-SearchBot" in (proposed / "robots.txt").read_text()
    assert (proposed / "schema.html").read_text().startswith('<script type="application/ld+json">')

    # Month 2: fix applied; but the engine stops mentioning them in 3/5
    web.routes.clear()
    serve_site(web, robots=(proposed / "robots.txt").read_text())
    web.api_responses("api.perplexity.ai", [fixture_json("engines/pplx_hit.json")] * 2 + [fixture_json("engines/pplx_miss.json")] * 3)
    web.calls.clear()
    r = geo(home, "--json", "audit", "smiles-dental", "-e", "perplexity", "--fail-on-regression")
    assert r.exit_code == EXIT_REGRESSION
    out2 = json.loads(r.stdout)
    assert out2["previous_run"] == out1["run_id"]
    assert {d["query_id"] for d in out2["regressions"]["rate_drops"]} == {"best-dentist", "emergency"}

    d = gjson(home, "diff", "smiles-dental")
    resolved = {f["check_id"] for f in d["resolved"]}
    assert "robots.blocks_oai_searchbot" in resolved
    assert any(x["before"] == "5/5" and x["after"] == "2/5" for x in d["rates"])
    assert geo(home, "diff", "smiles-dental", "--fail-on-regression").exit_code == EXIT_REGRESSION

    t = gjson(home, "trend", "smiles-dental")
    assert [(p["mentioned"], p["n"]) for p in t] == [(10, 10), (4, 10)]

    runs = gjson(home, "runs", "smiles-dental")
    assert [x["id"] for x in runs] == [out2["run_id"], out1["run_id"]]

    # Report: pure function of stored data, no network
    web.routes.clear()
    web.api.clear()
    n_calls = len(web.calls)
    paths = gjson(home, "report", "smiles-dental", "--last", "3")
    assert len(web.calls) == n_calls
    html = open(paths["html"]).read()
    assert "Smiles Dental" in html and "Changes since previous audit" in html
    assert "2/5" in html and "NOT the ChatGPT app" not in html  # only engines actually used are described
    assert "Perplexity Sonar API" in html
    assert "robots.txt blocks OAI-SearchBot" in html  # resolved list


def test_citations_dry_run_and_missing_keys(home):
    plan = gjson(home, "citations", "smiles-dental", "--dry-run")
    assert len(plan) == 30 and plan[0]["locality"]["city"] == "Orange"
    r = geo(home, "citations", "smiles-dental")
    assert r.exit_code == 1 and "no samples collected" in r.stderr


def test_raw_and_reparse_commands(home, web, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    web.api_responses("api.openai.com", [fixture_json("engines/openai_phone.json")])
    out = gjson(home, "citations", "smiles-dental", "-e", "openai", "-n", "1", "-q", "best-dentist")
    rate = [r for r in out["rates"] if r["entity"] == "client"][0]
    assert rate["mentioned"] == 1  # matched on phone number alone
    run = gjson(home, "show", out["run_id"])
    ref = run["samples"][0]["raw_ref"]
    r = geo(home, "raw", ref[:10])
    assert r.exit_code == 0 and json.loads(r.stdout)["id"] == "resp_1"
    meta = gjson(home, "raw", ref, "--meta")
    assert meta["artifacts"][0]["url"] == "https://api.openai.com/v1/responses"
    assert "sk-test" not in json.dumps(meta)
    rp = gjson(home, "reparse", "smiles-dental")
    assert rp["rederived"] == 0  # already current


def test_pdf_render_uses_headless_chrome_if_present(home, web, monkeypatch):
    import shutil

    from geo.report import find_chrome

    serve_site(web, robots=ALLOW_ALL)
    assert geo(home, "audit", "smiles-dental", "--no-citations").exit_code == 0
    if not find_chrome() or not shutil.which("true"):
        return
    paths = gjson(home, "report", "smiles-dental", "--pdf")
    assert open(paths["pdf"], "rb").read(5) == b"%PDF-"


def test_unknown_check_is_usage_error(home):
    assert geo(home, "audit", "smiles-dental", "--only", "nope").exit_code == 2


def test_audit_missing_keys_saves_site_results_but_exits_incomplete(home, web):
    serve_site(web, robots=ALLOW_ALL)
    result = geo(home, "--json", "audit", "smiles-dental")
    assert result.exit_code == 1
    out = json.loads(result.stdout)
    assert out["meta"]["citation_status"] == "unavailable"
    assert set(out["meta"]["engines_skipped"]) == {"openai", "perplexity", "google_aio"}
    assert gjson(home, "show", out["run_id"])["meta"]["citation_status"] == "unavailable"
    paths = gjson(home, "report", "smiles-dental")
    from pathlib import Path
    html = Path(paths["html"]).read_text()
    assert "AI visibility was not measured" in html
    assert "PERPLEXITY_API_KEY not set" in html
    assert gjson(home, "audit", "smiles-dental", "--no-citations")["meta"]["citation_status"] == "not_requested"


def test_failed_citation_samples_are_saved_and_exit_nonzero(home, web, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "k")
    web.api_responses("api.perplexity.ai", [(401, {"error": "invalid key"})])
    result = geo(home, "--json", "citations", "smiles-dental", "-e", "perplexity", "-n", "1")
    assert result.exit_code == 1
    out = json.loads(result.stdout)
    assert out["meta"]["citation_status"] == "failed"
    run = gjson(home, "show", out["run_id"])
    assert all(s["error"] and s["raw_ref"] for s in run["samples"])
