"""Citation tester on recorded engine responses. Zero network."""

import json

from geo.citations import plan, reparse_run, run_citations
from geo.citations.engines import GoogleAIO, OpenAI, Perplexity
from geo.citations.mentions import Detector, entities_for
from geo.config import load_client
from geo.fetch import Fetcher
from geo.models import Locality, Run, RunKind, compute_rates
from geo.store import Store

from .conftest import fixture_json

LOC = Locality(city="Orange", region="California", country="US", timezone="America/Los_Angeles",
               serp_location="Orange,California,United States")


def test_perplexity_payload_pins_location():
    p = Perplexity().payload("best dentist in Orange CA", LOC)
    assert p["web_search_options"]["user_location"] == {"country": "US", "city": "Orange", "region": "California"}
    assert p["messages"] == [{"role": "user", "content": "best dentist in Orange CA"}]  # no steering system prompt


def test_openai_payload_pins_location():
    p = OpenAI().payload("q", LOC)
    assert p["tools"][0]["user_location"] == {"type": "approximate", "country": "US", "city": "Orange",
                                              "region": "California", "timezone": "America/Los_Angeles"}


def test_parsers_extract_text_and_cited_urls():
    pp = Perplexity().parse(json.dumps(fixture_json("engines/pplx_hit.json")).encode())
    assert "Smiles Dental" in pp.answer_text and pp.cited_urls[0] == "https://www.smilesdental.example/"
    op = OpenAI().parse(json.dumps(fixture_json("engines/openai_phone.json")).encode())
    assert "https://orangefamilydentistry.example/?utm_source=chatgpt.com" in op.cited_urls
    assert "https://www.yelp.com/x" not in op.cited_urls  # retrieved, not cited
    gp = GoogleAIO().parse(json.dumps(fixture_json("engines/serp_aio.json")).encode())
    assert "Smiles Dental:" in gp.answer_text and len(gp.cited_urls) == 2
    none = GoogleAIO().parse(json.dumps(fixture_json("engines/serp_none.json")).encode())
    assert none.empty_reason == "no_ai_overview" and none.answer_text == ""


def test_retrieval_without_citation_does_not_count_as_cited():
    body = fixture_json("engines/pplx_hit.json")
    body["citations"] = []
    assert body["search_results"]
    assert Perplexity().parse(json.dumps(body).encode()).cited_urls == []


def test_mention_detection_is_deterministic_and_multi_signal(home):
    c = load_client("smiles-dental", home)
    d = Detector(entities_for(c))

    def m(text, urls=()):
        return {x.entity: x for x in d.detect(text, list(urls))}

    r = m("Try **Smiles Dental** on Glassell.")
    assert r["client"].mentioned and r["client"].matched_on == ["name"]
    assert m("Call 714.555.0142 today")["client"].matched_on == ["phone"]
    assert m("See smilesdental.example for hours")["client"].matched_on == ["domain"]
    assert m("Located at 123 N Glassell St Ste 4")["client"].matched_on == ["address"]
    # normalization: case, punctuation, possessive
    assert m("SMILES-DENTAL's team")["client"].mentioned
    # citation without mention in text
    r = m("Several options exist.", ["https://blog.smilesdental.example/post"])
    assert r["client"].cited and not r["client"].mentioned
    # no false positives on near-misses
    assert not m("Big Smiles Dentalcare and notsmilesdental.example.com")["client"].mentioned
    assert not m("Call 714-555-0143")["client"].mentioned
    assert m("Orange Family Dentistry is great")["Orange Family Dentistry"].mentioned


def test_plan_expands_samples_per_query_engine(home):
    c = load_client("smiles-dental", home)
    jobs = plan(c)
    assert len(jobs) == 2 * 3 * 5
    assert {j.locality.city for j in jobs} == {"Orange"}
    assert len(plan(c, engines=["openai"], samples=2)) == 4


