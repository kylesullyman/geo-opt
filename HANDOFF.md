# Handoff: geo CLI

## Resume here — September 23, 2026

**Next task: build the standalone ChatGPT browser automation runner. It is NOT
implemented yet.** The user approved this with “Build that,” then interrupted
the turn to request this handoff. That turn only read the existing source and
package configuration; no browser-runner code or dependency changes were made.

The user's goal is measuring and profiling businesses' visibility in **actual
ChatGPT answers**. Website readiness checks support the service but are not its
main deliverable. Do not substitute Codex or OpenAI API answers and label them
as consumer ChatGPT results. Start with the signed-in browser workflow; preserve
the easy API-key option for later. No further product-choice confirmation is
needed to begin the authorized runner implementation.

### Current state

- Python 3.12 / uv CLI, entry point `src/geo/cli.py`.
- Live website audits and profiles exist for three Orange businesses in 92867.
- Manual ChatGPT capture preparation/import is implemented and tested.
- Last completed test run: **86 passing**, including a simulated-DOM JavaScript
  smoke test. Tests were not rerun solely for this handoff.
- **Zero real ChatGPT browser observations collected. Zero live citation API
  requests validated.** Do not confuse fixture results with real visibility.
- Browser UI testing previously hit pending macOS Accessibility/Screen Recording
  permissions through Computer Use. No connected browser surfaces were listed.
  This does not establish whether a new Playwright browser can launch; check it
  independently. The ChatGPT website's login state has not been verified.
- `codex login status` reported “Logged in using ChatGPT” earlier. This proves
  Codex authentication only, not a reusable browser login or API credential.
- Last key check found no OpenAI/Perplexity/SerpApi keys. Recheck availability
  without printing secrets if needed; do not require keys for browser capture.
- Entire project is untracked in git. Preserve existing work; do not reset it.
  `.DS_Store` is also untracked and unrelated. No commits have been made.

### Existing implementation to reuse

| File | Purpose |
| --- | --- |
| `src/geo/citations/browser.py` | `Observation`/`Capture` validation; `prepare`, `load_capture`, `make_run` |
| `src/geo/cli.py` | `geo browser prepare`, `geo browser import`, `geo setup-api` |
| `src/geo/report/templates/capture.html.j2` | Local manual worksheet, save/download/restore |
| `src/geo/report/templates/report.html.j2` | Browser observation conditions and surface labeling |
| `src/geo/citations/engines.py` | API adapters and import-only `ChatGPTBrowser` parser |
| `src/geo/citations/__init__.py` | Sampling and snapshot-aware reparse |
| `src/geo/store/__init__.py` | Append-only SQLite runs and content-addressed raw evidence |
| `src/geo/fetch/render.py` | Existing optional Playwright site renderer; not a ChatGPT runner |
| `tests/test_browser.py`, `tests/capture_page_smoke.cjs` | Import, evidence, validation, setup and worksheet tests |

Browser engine is `chatgpt_browser`; OpenAI API is `openai`. The API answer parser
is `answer-2`, which excludes retrieved-but-uncited OpenAI/Perplexity sources
from citation counts. Full provider responses remain in raw evidence.

### Authorized browser-runner behavior

Build a CLI-operated runner, preferably Playwright, using visible browser mode
and page structure/accessibility locators. The user explicitly requested this
method. Screenshots may supplement evidence or diagnostics, but should not be
the primary extraction mechanism.

1. Establish a supported browser connection/session. Determine whether to attach
   to an explicitly enabled local browser connection or use a dedicated persistent
   profile with one-time manual login. Do not claim the normal browser's login
   transfers automatically. Do not copy tokens, use private ChatGPT endpoints,
   or disable security protections to make attachment work.
2. Read the prepared query plan and run sequentially. Start a fresh conversation
   per sample, submit the exact prompt, and avoid target-business steering.
3. Detect response completion using observed DOM/control state and bounded waits;
   do not rely on a single fixed sleep or capture partial streaming text.
4. Capture the assistant answer and its actual citation URLs. Inspect source UI
   as needed without treating all retrieved sources as citations. Verify selectors
   against the real UI rather than claiming guessed selectors are tested.
5. Capture timestamp, visible model label, search/session/personalization state
   where observable. Use `unknown` for hidden conditions. Browser location is not
   pinned by the existing query locality object.
6. Checkpoint after each sample. Resume without silently resubmitting prompts
   after ambiguous failures. Record in-flight state/conversation identity so a
   crash between submission and capture can be reconciled.
7. Pause with an actionable message for login, usage limits, challenges, selector
   drift, or extraction uncertainty. Never turn these into negative mentions.
   Leave challenges to the user; do not bypass them.
