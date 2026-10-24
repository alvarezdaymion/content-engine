# AGENTS.md

The content engine ranks video ideas from collected evidence, plans one recording session at a time as question cards, matches recorded takes to the cards, builds the cut, captions, graphics and post texts, and publishes approved items once per platform through a ledger.

If you were asked to set it up for someone, follow [SETUP.md](SETUP.md). The rules below are for changing the code.

## Layout

- `config.py`: config schema and defaults; `templates/content-engine.example.toml` mirrors it.
- `ideas/`: `evidence.py` (store), `filing.py` (ideas.toml and keyword filing), `rank.py` (the score, pure), `sources/` (one module per source; `base.py` defines the `Source` protocol).
- `session.py`: the session model and its rules. `cards.py`, `matching.py`, `trims.py`, `captions.py`, `overlay.py`, `carousel.py`: pure functions. `pipeline.py` runs match and edit; `review.py` writes the review page and records approvals; `render.py`, `tts.py` and `transcript.py` wrap the optional tools.
- `publish/`: `ledger.py` (events and states), `plan.py` (what to post today, what repair may retry; pure), `run.py` (the only code that calls connectors), `simulate.py` (dry runs), `platforms/` (one module per platform).
- `cli.py`: commands. `demo.py` and `demo_data.py`: the synthetic demo.

## Rules

- **The session is the unit of work.** Cards are created only by `session.plan`, inside a session. Keep at most one unrecorded session, match takes only against their own session's cards, and build the publish queue only from `session.queue`. Don't add a backlog, a calendar or any other path that plans or queues content outside a session.
- **One irreversible call per attempt, through `commit()`.** A connector does its preparation, then makes exactly one `commit(fn)` call for the request that makes the post exist. Reads after it are best effort (`best_effort`) and must not raise for a post that went through. Connectors never write the ledger and never decide what is retryable; `publish/run.py` classifies outcomes from the commit boundary.
- **Never weaken the ledger.** Done is final. Held (an unclear publish call, an orphaned commit, or a crash after the commit line) is never retried by code; only `reconcile` moves it. Repair retries only `failed`. The daily run never retries a failure and starts one item per day. Complete and fsync each write before continuing; recover only incomplete final records and stop on complete corruption. Hold the OS file lock for the whole run, without expiring or deleting it. Every rule has a test in `tests/test_ledger.py` or `tests/test_publish.py`; change the test only together with the README section that documents the rule.
- **Dry-run by default.** Live posting requires `[publish] mode = "live"` in the config. Don't add a flag or environment variable that turns it on. Dry runs use placeholder credentials and `publish/simulate.py`; they must not read real secrets or write the live ledger.
- **Only approved, unchanged items publish.** An approval stores `session.fingerprint` (take, segments, graphics, complete subtitle document, source and render bytes, hosted URL, post texts). Live publishing and repair require a successful render tied to the current presentation fingerprint and media digest. Reload `session.json` and verify persisted approval, fingerprint and artifacts against the prepared post before commit. Hold the session lock from this check through the irreversible request. Match, edit, approve and reject commands load, modify and save under that same lock. Session snapshots carry a content revision; saves reject stale snapshots, and matching/editing check it before touching artifacts. `_save` is only for callers already holding the lock and also checks the revision. Reload after a stale-state error; never merge an old approval into newer state. Instagram and Threads GET the hosted media and verify its complete bytes against the approved digest before creating containers. Hosted URLs contain that digest and must never be overwritten. Anything that changes what gets posted must be part of that fingerprint.
- **Pause applies during a run.** Check `state/PAUSED` before every platform attempt and immediately before the irreversible request, including after persisting the commit record. A request already sent may finish; its outcome still follows the ledger rules.
- **Math and timing in code.** Scores, matches, cuts and caption times are computed by pure functions and tested with synthetic transcripts. No model call decides a number.
- **Sources only read.** They send GET requests and scrub collected text (`textutil.scrub`).
- **Metered calls stay explicit.** ElevenLabs runs only through `content-engine audio` with `[tts] enabled = true`. Nothing metered runs in `demo` or in tests, and `run` reaches real platforms only in live mode.
- **Secrets** come from environment variables; `.env` is loaded explicitly with `uv run --env-file .env`. Never commit `.env`, `content-engine.toml`, `ideas.toml`, `data/`, `sessions/`, `state/` or `out/`, and never put tokens in errors, logs, the ledger or pages (`http.py` tracks request and refreshed credentials and scrubs reflected errors and URL queries).
- **CSV IDs** are scoped to the configured source name. Keep source names stable when refreshing an export.
- **The matcher owns take selection and supersession.** The pure matcher returns decisions for present transcripts and missing matched/superseded history. Newly discovered takes and corrected unmatched takes may replace the saved selection; filename order only breaks ties among eligible replacements. A missing selected take and its edit stay selected unless the matcher chooses a replacement. Restoring a known superseded take does not make it new, even when both transcripts are present. The pipeline only reads transcripts, attaches video paths, and persists the matcher’s decisions; it must not reselect takes, rewrite supersession or apply filename ordering.
- Runtime code is standard library only, on macOS or Linux (OS file locks use `fcntl`). faster-whisper is an optional extra and ffmpeg an optional tool; both are skipped cleanly when missing.

## Development

- Python 3.11+, `uv`. Run `uv run pytest` and `uv run ruff check` before a PR.
- Add a failing test with each behavior change. Network code is tested with a fake transport (`tests/conftest.py`'s `FakeHttp`); tests never call real APIs.
- Test data is synthetic: short fake IDs, example.com and example.org links, no real names, emails or phone numbers.
