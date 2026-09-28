"""Golden-fixture tests for site checks. False positives cost more than crashes."""

import pytest

from geo.checks import SiteContext, run_checks
from geo.config import load_client
from geo.fetch import Fetcher
from geo.models import Severity
from geo.store import Store

from .conftest import HOST, fixture_text, serve_site

ALLOW_ALL = "User-agent: *\nAllow: /\nSitemap: https://smilesdental.example/sitemap.xml\n"


def audit(home, only=None, flags=None):
    c = load_client("smiles-dental", home)
    st = Store(home / "data")
    with Fetcher(st, "test-run") as f:
        res = run_checks(SiteContext(client=c, fetcher=f, flags=flags or {}), only=only)
    assert res.errors == [], res.errors
    return res.findings


def ids(findings):
    return {f.check_id for f in findings}


def by_id(findings, cid):
    return [f for f in findings if f.check_id == cid]


# -------------------------------------------------------------------- robots


def test_accidental_wordpress_block_is_a_blocker_per_retrieval_bot(home, web):
    serve_site(web, robots="User-agent: *\nDisallow: /\n")
    fs = audit(home, only=["robots"])
    blockers = {f.check_id for f in fs if f.severity == Severity.blocker}
    assert {"robots.blocks_oai_searchbot", "robots.blocks_perplexitybot", "robots.blocks_claude_searchbot",
            "robots.blocks_googlebot", "robots.blocks_claude_user"} <= blockers
    # user-triggered fetchers that ignore robots are warnings, not blockers
    assert by_id(fs, "robots.blocks_chatgpt_user")[0].severity == Severity.warning
    f = by_id(fs, "robots.blocks_oai_searchbot")[0]
    assert f.fix and f.fix.generator == "robots"
    assert f.raw_ref  # evidence traceable to the stored robots.txt
    assert f.evidence["verdicts"][f"https://{HOST}/"]["rule"] == "Disallow: / (line 2)"


def test_training_block_is_policy_info_not_a_blocker(home, web):
    serve_site(web, robots="User-agent: GPTBot\nUser-agent: CCBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n")
    fs = audit(home, only=["robots"])
    assert not [f for f in fs if f.severity != Severity.info]
    (pol,) = by_id(fs, "robots.training_policy")
    assert pol.evidence["bots"]["GPTBot"] == "blocked"
    assert pol.evidence["bots"]["ClaudeBot"] == "allowed"


def test_partial_block_of_key_page_is_warning(home, web):
    serve_site(web, robots="User-agent: PerplexityBot\nDisallow: /services\n")
    (f,) = by_id(audit(home, only=["robots"]), "robots.blocks_perplexitybot")
    assert f.severity == Severity.warning
    assert f.evidence["blocked_urls"] == [f"https://{HOST}/services"]


def test_robots_5xx_is_blocker_and_404_is_info(home, web):
    serve_site(web)
    web.page(HOST, "/robots.txt", "oops", status=503)
    assert ids(audit(home, only=["robots"])) == {"robots.server_error"}


def test_no_robots_is_fine(home, web):
    serve_site(web)
    assert ids(audit(home, only=["robots"])) == {"robots.absent"}


def test_clean_site_has_no_robots_problems(home, web):
    serve_site(web, robots=ALLOW_ALL)
    assert ids(audit(home, only=["robots"])) == {"robots.training_policy"}


@pytest.mark.parametrize("target", ["/sitemap.xml", "sitemap.xml"])
def test_relative_sitemap_is_resolved_and_flagged(home, web, target):
    serve_site(web, robots=f"User-agent: *\nAllow: /\nSitemap: {target}\n")
    fs = audit(home, only=["sitemap"])
    assert ids(fs) == {"sitemap.invalid_url"}
    assert fs[0].evidence["resolved"] == f"https://{HOST}/sitemap.xml"


@pytest.mark.parametrize("target", ["ftp://example.com/sitemap.xml", "https://[broken"])
def test_invalid_sitemap_does_not_crash(home, web, target):
    serve_site(web, robots=f"User-agent: *\nAllow: /\nSitemap: {target}\n")
    assert ids(audit(home, only=["sitemap"])) == {"sitemap.invalid_url", "sitemap.missing"}


# -------------------------------------------------------------------- clean site: golden negative


def test_good_site_has_no_blockers_or_warnings(home, web):
    serve_site(web, robots=ALLOW_ALL)
    fs = audit(home)
    bad = [(f.check_id, f.title) for f in fs if f.severity != Severity.info]
    assert bad == []


# -------------------------------------------------------------------- broken site: golden positive