8. Feed completed captures through the existing validation/import pipeline, then
   generate the report. Retain manual capture as a fallback and APIs as optional.

Suggested CLI shape (design proposal, **not existing commands**):

```sh
uv run geo browser login
uv run geo browser run smile-doctors-orange --batch <batch-id>
uv run geo browser run smile-doctors-orange --batch <batch-id> --resume
```

### Important integration details

- Prepared files live at `data/captures/<client>/<batch-id>/plan.json` and
  `capture.html`. The plan includes exact prompts, zero-based sample indices,
  query locality, and a profile/facts/query snapshot.
- Capture format is `geo-chatgpt-browser-1`. See `Observation` for required
  fields, including `citations_reviewed` and `complete`. Do not silently set a
  human-review flag merely because an extractor ran: add honest capture-method
  and citation-verification metadata, preserving compatibility with manual data.
- Current surface/report text explicitly says “manually captured.” Update this
  accurately for automated captures, preserving per-observation provenance.
- Import saves an immutable run with ID `browser-<batch-id>`. A batch may be
  finalized only once; partial imports require `--allow-partial` and return exit 1.
  Keep incremental checkpoints separate from imported runs until finalization.
- Failed observations have no answer/citations and are excluded from denominators.
  Missing sample slots are not automatically negative results.
- Snapshots govern browser mention detection and reparse. Reports currently use
  the latest citation run for their main rate table; trends separate engine names.
- `data/` and `.env` are gitignored. Protect persistent browser profiles too:
  they contain session credentials and must stay local/private and untracked.
- Keep network/browser execution behind a clear module boundary; existing direct
  HTTP requests belong in `fetch/`. Do not introduce outbound traffic in tests.

### Implementation and verification sequence

1. Read `BROWSER_CAPTURE.md`, the modules above, and applicable instructions.
2. Check Playwright/browser availability. `pyproject.toml` already has optional
   extra `js = ["playwright>=1.47"]`; earlier installation was unverified.
3. Implement runner state/checkpoints, UI adapter, CLI, and provenance changes.
4. Test completion detection, citation extraction, error/limit handling, interrupted
   resume, duplicate prevention, and import compatibility with local fixtures.
5. Run the existing suite. Last command used: `uv run pytest`.
6. Validate one real question end to end, manually compare captured answer and
   URLs, then test a small resumed batch. Do not launch all 45 samples before the
   extraction is proven. If login/permissions block this, finish testable code and
   clearly report which live checks remain unverified.
7. Update README, BROWSER_CAPTURE.md, and this handoff with actual commands/results.

Current environment changed to **workspace-write with restricted network** during
the interruption. Writes outside permitted roots, dependency downloads, and GUI
launches may need tool escalation. Follow the active permission policy; do not
assume the earlier unrestricted environment still applies. Avoid delegating to
subagents unless the user or applicable instructions explicitly request it.

### Existing local businesses and capture batches

| Slug | Prepared batch (15 samples each) |
| --- | --- |
| `smile-doctors-orange` | `20260923T202439Z-fd01e1b8` |
| `queen-isabellas-bakery` | `20260923T202439Z-7c2d022e` |
| `orange-empire-auto-repair` | `20260923T202438Z-574f9444` |

Each profile has three localized discovery questions, IDs `discovery-1` through
`discovery-3`. The test fixture `smiles-dental` uses different query IDs.
Website audit findings/evidence and limitations are in `LOCAL_AUDIT_REVIEW.md`.
The auto shop returned HTTP 403 to the site auditor; this does not prove it is
absent from ChatGPT. The other two sites were readable.

Working manual/API commands (already implemented):

```sh
uv run geo browser prepare smile-doctors-orange -q discovery-1 -n 1
uv run geo browser import smile-doctors-orange /path/to/capture.json
uv run geo report smile-doctors-orange
uv run geo setup-api openai  # optional hidden prompt; no paid request
uv run geo citations smile-doctors-orange -e openai --dry-run
```

## Historical session notes

The sections below preserve earlier implementation context. Their older test
counts, next-step ordering, and unverified-site statements are superseded by the
resume section above.

## Update: ChatGPT browser workflow, September 23, 2026

`geo browser prepare` builds a local capture worksheet and plan snapshot;
`geo browser import` validates and saves the downloaded observations as immutable
`chatgpt_browser` citation runs. Prepared batches for all three businesses are
linked in `BROWSER_CAPTURE.md`. This is guided manual copy/paste, not a scraper.
Reports include model/search/session metadata and do not claim pinned browser
location. Partial imports are explicit and batches cannot be imported twice.
`geo setup-api <engine>` enables future APIs through a hidden prompt/private .env.
The answer parser is now `answer-2`: retrieved OpenAI/Perplexity search sources no
longer count automatically as answer citations. Raw provider responses retain them.
All 86 tests pass, including a simulated-DOM worksheet smoke test. Live browser
verification was blocked by pending macOS Computer Use permissions.

