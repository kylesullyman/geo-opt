# GEO Service — Overview

*AI search visibility as a productized service for local businesses in Orange County, CA.*

---

## Part I — The Business

### The idea

Local businesses (dentists, contractors, restaurants, salons, med spas) are increasingly found — or not found — through AI answers (ChatGPT, Perplexity, Google AI Overviews) rather than a list of blue links. Almost none of them know whether they appear in those answers, and fewer still know what would change it.

The service: **audit a business's AI visibility, fix what's blocking it, and keep monitoring.** Sold as a productized offering, not custom consulting.

### Why the opportunity exists

The failure modes are mechanical and unglamorous, which is exactly why they persist:

- A WordPress plugin or a Cloudflare setting is blocking the crawlers that AI systems use at answer time — the business is invisible and no one knows.
- No structured data, so the business's identity, service area, and hours are ambiguous to a retrieval system.
- Content written as marketing prose rather than as self-contained answers, so it never survives chunking and reranking.
- Name/address/phone inconsistencies across the site and directories, so nothing wins on confidence.

None of this requires the client to understand AI. It requires someone to measure it and fix it.

### How the underlying tech works

AI engines answer through a **retrieval pipeline**, not a ranking algorithm:

```
crawl → chunk pages → embed chunks → index
                                       ↓
query → retrieve candidates → rerank → generate answer → cite sources
```

Every stage has controllable levers:

| Stage | Lever | What we check / fix |
|---|---|---|
| Crawl | Access | robots.txt per bot, status codes, JS-dependence, sitemaps |
| Chunk | Structure | heading hierarchy, self-contained paragraphs, question-shaped headings |
| Index | Identity | JSON-LD schema, NAP consistency, entity disambiguation |
| Rerank | Confidence | freshness, specificity, corroboration across sources |
| Cite | Attribution | canonical URLs, being the authoritative source for own facts |

**The distinction that matters most:** training crawlers and answer-time fetchers are different populations, and blocking them has different consequences.

- **Training** (`GPTBot`, `ClaudeBot`, `CCBot`, `Google-Extended`, `Applebot-Extended`, `Meta-ExternalAgent`, `Bytespider`) — affects future model knowledge.
- **Answer-time retrieval** (`OAI-SearchBot`, `ChatGPT-User`, `Claude-SearchBot`, `Claude-User`, `PerplexityBot`, `Perplexity-User`, `Googlebot`) — affects **what these systems can say about the business right now.**

Blocking the retrieval group is the single highest-value, most demoable finding, and it is extremely common as an accident. The tool must report the two groups separately; conflating them produces advice that is wrong in both directions.

### The service loop

1. **Detect** — crawlability, schema, content structure, and whether the business is actually mentioned for real customer queries.
2. **Generate the fix** — corrected robots.txt, generated schema markup, drafted FAQ content.
3. **Apply** — with a human in the loop. Most fixes land in someone else's CMS, and AI-drafted content is always reviewed before it goes live.
4. **Monitor** — rerun the same test queries on a schedule and track whether visibility trends up.

### Packaging

| Tier | What it is | Delivers |
|---|---|---|
| **Audit** | One-time diagnostic | Report: findings by severity, baseline visibility scores, prioritized fix list |
| **Implementation** | One-time remediation | Applied fixes (or handoff-ready artifacts), re-run showing before/after |
| **Monitoring** | Monthly retainer | Scheduled reruns, trend reporting, alerts on regressions, ongoing content additions |

The retainer is the business. The audit's real job is to be cheap, fast, and convincing enough to sell the retainer.

### The scalability split

This is the structural decision the whole business rests on:

**Mechanical → built once, pointed at any client.** Crawl checks, schema validation, the citation-test runner, report generation. Zero marginal thought per client.

**Judgment → manual, per client.** Which test queries actually matter for this business. Who is currently winning those queries and why. How a fix gets implemented in this client's specific CMS. What the business actually offers and can truthfully claim.