def test_spa_site_findings(home, web):
    serve_site(web, robots=ALLOW_ALL, home_html=fixture_text("sites/spa_home.html"), sitemap=False)
    fs = audit(home)
    got = ids(fs)
    assert "status.noindex" in got
    assert "crawl.js_required" in got
    assert "schema.jsonld_syntax" in got  # the trailing-comma FAQPage block
    assert "sitemap.missing" in got
    errs = " ".join(e for f in by_id(fs, "schema.invalid") for e in f.evidence["errors"])
    assert "servesCuisine" in errs  # property not defined for LocalBusiness
    assert "FAQPage requires mainEntity" in errs  # extruct parses the malformed block leniently
    (nap,) = by_id(fs, "nap.schema_mismatch")
    assert set(nap.evidence["mismatches"]) == {"telephone", "streetAddress"}


def test_no_structured_data_is_blocker(home, web):
    serve_site(web, robots=ALLOW_ALL,
               home_html="<html><body><h1>Smiles</h1><p>" + "words " * 300 + "</p></body></html>")
    assert "schema.no_localbusiness" in ids(audit(home, only=["schema"]))


# -------------------------------------------------------------------- status / canonical / hosts


def test_404_key_page_is_blocker(home, web):
    serve_site(web, robots=ALLOW_ALL)
    web.page(HOST, "/services", "gone", status=404)
    (f,) = by_id(audit(home, only=["status"]), "status.http_error")
    assert f.severity == Severity.blocker and f.evidence["status"] == 404


def test_x_robots_tag_noindex_header(home, web):
    serve_site(web, robots=ALLOW_ALL)
    web.page(HOST, "/services", fixture_text("sites/good_services.html"), headers={"X-Robots-Tag": "noindex"})
    (f,) = by_id(audit(home, only=["status"]), "status.noindex")
    assert f.evidence["source"] == "X-Robots-Tag header"


def test_offsite_canonical(home, web):
    serve_site(web, robots=ALLOW_ALL,
               home_html=fixture_text("sites/good_home.html").replace(
                   'href="https://smilesdental.example/"', 'href="https://old-agency-template.example/"'))
    assert "canonical.offsite" in ids(audit(home, only=["canonical"]))


def test_split_origin(home, web):
    serve_site(web, robots=ALLOW_ALL)
    web.page("www." + HOST, "/", "<html>dup</html>")  # www serves its own copy instead of redirecting
    assert "hosts.split_origin" in ids(audit(home, only=["hosts"]))


# -------------------------------------------------------------------- content


def test_thin_page_without_question_headings(home, web):
    serve_site(web, robots=ALLOW_ALL,
               home_html="<html><body><h1>Welcome</h1><p>We love smiles.</p></body></html>",
               services_html="<html><body><h1>Services</h1><h3>Crowns</h3><p>This is what we do.</p></body></html>")
    got = ids(audit(home, only=["content"]))
    assert {"content.thin", "content.no_question_headings", "content.heading_skips", "content.dangling_references"} <= got


# -------------------------------------------------------------------- WAF probe (opt-in)


def test_waf_probe_is_opt_in_and_detects_ua_block(home, web):
    serve_site(web, robots=ALLOW_ALL)
    web.page(HOST, "/", fixture_text("sites/good_home.html"), ua_status={"PerplexityBot": 403})
    assert not [f for f in audit(home) if f.check_id.startswith("waf.")]
    fs = audit(home, only=["waf_probe"], flags={"probe_bots": True})
    assert ids(fs) == {"waf.blocks_perplexitybot"}


# -------------------------------------------------------------------- engine robustness


def test_crashing_check_is_reported_not_turned_into_finding(home, web, monkeypatch):
    from geo import checks

    serve_site(web, robots=ALLOW_ALL)
    spec = checks.registry()["content"]
    monkeypatch.setitem(checks._REGISTRY, "content", spec.__class__(**{**spec.__dict__, "fn": lambda ctx: 1 / 0}))
    c = load_client("smiles-dental", home)
    with Fetcher(Store(home / "data"), "r") as f:
        res = run_checks(SiteContext(client=c, fetcher=f), only=["content"])
    assert res.findings == [] and res.errors[0]["check"] == "content"


@pytest.mark.parametrize("path", ["/", "/services"])
def test_every_fetch_is_stored_raw(home, web, path):
    serve_site(web, robots=ALLOW_ALL)
    st = Store(home / "data")
    c = load_client("smiles-dental", home)
    with Fetcher(st, "raw-run") as f:
        SiteContext(client=c, fetcher=f).page(path)
    arts = [a for a in st.artifacts("raw-run") if a["url"].endswith(path)]
    assert arts and st.blobs.exists(arts[0]["raw_ref"])
