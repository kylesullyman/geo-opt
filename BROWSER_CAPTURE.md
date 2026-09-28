# ChatGPT browser capture

This is the initial visibility workflow: actual answers from your signed-in
ChatGPT browser, captured through a local worksheet and imported into GEO.
No API account or key is needed. Your ChatGPT usage limits still apply.

## Ready-to-use capture pages

Each batch has three questions with five independent samples per question:

- [Smile Doctors](data/captures/smile-doctors-orange/20260923T202439Z-fd01e1b8/capture.html)
- [Queen Isabella's Bakery](data/captures/queen-isabellas-bakery/20260923T202439Z-7c2d022e/capture.html)
- [Orange Empire Auto Repair](data/captures/orange-empire-auto-repair/20260923T202438Z-574f9444/capture.html)

Open a page in your normal browser and keep it beside ChatGPT. These files and
their plan.json manifests live in gitignored `data/`; back them up with geo.db.

## Collect a sample

1. Select the question/sample on the capture page and copy the question.
2. In ChatGPT, start a fresh conversation for every sample. Prefer a temporary
   chat when available. Keep model, search mode, and personalization consistent
   across the batch. Record the settings actually used; do not guess hidden state.
3. Ask the exact prepared question. Do not include the target business name,
   mention the audit, upload the profile, or add instructions favoring a business.
   Do not ask follow-ups or regenerate until a preferred answer appears.
4. Wait for completion, then copy only the complete assistant answer into the
   worksheet. Do not paste the user prompt, sidebar, or unrelated conversations.
5. Inspect the answer's citation links and paste their full destination URLs,
   one per line. A text copy may omit links. Do not count every retrieved search
   result as an answer citation. An answer with no citations is valid: leave the
   list empty and check the review checkbox.
6. Record the model label as displayed (or `unknown`), whether search visibly
   occurred, conversation type, personalization state, and observation time.
   For temporary chats, leaving conversation URL blank is fine. Do not create a
   public share link just to capture evidence.
7. Save the sample. If ChatGPT fails or reaches a limit, record the failure reason
   with an empty answer/citation list. Failed attempts are excluded from rates.
8. Download the capture JSON. You can resume with the file picker on the same
   worksheet. Saved samples also use local storage when your browser allows it;
   unsaved fields are not exported. Never rely on local storage as your only backup.

This is manual capture, not background browser scraping or an OAuth integration.
It records what your account saw under the recorded conditions. Browser location
is not pinned; locality is expressed in the query. Five samples are an exploratory
baseline, not a statistically precise measure of what all ChatGPT users see.

## Import and report

From the GEO project directory, replace the example path with the downloaded file:

```sh
uv run geo browser import smile-doctors-orange /path/to/downloaded-capture.json
uv run geo report smile-doctors-orange
uv run geo runs smile-doctors-orange
uv run geo show <run-id> --answers
```

Import validates client, exact prompts, sample IDs, timestamps, citation URLs,
completion and review flags. Duplicate slots and repeat imports are rejected.
The original downloaded JSON, plan snapshot, and per-answer evidence are saved
by hash. The profile snapshot at preparation time is used for mention detection
and re-parsing. Reports identify the engine as `chatgpt_browser`, show observation
conditions, and distinguish browser observations from API measurements.

Finish the batch before import. To deliberately finalize fewer samples, add
`--allow-partial`. It saves an incomplete run and exits 1; missing slots do not
become negative observations. You cannot append to that batch after importing.
Prepare a new batch for future collection. Do not re-import old answers under a
new batch to inflate the sample count.

For a small pilot or a new collection day:

```sh
uv run geo browser prepare smile-doctors-orange -q discovery-1 -n 1
```

## API option later

```sh
uv run geo setup-api openai
uv run geo validate smile-doctors-orange
uv run geo citations smile-doctors-orange -e openai --dry-run
# This next command makes a paid API request once billing/key access is active:
uv run geo citations smile-doctors-orange -e openai -q discovery-1 -n 1
```

`setup-api` prompts without echoing the key, preserves other `.env` settings,
and saves the file with owner-only permissions. It makes no API requests.
An already-exported environment variable takes precedence over `.env`.
For this project `.env` is gitignored. Other providers can be configured with
`setup-api perplexity` and `setup-api google_aio`.

API answers and ChatGPT web observations are different measurement surfaces.
Reports and trends keep their engine labels separate. The report's main rate
table displays the latest citation run; earlier runs remain available via `show`
and `trend`. Live API payload/model/account validation is still pending.

## Verification status

Offline tests cover capture preparation, import validation, duplicates, partial
and failed observations, immutable evidence, snapshot-based re-parsing, report
labels, and private API-key setup. All 86 tests pass, including a JavaScript
save/download/restore smoke test using a simulated DOM. No real ChatGPT observations were collected
during setup: macOS Accessibility/Screen Recording permission was still pending,
preventing browser interaction and visual verification. The manual workflow does
not require giving GEO those permissions; you can use the worksheet yourself.