Keeping that line sharp is what lets client #12 take an afternoon instead of a week, without degrading into a generic report that misses what matters for each client. In the tool, the split is literal: **the judgment lives in per-client config files, the mechanics live in code.**

### Honesty constraints (these are commercial, not just ethical)

- **Say which surface each number measures.** The OpenAI API with web search is not the same answer distribution as a person using ChatGPT. Google AI Overviews has no API at all. Overclaiming survives exactly until the client Googles themselves.
- **Never let generated content invent facts.** A hallucinated "24/7 emergency service" on a dentist's live site is a liability event, not a bug.
- **Report visibility as a rate, not a checkmark.** One query result is noise (see below). "Mentioned in 3/5 responses" is both more honest and more defensible than a green check.

---

## Part II — The Tool

### Shape: a pipeline, not an app

The tool is **a batch job that runs per client on a schedule and emits a report.** Build it as a Python library with a CLI:

```
geo audit smiles-dental             # full run, writes an immutable Run
geo citations smiles-dental         # just the query tester
geo report smiles-dental --last 3   # render HTML/PDF from stored runs
```

Cron, CI, a future client dashboard, and a live demo on a sales call all become callers of that CLI. A dashboard added later reads the same database and requires no change to the core. Starting with a web app instead smears the pipeline across route handlers and makes headless runs impossible — which kills the retainer, since the retainer is scheduled headless runs.

### Stack

- **Python 3.12**, `uv`, `pydantic` v2
- `httpx` (HTTP), `selectolax` (parsing), `extruct` (JSON-LD / microdata extraction), `trafilatura` (main-content extraction — needed for chunk-quality checks)
- `jinja2` → HTML reports, headless Chrome → PDF
- **SQLite**, one file per agency. Not Postgres — Postgres is for a multi-tenant web app with concurrent writers, which is a later problem and possibly never.
- Playwright behind a feature flag, only for the "does this site need JS to render its content" check.

Python over TypeScript specifically for `extruct` + `trafilatura`. The JS equivalents are weaker and reimplementing content extraction is a tarpit.

### The data model

Two rules carry the whole design:

**1. Append-only runs.** Never update a client's "current state." Every execution writes a new immutable `Run` with its findings. Monitoring is `diff(run_n, run_n-1)`; trends are a `GROUP BY` over run timestamps. This matters because **the retainer product *is* the time series.** Storing current state and computing deltas ad hoc leaves nothing to sell in month two.

**2. Raw-first, derived-later.** Store every raw HTTP response and raw LLM completion verbatim, content-addressed on disk (`raw/<sha256>`), with the hash referenced from the database. Then parse. Every derived row carries a `parser_version`.

Two payoffs: when the mention detector improves, reparse 18 months of history instead of starting over; and when a client asks "prove ChatGPT didn't mention us," the actual response text is on hand.

The core contract, which everything else depends on:

```python
class Finding(BaseModel):
    check_id: str          # "robots.blocks_oai_searchbot"
    severity: Severity     # blocker | warning | info
    title: str
    evidence: dict         # what was observed, raw
    fix: Fix | None        # proposed remediation, never auto-applied
    raw_ref: str | None    # sha256 of the artifact this came from
```

If `Finding`, `Fix`, and `Run` are right, every module above them is independently replaceable.

### Module structure

```
geo/
  fetch/        # THE only network layer: rate limiting, caching, user-agent, robots respect
  checks/
    crawl.py    # robots.txt per bot group, sitemaps, status codes, canonicals, JS-dependence
    schema.py   # extruct → validate LocalBusiness / Organization / FAQPage, NAP consistency
    content.py  # chunkability: heading structure, self-contained answers, Q-shaped headings
  citations/    # the query runner
  fixes/        # generators → proposed artifacts, never writes to a client site
  report/       # pure function of stored data
  store/        # SQLite + content-addressed raw blobs
```

Two invariants keep this from rotting:

