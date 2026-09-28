# CLI review and 92867 test audits

Run date: September 22, 2026. Three public-source prospect profiles, two key pages
per business, ordinary crawler requests, no changes to any business website.

## CLI changes made

- Fixed a live sitemap-check crash on `Sitemap: /sitemap.xml`. Relative URLs are
  resolved for inspection and flagged for correction; malformed/non-HTTP URLs
  produce findings instead of crashing. Sitemap parser version is now `sitemap-2`.
- Audits and citation runs now return exit 1 when requested citation sampling is
  unavailable, partial, or failed, while preserving collected evidence. Explicit
  `--no-citations` audits can still complete successfully. Execution errors take
  precedence over regression exit codes.
- JSON output includes run metadata. HTML reports show citation status, skipped
  engines, check errors/coverage, business address, and profile provenance. A page
  that could not be fetched is explicitly marked unassessed for content/schema.

Validation: 67 offline tests passed, including missing-key behavior, failed API
samples, relative sitemap URLs, and malformed/non-HTTP sitemap URLs. The live
bakery audit was rerun after fixing the crash; the original run remains immutable.

## Business profiles and results

| Business | Address (Orange, CA 92867) | Latest audit | Findings |
| --- | --- | --- | --- |
| [Orange Empire Auto Repair](https://orangeempireautorepair.com/contact/) | 1188 N Batavia St | `20260922T212907Z-2d2b8a18` | 2 blockers, 0 warnings, 2 notes |
| [Smile Doctors Dentistry & Implants](https://smiledoctorsorange.com/our-office/) | 1042 N. Tustin Street | `20260922T212907Z-37610080` | 0 blockers, 1 warning, 5 notes |
| [Queen Isabella's Bakery](https://queenisabellasbakery.com/) | 125 N Tustin St | `20260922T213001Z-e48a7d69` | 0 blockers, 1 warning, 4 notes |

These counts are diagnostic findings, not measured rankings or business quality
scores. All latest runs completed nine checks with zero check crashes; the
opt-in WAF probe was skipped. Browser rendering was not enabled. Checks that
require HTML had no usable pages for Orange Empire, so its small finding count
does not imply a clean site.

### Orange Empire Auto Repair

Both `/` and `/contact/` returned HTTP 403 to GEOAudit. `robots.txt` and
`sitemap_index.xml` were readable; robots did not declare that sitemap. Prioritize
understanding the access restriction before assessing content or business markup.
This does not prove Googlebot, ChatGPT, or Perplexity are blocked. No bot impersonation
or access-control bypass was attempted. Address/phone came from the official
contact page in search results; content could not be independently fetched by the CLI.

### Smile Doctors Dentistry & Implants

Both pages returned HTTP 200. Dentist markup is present, with consistent business
name/address. The business node lacks top-level telephone, geo, priceRange, and
openingHoursSpecification. Its phone is nested in PostalAddress and it does have
`openingHours`: the hours recommendation is an opportunity to use the more
explicit structure, not evidence that hours are absent. Opening hours also differ
between homepage prose, office-page hours, and structured data; reconcile with
the owner before proposing replacement markup. The structured-data mismatch in
hours is a manual observation, not currently detected by the CLI.

The two sampled pages lack question-shaped headings, and the homepage has three
H1s. These are content heuristics, not proof of poor AI visibility. The site has a
FAQ section outside the two sampled pages, so the warning does not mean the
entire site lacks FAQs. The XML sitemap exists but is not declared in robots.txt.

### Queen Isabella's Bakery

Both pages returned HTTP 200 and include Bakery markup. `robots.txt` declares a
relative sitemap URL; use `https://queenisabellasbakery.com/sitemap.xml` instead.
The resolved sitemap itself returned HTTP 200. Google documents that sitemap
directives must use [fully qualified URLs](https://developers.google.com/crawling/docs/robots-txt/create-robots-txt).
Business markup lacks telephone, url, and geo; heading levels skip on both pages.
No telephone was verified from the official pages, so the profile leaves it
unknown rather than borrowing an unverified directory number. Operating hours
were also left unset pending owner confirmation: the homepage says closed Monday,
while the indexed menu page had said open daily.

## AI visibility remains unmeasured

`OPENAI_API_KEY`, `PERPLEXITY_API_KEY`, and `SERPAPI_API_KEY` were absent from this
project's environment. Each audit attempted its configured citation phase,
recorded all three engines as skipped, saved its website results, and exited 1.
There were zero paid API calls and no mention-rate results. Missing samples must
not be interpreted as 0% visibility.

Each profile has three non-branded discovery queries pinned to Orange, California,
with five samples per engine: 45 planned sample jobs per business, 135 total for
all three engines. This is a job count, not a dollar estimate; retries and Google
AI Overview follow-ups can add HTTP requests. After adding the desired keys to
`.env`, validate and run a selected engine, for example:

```sh
uv run geo validate smile-doctors-orange
uv run geo citations smile-doctors-orange --dry-run -e perplexity
uv run geo citations smile-doctors-orange -e perplexity
uv run geo report smile-doctors-orange
```

## Remaining CLI work before treating this as production-ready

1. Validate real provider payloads, model access, parser behavior, billing, and
   repeated measurements on multiple days. A one-day run cannot establish trend
   stability. This is still the main product-validation gap.
2. Expand coverage beyond two key pages, add competitor configurations, and retain
   real-site golden fixtures. Content heuristics need tuning across more sites.
3. Snapshot client config/facts/query definitions with runs so later profile edits
   cannot silently change the context of an old report. Reports currently read the
   current profile; raw responses and findings themselves are immutable.
4. Harden CLI input validation: unknown engine/query filters currently can select
   no jobs; unknown engines in `validate` are warnings. `init` interpolates YAML
   strings directly, so names containing YAML punctuation need safe serialization.
5. Directory NAP comparison and cross-source hours reconciliation remain manual.
   JavaScript rendering and real WAF behavior remain unverified.

## Saved artifacts

- Profiles: `clients/{orange-empire-auto-repair,smile-doctors-orange,queen-isabellas-bakery}/`
- HTML reports: `data/reports/<client>/<client>-<run-id>.html`
- Immutable runs: `data/geo.db`
- Raw HTTP evidence: `data/raw/<sha256>`

`data/` is gitignored and needs separate backup. No commits were created.