def test_run_samples_n_times_and_computes_rates(home, web, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "k1")
    monkeypatch.setenv("SERPAPI_API_KEY", "k3")
    web.api_responses("api.perplexity.ai", [fixture_json("engines/pplx_hit.json")] * 3 + [fixture_json("engines/pplx_miss.json")] * 2)
    web.api_responses("serpapi.com", [fixture_json("engines/serp_aio.json"), fixture_json("engines/serp_none.json")])
    c = load_client("smiles-dental", home)
    st = Store(home / "data")
    with Fetcher(st, "cit-run") as f:
        samples, skipped = run_citations(c, f, queries=["best-dentist"], concurrency=1)
    assert skipped == {"openai": "OPENAI_API_KEY not set"}
    assert len(samples) == 10
    rates = {(r.engine, r.entity): r for r in compute_rates(samples, c.entities())}
    assert rates[("perplexity", "client")].label == "3/5"
    assert rates[("perplexity", "Orange Family Dentistry")].mentioned == 5
    # AIO absent on some samples: counted in n (a real observation), flagged empty
    g = rates[("google_aio", "client")]
    assert g.n == 5 and 1 <= g.mentioned < 5
    assert any(s.empty_reason == "no_ai_overview" for s in samples if s.engine == "google_aio")
    # every sample's raw completion is on disk, and secrets never hit the artifact log
    assert all(st.blobs.exists(s.raw_ref) for s in samples)
    log = json.dumps(st.artifacts("cit-run"))
    assert "k1" not in log and "k3" not in log and "<redacted>" in log


def test_api_errors_are_recorded_not_counted(home, web, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "k")
    web.api_responses("api.perplexity.ai", [(401, {"error": "bad key"})])
    c = load_client("smiles-dental", home)
    with Fetcher(Store(home / "data"), "r") as f:
        samples, _ = run_citations(c, f, engines=["perplexity"], queries=["best-dentist"], samples=2)
    assert all(s.error and "401" in s.error for s in samples)
    (r,) = [r for r in compute_rates(samples, ["client"])]
    assert r.n == 0 and r.errors == 2 and r.label == "n/a"


def test_serpapi_page_token_followup(home, web, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "k")
    web.api_responses("serpapi.com", [fixture_json("engines/serp_token.json"), fixture_json("engines/serp_followup.json")])
    c = load_client("smiles-dental", home)
    with Fetcher(Store(home / "data"), "r") as f:
        (s,) = run_citations(c, f, engines=["google_aio"], queries=["best-dentist"], samples=1)[0]
    assert s.mention_for("client").mentioned and s.cited_urls == ["https://smilesdental.example/"]
    assert web.calls[1].url.params["page_token"] == "TOKEN123"


def test_reparse_rederives_from_raw_without_mutating_history(home, web, monkeypatch):
    import geo.citations as cit

    monkeypatch.setenv("PERPLEXITY_API_KEY", "k")
    web.api_responses("api.perplexity.ai", [fixture_json("engines/openai_miss.json")])  # wrong shape -> parse yields ""
    web.api_responses("api.perplexity.ai", [fixture_json("engines/pplx_hit.json")])
    c = load_client("smiles-dental", home)
    st = Store(home / "data")
    run = Run(client=c.slug, kind=RunKind.citations)
    with Fetcher(st, run.id) as f:
        run.samples, _ = run_citations(c, f, engines=["perplexity"], queries=["best-dentist"], samples=2)
    # simulate an older, dumber parser that missed the mention
    run.samples = [s.model_copy(update={"mentions": [], "parser_version": "old"}) for s in run.samples]
    st.save_run(run)
    assert compute_rates(st.get_run(run.id).samples, ["client"])[0].mentioned == 0
    written, _ = reparse_run(st, c, run.id)
    assert written == 2
    after = st.get_run(run.id)
    assert compute_rates(after.samples, ["client"])[0].mentioned == 2
    assert {s.parser_version for s in after.samples} == {cit.PARSER_VERSION}
    # original derivations still there
    n = st.conn.execute("SELECT COUNT(*) FROM samples WHERE run_id=?", (run.id,)).fetchone()[0]
    assert n == 4