## Update: local-business validation, September 22, 2026

See `LOCAL_AUDIT_REVIEW.md` for three real 92867 profiles, live findings, and
remaining work. Tests now total 67 passing. Fixed relative/malformed sitemap
handling (`sitemap-2`), added explicit citation status and incomplete-run exit 1,
and exposed missing coverage in JSON/HTML reports. Nine site checks completed
without crashes in the latest runs for all three businesses. Orange Empire's
pages returned 403, limiting content/schema coverage. Real citation API calls
remain unverified because no engine keys were configured. The older status
below records the previous session.

Last session: 2026-09-22. Read `GEO Service Overview.md` first (the design spec). This file covers what exists, what's verified, what isn't, and what to do next.

## Status in one paragraph

The CLI portion is built: a Python 3.12 / uv package in `src/geo/` with a `geo` entry point. There are 61 tests and all pass offline in about 2s (`uv run pytest`). The tool ran end to end against a live site (www.python.org, site checks only): audit, stored run, diff, trend, HTML report, PDF via headless Chrome, and staged fixes. **The citation tester has never hit a real API.** It is tested only against recorded responses I built from the providers' docs. That is the design doc's step 1 (the go/no-go on the business) and it is still open.

## Commands

```
geo init <slug> --name ... --domain ... --city ...   scaffold clients/<slug>/
geo validate <slug>          lint config, show which engine keys are set
geo checks | clients         list registered checks / configured clients
geo audit <slug>             site checks + citations -> one immutable Run
      --no-citations --only X --skip X --probe-bots --js -e ENGINE -n N --ignore-robots --fail-on-regression
geo citations <slug>         query tester only  (--dry-run shows call plan; -q QUERY_ID, -e, -n)
geo runs <slug> | show <run> [--evidence --answers]
geo diff <slug> [a b] [--kind citations] [--fail-on-regression]    exit 3 = regression
geo trend <slug>
geo report <slug> [--last 3] [--run ID] [--pdf] [-o DIR]           no network
geo fix <slug> [robots schema faq] [--offline]                     writes clients/<slug>/proposed/
geo reparse <slug> [--run ID]    re-derive mentions from raw with current parser
geo raw <sha|prefix> [--meta]    print stored artifact verbatim / its provenance
Global: --home DIR (or GEO_HOME; default cwd), --json, --quiet. Reads <home>/.env.
```

Exit codes: 0 ok, 1 error, 2 usage, 3 regression.

## Layout and where each design rule lives