- **Nothing outside `fetch/` calls `httpx`.** That single seam makes the test suite offline and instant, and prevents accidentally hammering a client's site.
- **Checkers implement one interface and self-register:**

```python
@register("robots")
def check(ctx: SiteContext) -> list[Finding]: ...
```

Adding a check becomes one new file with no wiring. The check library will grow past 40 entries; design for that from day one.

### The citation tester

The load-bearing component, and the one most likely to sink the product. Design requirements:

**Sample, don't measure once.** Ask "best dentist in Orange CA" five times and get five different answer sets. The primitive is a **mention rate over N samples** (N ≥ 5) per `(query, engine, run)` — not a boolean.

**Locality is a first-class variable.** These are local queries; results depend on where the query originates. Encode the city in the query text *and* pin the geo parameter wherever a SERP vendor is used. Store both on the query record — otherwise a routing change is indistinguishable from a real trend.

**Know what each engine can actually give you.**

| Surface | Access | Notes |
|---|---|---|
| Perplexity | Sonar API | Returns citations / `search_results` — genuinely measurable |
| ChatGPT | OpenAI API w/ web search | Measurable, but a *different distribution* than the consumer product |
| Google AI Overviews | No API | SERP vendor (SerpApi, DataForSEO) or omit — do not fake it |

**Mention detection: deterministic first.** Match on domain, brand name, phone, and address with normalization. An LLM extractor is a versioned fallback for genuinely fuzzy cases only. Never make a model call to answer "does this string contain the client's domain."

### The fix generator

Two non-negotiable guardrails:

**It cannot invent facts.** Generation is constrained to a per-client `facts.yaml` filled in from the intake call — hours, address, services, licenses, prices. Missing fact → emit `[NEEDS INPUT]` and refuse.

**Generated JSON-LD is validated programmatically before a human ever reads it** — required properties present, types resolve against schema.org, then a Rich Results check. Models invent plausible-looking schema properties constantly.

Output is always a file staged for review — `proposed/robots.txt`, `proposed/schema.jsonld`, `proposed/faq-draft.md`. **No write path to a client's site exists in the codebase.**

### Per-client configuration as code

```
clients/smiles-dental/
  client.yaml     # domain, competitors, CMS, contacts
  facts.yaml      # ground-truth business facts (the only source generation may use)
  queries.yaml    # test queries + locality
  proposed/       # generated fixes awaiting review
```

Git-tracked, diffable, reviewable. This is the scalability split made literal: **the YAML is the judgment layer, the code is the mechanics.** Onboarding client #12 is "copy a directory and write the queries."

### Operations

**Scheduling:** cron or a CI workflow invoking the CLI. Do not build a scheduler.

**Reporting:** Jinja → HTML, headless Chrome → PDF. Deterministic, no dashboard required. A client-facing dashboard is a v2 that reads the same store.

**Testing:** golden fixtures. Real saved HTML from a dozen local business sites, asserted findings. Recorded LLM responses for the citation tester. Zero network in tests. These findings are the product — a false positive on a client's site costs more than a crash would.

### Build order

Sequenced by risk, not by ease:

1. **Spike the citation tester alone, against three real local businesses.** If AI-answer visibility can't be measured with a signal stable enough to show a week-over-week trend, the monitoring retainer has nothing to sell and the audit is a one-time novelty. This is a go/no-go on the business model and it is cheap to find out.
2. **Build one vertical slice by hand** — robots.txt check → `Finding` → stored `Run` → rendered HTML report — on one real client site. A contract written before a slice exists is usually wrong; this validates it against real use.
3. **Freeze the contract**, then build out the remaining checkers against it.
4. **Add the fix generator** with its validator and `facts.yaml` constraint.
5. **Wire scheduling and trend reporting** — the retainer deliverable.

Note on step 1: it is the reverse of the intuitive order (easy mechanical checks first, hard measurement last). Do it anyway. The content generator is a prompt plus a validator — a known quantity. The measurement problem is the actual unknown.
