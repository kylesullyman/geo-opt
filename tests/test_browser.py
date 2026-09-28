import json
from pathlib import Path

import pytest

from geo.citations import reparse_run
from geo.config import load_client
from geo.models import utcnow
from geo.store import Store
from .test_cli import geo, gjson


def capture(home, count=2):
    prepared = gjson(home, "browser", "prepare", "smiles-dental", "-n", "1")
    plan = json.loads(Path(prepared["plan"]).read_text())
    rows = []
    for j in plan["jobs"][:count]:
        rows.append({"query_id": j["query_id"], "sample_idx": j["sample_idx"], "prompt": j["prompt"],
                     "answer_text": "Smiles Dental is an option in Orange.",
                     "cited_urls": ["https://smilesdental.example/"], "observed_at": utcnow().isoformat(),
                     "model_label": "Observed model", "search_used": "yes", "session": "temporary",
                     "personalization": "unknown", "citations_reviewed": True, "complete": True})
    bundle = {"format": plan["format"], "batch_id": plan["batch_id"], "client": plan["client"], "observations": rows}
    path = home / "capture.json"
    path.write_text(json.dumps(bundle))
    return prepared, bundle, path


def test_browser_import_report_raw_and_duplicate(home):
    prep, bundle, path = capture(home)
    # Neither importing nor reporting should need API keys or a transport.
    result = gjson(home, "browser", "import", "smiles-dental", str(path))
    assert result["meta"]["source"] == "chatgpt_browser"
    assert result["meta"]["citation_status"] == "complete"
    assert all(r["n"] == 1 for r in result["rates"])
    st = Store(home / "data")
    run = st.get_run(result["run_id"])
    assert st.blobs.get(run.meta["capture_raw_ref"]) == path.read_bytes()
    assert all(s.engine == "chatgpt_browser" and s.mention_for("client").cited for s in run.samples)
    assert len(st.artifacts(run.id)) == 2
    html = Path(gjson(home, "report", "smiles-dental")["html"]).read_text()
    assert "Actual ChatGPT browser observations" in html
    assert "browser location was not pinned" in html
    assert "Observed model" in html
    assert geo(home, "browser", "import", "smiles-dental", str(path)).exit_code == 1
    assert len(st.list_runs("smiles-dental")) == 1


@pytest.mark.parametrize("change", [
    {"prompt": "different question"}, {"sample_idx": 10}, {"complete": False},
    {"citations_reviewed": False}, {"answer_text": " "},
    {"observed_at": "2026-09-23T12:00:00"}, {"observed_at": "2000-01-01T12:00:00Z"},
    {"cited_urls": ["javascript:alert(1)"]}, {"conversation_url": "https://evil.example/c/1"},
    {"conversation_url": "https://chatgpt.com/c/1?token=secret"},
])
def test_invalid_capture_does_not_save_run(home, change):
    _, b, p = capture(home)
    b["observations"][0].update(change)
    p.write_text(json.dumps(b))
    assert geo(home, "browser", "import", "smiles-dental", str(p)).exit_code == 1
    assert Store(home / "data").list_runs("smiles-dental") == []


def test_partial_and_failure_are_not_zero_visibility(home):
    _, b, p = capture(home, count=1)
    assert geo(home, "browser", "import", "smiles-dental", str(p)).exit_code == 1
    b["observations"][0].update(answer_text="", cited_urls=[], error="Usage limit reached")
    p.write_text(json.dumps(b))
    result = geo(home, "--json", "browser", "import", "smiles-dental", str(p), "--allow-partial")
    assert result.exit_code == 1
    out = json.loads(result.stdout)
    assert out["meta"]["planned_samples"] == 2 and out["meta"]["captured_samples"] == 1
    assert out["rates"][0]["n"] == 0 and out["rates"][0]["mention_rate"] is None


def test_duplicate_sample_and_wrong_client_rejected(home):
    _, b, p = capture(home)
    b["observations"][1] = b["observations"][0]
    p.write_text(json.dumps(b))
    assert "duplicate observation" in geo(home, "browser", "import", "smiles-dental", str(p)).stderr
    b["client"] = "other-business"
    p.write_text(json.dumps(b))
    assert "different client" in geo(home, "browser", "import", "smiles-dental", str(p)).stderr


def test_browser_reparse_uses_snapshot(home, monkeypatch):
    _, b, p = capture(home)
    out = gjson(home, "browser", "import", "smiles-dental", str(p))
    import geo.citations as citations
    monkeypatch.setattr(citations, "PARSER_VERSION", "browser-test-2")
    client = load_client("smiles-dental", home)
    client.config.name = "Renamed business"
    client.config.domain = "changed.example"
    client.config.aliases = []
    client.facts.legal_name = None
    st = Store(home / "data")
    assert reparse_run(st, client, out["run_id"])[0] == 2
    assert st.get_run(out["run_id"]).samples[0].mention_for("client").cited


def test_browser_prepare_exact_prompts_and_safe_html(home):
    assert geo(home, "browser", "prepare", "smiles-dental", "-q", "typo").exit_code == 1
    prep = gjson(home, "browser", "prepare", "smiles-dental", "-q", "best-dentist", "-n", "2")
    plan = json.loads(Path(prep["plan"]).read_text())
    assert len(plan["jobs"]) == 2
    assert all(j["query_id"] == "best-dentist" for j in plan["jobs"])
    assert "Download capture JSON" in Path(prep["html"]).read_text()


def test_capture_and_report_escape_untrusted_labels(home):
    from geo.citations.browser import prepare
    client = load_client("smiles-dental", home)
    client.config.name = '<script>alert("name")</script>'
    prepared = prepare(client, home / "data", samples=1)
    html = Path(prepared["html"]).read_text()
    assert client.config.name not in html
    assert "&lt;script&gt;" in html
    _, b, path = capture(home)
    b["observations"][0]["model_label"] = '<script>alert("model")</script>'
    path.write_text(json.dumps(b))
    gjson(home, "browser", "import", "smiles-dental", str(path))
    html = Path(gjson(home, "report", "smiles-dental")["html"]).read_text()
    assert '<script>alert("model")</script>' not in html
    assert "&lt;script&gt;" in html


def test_setup_api_preserves_other_settings_and_hides_key(home):
    (home / ".env").write_text("# keep me\nGEO_OPENAI_MODEL=gpt-5-mini\nOPENAI_API_KEY=old\n")
    from .test_cli import runner
    result = runner.invoke(__import__("geo.cli", fromlist=["app"]).app,
                           ["--home", str(home), "setup-api", "openai"], input="sk-test-secret\n")
    assert result.exit_code == 0, result.output
    assert "sk-test-secret" not in result.output
    assert (home / ".env").read_text() == "# keep me\nGEO_OPENAI_MODEL=gpt-5-mini\nOPENAI_API_KEY=sk-test-secret\n"
    assert (home / ".env").stat().st_mode & 0o777 == 0o600


def test_capture_page_javascript(home):
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("Node is optional; needed for the worksheet JavaScript smoke test")
    prep = gjson(home, "browser", "prepare", "smiles-dental", "-n", "1")
    result = subprocess.run(["node", str(Path(__file__).with_name("capture_page_smoke.cjs")), prep["html"]],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