| Design rule | Where it lives |
|---|---|
| Finding/Fix/Run contract | `models.py` (+ `CitationSample`, `Mention`, `Locality`, `Rate`, `compute_rates`) |
| Only `fetch/` calls httpx | `fetch/__init__.py`; enforced by `test_fixes.py::test_no_write_path_to_client_sites` |
| Append-only runs | `store/__init__.py`: SQLite triggers abort UPDATE/DELETE on runs/findings/samples/artifacts |
| Raw-first | every response goes to `data/raw/<sha256>`, logged in the `artifacts` table with headers/params redacted |
| parser_version on derived rows | findings get `<check>-<version>`; samples get `answer-1+mention-1` |
| Reparse without mutation | `samples` PK is `(run_id, idx, parser_version)`; readers take the latest `derived_at` |
| Training vs retrieval bots | `bots.py`; `checks/crawl.py::robots` emits a separate finding per retrieval bot plus one info finding for training policy |
| Self-registering checks | `checks/__init__.py` `@register(name, version=, requires=)`; modules are auto-discovered |
| Rate, not checkmark | `compute_rates`: errors are excluded from n; "no AI Overview shown" counts in n (it's a real observation) |
| Locality first-class | `queries.yaml` locality is sent to each engine's location param and stored on every sample; `validate` warns if the city isn't in the query text |
| Deterministic mentions | `citations/mentions.py`: domain / name+aliases / phone / street, normalized. No LLM fallback exists yet |
| Honest surfaces | each engine's `surface` string is rendered in the report |
| No invented facts | `fixes/*` read only `facts.yaml` (strict pydantic, `extra="forbid"`); gaps become `[NEEDS INPUT]` and the artifact is marked NOT READY |
| Validate generated JSON-LD | `schemaorg.py` validates against the vendored official vocab `data/schemaorg_vocab.json` (rebuild: `scripts/build_schema_vocab.py`) |
| No write path to sites | `fix` only writes `clients/<slug>/proposed/` |

Other decisions:
- `robots.py` is a custom RFC 9309 parser (exact-token UA match, longest-match wins, Allow wins ties, `*`/`$`). `urllib.robotparser` gets these files wrong. The fetcher uses it too, for our own crawler.
- A crashing check goes into `run.meta.check_errors` and `audit` exits 1. It never becomes a finding.
- The robots fix always emits an explicit allow group for retrieval bots. It keeps private-path Disallows from `*` (e.g. /wp-admin/), leaves training bots alone unless `client.yaml training_bots: allow|block`, and re-parses its own output to verify it.
- The FAQ generator uses deterministic templates, not an LLM, with a `<!-- facts: ... -->` trace per answer. The design allows LLM drafting later, as long as it stays behind this facts constraint plus human review.
- Default engine models are `sonar` and `gpt-5-mini`. These were **not verified against the account**. Override with `GEO_PERPLEXITY_MODEL` / `GEO_OPENAI_MODEL`.
- The Google AIO engine uses SerpApi `engine=google` and follows the `page_token` with `engine=google_ai_overview`. The two responses are merged into one blob so parsing stays a pure function of a single raw body.

## Verified vs not

Verified (real execution):
- 61 offline tests pass. Three mutation checks (robots tie-break, robots UA matching, phone matching) were each caught by the tests.
- Live audit on python.org, run twice. It found one real false positive (`query-input` flagged as an unknown property); that's fixed and tested. The live diff showed it as resolved. The missing-sitemap finding was confirmed by hand (404).
- PDF render via `/Applications/Google Chrome.app` works.

NOT verified:
- Any real Perplexity / OpenAI / SerpApi call: payload acceptance, response shapes, model names, rate limits, cost.
- The `--js` path (Playwright not installed; `uv sync --extra js && uv run playwright install chromium`).
- `--probe-bots` against a real WAF. It's a UA-only probe; real crawlers are verified by IP, and the finding's evidence says so.
- Google Rich Results Test: no API exists, so the tool tells the human to run it.
- Behavior on a real local-business site (WordPress, Wix, Squarespace). Only python.org has been tested live.

## Next steps, in the design doc's risk order

1. **Citation spike (go/no-go).** `cp .env.example .env`, add keys. Create 3 real Orange County clients (`geo init`) with honest `facts.yaml` + 3–5 queries each. Run `geo citations <slug> --dry-run` to see call counts, then real runs on several days. Question to answer: is the mention rate stable enough week to week to show a trend? Expect to fix engine parsing on first contact with real responses; if parsing changes, bump `ANSWER_PARSER_VERSION` / `PARSER_VERSION` and run `geo reparse`.
2. **Real-client vertical slice.** Run `geo audit` on one real local business site; review every finding for false positives. Add that site's saved HTML as a golden fixture (`tests/fixtures/sites/`) with asserted findings. The design wants about a dozen of these.
3. **Freeze the contract** (`models.py`) once 1–2 hold up, then grow the check library.
4. Optional LLM fallback extractor for fuzzy mentions: versioned, used only when deterministic matching is ambiguous.
5. Cron/CI wiring (README has a cron example). Do not build a scheduler.

## Known gaps / small TODOs

- `hosts` check: `hosts.no_https_redirect` only fires if an http variant serves 200 without redirecting; it doesn't detect broken https certs specifically.
- Content checks are heuristics (thresholds: 250 main-content words, 400-word sections); evidence marks them `heuristic: true`. Tune on real sites.
- NAP checks compare the site to `facts.yaml` only. There's no directory (Yelp/GBP) cross-check yet.
- `fix` without `--offline` fetches robots.txt and logs it under a pseudo run id `fix-<id>`. It doesn't create a Run row.
- Possible macOS prompts when headless Chrome runs (Keychain "Chrome Safe Storage"). If it's a nuisance, add `--user-data-dir=<tmp>` and `--use-mock-keychain` in `report/__init__.py::html_to_pdf`.
- Nothing is committed yet (`git init` done; `.gitignore` excludes `data/` and `.env`). Suggest a first commit before the spike.

## Test harness notes

- `tests/conftest.py::FakeWeb` is an in-memory internet installed via `geo.fetch.DEFAULT_TRANSPORT`. Plain `http://` auto-redirects to https unless a route is registered as `("http:"+host, path)`. `web.api_responses(host, [...])` cycles through recorded bodies, and `(status, body)` tuples simulate errors.
- The rate limiter and sleeps are monkeypatched off in tests.
- The `home` fixture copies `tests/fixtures/clients/smiles-dental` into a tmp GEO_HOME and unsets API keys.
