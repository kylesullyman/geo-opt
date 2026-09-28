# geo

AI search visibility audits for local businesses. Design: see `GEO Service Overview.md`.

## Setup

    uv sync
    # Browser capture needs no .env. For APIs later: uv run geo setup-api openai
    uv run pytest               # offline, ~2s

Optional JS rendering check: `uv sync --extra js && uv run playwright install chromium`, then `geo audit --js`.

## Start with actual ChatGPT browser answers (no API key)

    uv run geo browser prepare smile-doctors-orange
    # Open the printed capture.html in your normal signed-in browser.
    # Ask each exact question in a fresh ChatGPT conversation, paste the complete
    # answer and its cited URLs, save samples, and download the capture JSON.
    uv run geo browser import smile-doctors-orange /path/to/downloaded-capture.json
    uv run geo report smile-doctors-orange

Capture pages for all three local prospects are already prepared. See
[BROWSER_CAPTURE.md](BROWSER_CAPTURE.md) for links and the collection protocol.
This is a guided copy/paste workflow, not an unattended scraper. No login tokens
are read, copied, or stored by GEO. Browser observations use `chatgpt_browser` as
their own engine and do not get pooled with OpenAI API samples.

## Enable API sampling later (optional)

    uv run geo setup-api openai        # hidden key prompt; private GEO_HOME/.env
    uv run geo validate smile-doctors-orange
    uv run geo citations smile-doctors-orange -e openai --dry-run
    uv run geo citations smile-doctors-orange -e openai -q discovery-1 -n 1

Setup does not make a paid request. The last command does. The existing API
adapters remain available; use `perplexity` or `google_aio` with `setup-api` for
those providers. API usage is separate from your ChatGPT subscription, and
OpenAI API results are labeled separately from ChatGPT browser observations.

## Workflow

    geo init smiles-dental --name "Smiles Dental" --domain smilesdental.com --city Orange
    # edit clients/smiles-dental/{client,facts,queries}.yaml  (the judgment layer)
    geo validate smiles-dental
    geo citations smiles-dental --dry-run        # see the call plan / cost before spending
    geo audit smiles-dental                      # site checks + citation sampling -> one immutable Run
    geo citations smiles-dental                  # just the query tester
    geo fix smiles-dental                        # proposed/robots.txt, schema.jsonld, faq-draft.md
    geo report smiles-dental --last 3 --pdf      # pure function of stored runs
    geo diff smiles-dental --fail-on-regression  # exit 3 on regressions (cron alerting)
    geo trend smiles-dental
    geo runs | show <run> --answers | raw <sha> [--meta] | reparse <client> | checks | clients

Global flags: `--home DIR` (or `GEO_HOME`), `--json`, `--quiet`.

Audits save website results even when citation engines are unavailable. When citations
are requested, unavailable/partial/failed sampling returns exit 1; `--no-citations`
explicitly runs only website checks. JSON output includes `meta.citation_status`,
skipped engines, and check coverage. Reports label unmeasured AI visibility and
inaccessible pages. Exit 0 means execution completed, not that the site has no findings.

Three public-source prospect profiles near 92867 are in `clients/`: `orange-empire-auto-repair`,
`smile-doctors-orange`, and `queen-isabellas-bakery`. Each has three localized discovery
queries with five samples per engine. See `LOCAL_AUDIT_REVIEW.md` for the test results
and remaining validation work. These profiles have not been verified with the owners.

## Cron example

    0 6 1 * *  cd ~/agency && uv run --project ~/geo geo -q audit smiles-dental --fail-on-regression || notify "smiles-dental regressed"

## Layout

    src/geo/
      cli.py         the only entry point
      models.py      Finding / Fix / Run / CitationSample / Rate (the contract)
      fetch/         the ONLY network layer (rate limit, memo, robots, raw capture, secret redaction)
      robots.py      RFC 9309 parser (not urllib.robotparser)
      bots.py        retrieval vs training crawler taxonomy
      checks/        @register'd checks: crawl.py, schema.py, content.py
      schemaorg.py   validator against vendored schema.org vocab (scripts/build_schema_vocab.py)
      citations/     engines (Perplexity, OpenAI, SerpApi AIO), deterministic mention detector, runner
      fixes/         robots / schema / faq generators, facts.yaml only, [NEEDS INPUT] on gaps
      diff.py        run diffs + trends
      report/        Jinja HTML + headless Chrome PDF
      store/         SQLite (append-only triggers) + content-addressed raw/<sha256>
