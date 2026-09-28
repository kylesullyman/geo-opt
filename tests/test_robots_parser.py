"""RFC 9309 semantics: the robots verdict is the product's headline finding, so
it gets the most exhaustive tests."""

from geo.robots import Robots


def test_specific_group_overrides_star():
    r = Robots.parse("User-agent: *\nDisallow: /\n\nUser-agent: OAI-SearchBot\nAllow: /\n")
    assert r.allowed("OAI-SearchBot", "/")
    assert not r.allowed("PerplexityBot", "/")


def test_specific_group_does_not_inherit_star_rules():
    # A bot with its own group ignores '*' entirely, even if its group is empty-ish.
    r = Robots.parse("User-agent: *\nDisallow: /private\n\nUser-agent: GPTBot\nDisallow: /tmp\n")
    assert r.allowed("GPTBot", "/private")
    assert not r.allowed("GPTBot", "/tmp/x")


def test_longest_match_wins_regardless_of_order():
    r = Robots.parse("User-agent: *\nAllow: /blog/public\nDisallow: /blog\n")
    assert r.allowed("x", "/blog/public/post")
    assert not r.allowed("x", "/blog/other")
    r2 = Robots.parse("User-agent: *\nDisallow: /blog\nAllow: /blog/public\n")
    assert r2.allowed("x", "/blog/public/post")


def test_allow_wins_on_equal_length():
    r = Robots.parse("User-agent: *\nDisallow: /page\nAllow: /page\n")
    assert r.allowed("x", "/page")


def test_wildcards_and_end_anchor():
    r = Robots.parse("User-agent: *\nDisallow: /*.pdf$\nDisallow: /*?session=\n")
    assert not r.allowed("x", "/files/menu.pdf")
    assert r.allowed("x", "/files/menu.pdf.html")
    assert not r.allowed("x", "/page?session=abc")
    assert r.allowed("x", "/page?q=1")


def test_ua_match_is_exact_token_not_substring():
    # urllib.robotparser would let "Claude-User" match a "Claude" group; RFC 9309 does not.
    r = Robots.parse("User-agent: Claude\nDisallow: /\n")
    assert r.allowed("Claude-User", "/")
    r2 = Robots.parse("User-agent: claudebot\nDisallow: /\n")
    assert not r2.allowed("ClaudeBot", "/")  # case-insensitive


def test_grouped_user_agents_share_rules_and_merge_duplicate_groups():
    r = Robots.parse("User-agent: GPTBot\nUser-agent: CCBot\nDisallow: /\n\nUser-agent: GPTBot\nAllow: /ok\n")
    assert not r.allowed("CCBot", "/x")
    assert not r.allowed("GPTBot", "/x")
    assert r.allowed("GPTBot", "/ok")


def test_empty_disallow_allows_everything_and_no_star_group_allows():
    assert Robots.parse("User-agent: *\nDisallow:\n").allowed("x", "/anything")
    assert Robots.parse("User-agent: GPTBot\nDisallow: /\n").allowed("PerplexityBot", "/")


def test_robots_txt_itself_always_allowed_and_comments_bom_ignored():
    r = Robots.parse("\ufeffUser-agent: * # everyone\nDisallow: / # all\nSitemap: https://x/sitemap.xml\n")
    assert r.allowed("x", "/robots.txt")
    assert not r.allowed("x", "/")
    assert r.sitemaps == ["https://x/sitemap.xml"]


def test_full_url_and_percent_encoding():
    r = Robots.parse("User-agent: *\nDisallow: /caf%C3%A9\n")
    assert not r.allowed("x", "https://example.com/café/menu")


def test_verdict_evidence_names_the_rule_and_line():
    r = Robots.parse("User-agent: *\nAllow: /\n\nUser-agent: PerplexityBot\nDisallow: /\n")
    v = r.verdict("PerplexityBot", "/")
    assert v.as_evidence() == {"allowed": False, "matched_group": "explicit", "rule": "Disallow: / (line 5)"}
