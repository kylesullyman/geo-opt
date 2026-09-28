"""Fix generators: facts-only, validated, never published."""

import json
import re
from pathlib import Path

from geo import schemaorg
from geo.config import load_client
from geo.fixes import GENERATORS, NEEDS_INPUT
from geo.robots import Robots


def test_robots_fix_unblocks_retrieval_keeps_private_paths_and_policy(home):
    c = load_client("smiles-dental", home)
    current = "User-agent: *\nDisallow: /\nDisallow: /wp-admin/\n\nUser-agent: GPTBot\nDisallow: /\n\nUser-agent: PerplexityBot\nDisallow: /\n"
    art = GENERATORS["robots"](c, current, "https://smilesdental.example/sitemap.xml")
    assert art.ready, art.validation
    r = Robots.parse(art.content)
    for bot in ("OAI-SearchBot", "PerplexityBot", "Claude-SearchBot", "Googlebot", "ChatGPT-User"):
        assert r.allowed(bot, "/"), bot
        assert not r.allowed(bot, "/wp-admin/x"), bot
    assert not r.allowed("GPTBot", "/")  # training policy 'unchanged': their block stays
    assert not r.allowed("SomeRandomBot", "/")  # '*' group preserved as-is
    assert "Sitemap: https://smilesdental.example/sitemap.xml" in art.content


def test_robots_fix_training_policy_block(home):
    c = load_client("smiles-dental", home)
    c.config.training_bots = "block"
    art = GENERATORS["robots"](c, "User-agent: *\nAllow: /\n")
    r = Robots.parse(art.content)
    assert art.ready and not r.allowed("CCBot", "/") and r.allowed("OAI-SearchBot", "/")


def test_schema_fix_uses_only_facts_and_validates(home):
    c = load_client("smiles-dental", home)
    art = GENERATORS["schema"](c)
    assert art.ready, art.validation
    doc = json.loads(art.content)
    assert doc["@type"] == "Dentist"
    assert doc["telephone"] == "+1-714-555-0142"
    assert doc["openingHoursSpecification"][0]["dayOfWeek"] == ["Monday", "Tuesday", "Wednesday", "Thursday"]
    assert schemaorg.validate(doc).ok
    # nothing in the output that isn't traceable to facts/client config
    blob = art.content
    assert "24/7" not in blob and "emergency" not in blob.lower().replace("emergency dental care during business hours", "")


def test_schema_fix_refuses_to_guess_missing_facts(home):
    c = load_client("smiles-dental", home)
    c.facts.telephone = None
    c.facts.hours = None
    art = GENERATORS["schema"](c)
    assert not art.ready
    assert set(art.needs_input) == {"telephone", "hours"}
    assert json.loads(art.content)["telephone"] == NEEDS_INPUT


def test_schema_fix_rejects_invented_type(home):
    c = load_client("smiles-dental", home)
    c.facts.schema_type = "DentalSpa"
    assert not GENERATORS["schema"](c).ready


def test_faq_fix_answers_only_from_facts(home):
    c = load_client("smiles-dental", home)
    c.facts.service_area = None
    art = GENERATORS["faq"](c)
    assert "## Where is Smiles Dental located?" in art.content
    assert "123 North Glassell Street, Suite 4, Orange, CA 92866" in art.content
    assert "Monday through Thursday, 08:00 to 17:00" in art.content
    assert f"{NEEDS_INPUT} add `service_area`" in art.content
    assert art.needs_input == ["service_area"] and not art.ready
    doc = json.loads(re.search(r"```json\n(.*?)\n```", art.content, re.S).group(1))
    assert schemaorg.validate(doc).ok
    assert all("serve" not in q["name"] for q in doc["mainEntity"])  # unanswered Q not in JSON-LD


def test_no_write_path_to_client_sites():
    """Structural guarantee: nothing outside fetch/ touches httpx, and fetch only GETs sites."""
    src = Path(__file__).parent.parent / "src" / "geo"
    for p in src.rglob("*.py"):
        if "fetch" in p.parts:
            continue
        assert "import httpx" not in p.read_text(), f"{p} imports httpx outside fetch/"
    fetch_src = (src / "fetch" / "__init__.py").read_text()
    site_get = fetch_src.split("def get(")[1].split("def api(")[0]
    assert '_send("GET"' in site_get and "POST" not in site_get and "PUT" not in site_get
