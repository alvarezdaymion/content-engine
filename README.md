# Content engine

Content engine is a video pipeline for a creator or small business that answers audience questions on camera. Once a week you sit down for a recording session: an AI voice reads each question and you answer it like a real call. The engine decides what to record from what is working for other creators, competitor ads, the news and your own numbers. It writes the question cards, matches each recorded take to its card, trims it, adds word-by-word captions and graphics, and puts every cut on a numbered review page. Then it posts one approved reel a day to Facebook, Instagram, Threads, X, YouTube and TikTok through a ledger that never posts the same item to a platform twice, and turns each posted reel into carousel text.

Project page: [daymionalvarez.com/portfolio/content-engine](https://daymionalvarez.com/portfolio/content-engine)

## How it works

Evidence from pluggable sources is filed under the ideas you list in `ideas.toml` and scored by a plain formula (`ideas/rank.py`). `content-engine plan` opens a recording session and turns the top ideas into question cards. After you record, each take's transcript is aligned against the session's card questions to find its card and where the question ends. The answer is cut at long pauses and at phrases you mark, captions are built word by word from the transcript's timestamps, and graphics are anchored to phrases you say. Everything up to here is pure Python and runs without ffmpeg; ffmpeg only renders the final cut. Approval is by card number and covers exactly what you saw. Publishing goes through `publish/ledger.py`, which writes a line before and after the one irreversible call to each platform, so a crash or a timeout can never turn into a second post.

## Quickstart

Needs macOS or Linux, Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/alvarezdaymion/content-engine.git
cd content-engine
uv run content-engine demo        # synthetic data, no keys, no network, no ffmpeg
```

Then open `content-engine-demo/out/demo/review/index.html` and `content-engine-demo/out/demo/sheet.html`.

To use it for real, run `uv run content-engine init`, edit `content-engine.toml` and `ideas.toml`, copy `.env.example` to `.env` and fill in the credentials, then run `uv run --env-file .env content-engine check`. The CLI reads the process environment; it does not load `.env` itself. Include `--env-file .env` in every `uv run` command that needs credentials (ideas, audio, run, publish or repair), or export them in its environment. The scheduler examples load the same file explicitly. Or hand the repo to your coding agent and say "set up the content engine for me": [SETUP.md](SETUP.md) is written for it.

## What the demo shows

The demo runs the whole loop for a made-up bookkeeping studio and prints each step:

1. **Ideas ranked** from four synthetic sources (creator posts, competitor ads, news, own posts and comments) plus two questions "asked on calls". Each idea shows its score split into creators, market and ours. Words that keep showing up in unfiled evidence are listed as possible new ideas.
2. **A session planned** with four cards. The AI voice reads a recent call question instead of the idea's default question. Planning a second session is refused while this one is unrecorded.
3. **Takes matched** from transcripts: a clean take, an aborted take superseded by its retake, a take that started mid-question with a misheard word, and a pickup with no question that stays unmatched. Card 4 was not recorded, so its idea goes back into the ranking.
4. **Edits**: a false start cut by phrase, long pauses cut, word-by-word caption cues, one stat graphic and one callout, and post texts for every platform. Rendering is skipped because there is no video.
5. **Approved by number**: items 1 and 2.
6. **Dry-run publish** of item 1 to all six platforms against an in-process simulator. Facebook and X post, YouTube uploads as private, TikTok lands as a draft. Instagram's publish call times out, so it is **held** for a person to check. Threads fails before publishing, and repair fills it. A second run the same day posts nothing (one reel a day).
7. **Carousel text** for item 1, word for word from the approved caption.

## How it runs

Schedule `content-engine run` every hour (cron, launchd and systemd examples are in [examples/scheduling](examples/scheduling)). The weekly loop:

1. **Ideas.** Each tick runs the idea sources that are due (every 6 hours by default), files new evidence under your ideas, ranks them and writes `out/ideas.json`. Sources only read. A failing source keeps its earlier evidence and never stops the others.
2. **Plan.** `content-engine plan` opens the next session: the top ideas (8 by default) become cards, and the recording sheet is written to `out/<session>/sheet.html`. `content-engine audio` optionally has an AI voice read each question.
3. **Record.** Play each question and answer it on camera, with the question audible in the take. Put each video and its word-timestamp transcript in `sessions/<session>/takes/` (`content-engine transcribe` makes transcripts with a local Whisper model if you install the extra).
4. **Match.** `content-engine match` finds each take's card. Newly discovered retakes replace the saved take of the same card; filename order breaks ties among new takes. If a selected retake's transcript disappears, its match and edit stay selected until a new take replaces it. Superseded take identities remain in the session even when their files disappear. Restoring an older transcript cannot make it new or replace the selected retake, including when both transcripts are present. Keep each take's filename stable.
5. **Edit.** `content-engine edit` builds the cut, captions, graphics and post texts for every matched card and writes `out/<session>/review/index.html`. Add `--render` to render with ffmpeg. Cuts and graphics you want go in the session's `edit_notes` (see Configuration).
6. **Review.** `content-engine approve <session> 1 3`, or `reject <session> 2 --note "..."`.
7. **Publish.** The first tick after `[publish] post_after` posts one approved item to every enabled platform, then repair retries same-day failures that definitely did not post. The carousel text goes to `out/<session>/carousels/`, and the summary lists what needs you: a TikTok draft to finish, a private YouTube upload to publish, a held platform to check. When few approved reels are left, it says to record the next session.

## Why everything hangs off a recording session

Content tools tend to plan around an ideal process: calendars, briefs, scripts and approval chains. When the creator does not work that way, the plan goes stale and the tool goes unused. The habit that reliably happens is sitting down once a week and answering questions on camera, so the recording session is the unit of work and every stage hangs off one:

- Ideas are ranked all the time, but nothing is planned until a session pulls the top ideas in as cards. There is no card backlog and no content calendar.
- Only one session can be unrecorded at a time. You can re-plan it (`plan --replace`), not stack another on top.
- A session holds its ideas out of the ranking for 21 days. Once its takes are matched, the ideas of cards you didn't record go back.
- Takes are matched only against their own session's cards, review numbers are that session's card numbers, and the publish queue is built only from approved items of sessions, oldest session first.
- The only planning prompt the engine ever gives is "record the next session", when the queue runs low.

These rules live in `src/content_engine/session.py` and are tested in `tests/test_session.py`.

## The publish ledger

Every connector does its preparation (uploads, containers, processing waits) and then makes exactly one irreversible call through `commit()`: the call that makes the post exist. The runner writes an `attempt` line before anything is sent and a `commit` line right before that call, both fsynced, and classifies the outcome from the commit boundary rather than trusting each connector:

| What happened | Ledger state | What happens next |
|---|---|---|
| The platform confirmed the post | done | never sent again |
| Error before the publish call, or the platform refused it (HTTP 4xx) | failed | `repair` may retry it the same day, up to `max_attempts` |
| The publish call timed out, dropped, returned 5xx, 408 or 409, or anything broke after it | held | nothing retries it; a person checks the platform |
| The process died after `attempt` | failed | nothing that could post was sent |
| The process died after `commit` | held | it may have posted |
| A commit has no preceding attempt record | held | treat it as started; a person checks the platform |

Ledger appends finish every byte and fsync before continuing. An incomplete, unterminated final record is removed before the next append; a valid final record without a newline is preserved and separated from the next record. A malformed complete record or invalid event fields stop processing without publishing. Preserve the ledger and inspect corruption by hand; do not delete it to restart publishing.

`content-engine reconcile <item> <platform> --posted [--url URL]` or `--not-posted` records what you found on a held platform; not posted makes it eligible for `repair --item`. One item is started per day and a done platform is final. If a run is interrupted, or a platform is switched on, the next run first gives the last item to the platforms that never got it. A successful publish or repair writes the same carousel text artifact, including when every initial attempt failed. Dry runs use the same connectors against `publish/simulate.py` and their own ledger in `state/dry-run/`, so a rehearsal never touches the live ledger or a real account. `tests/test_ledger.py` and `tests/test_publish.py` cover each row of the table, including a crash inside the publish call.

## The idea score

Each piece of evidence adds points to one of three parts, halving in value every few days:

| Evidence | Points | Half-life |
|---|---|---|
| Creator post | lift(views / that creator's normal); 0.5 x (1 + log10(1 + views / 10,000)) when the normal is unknown | 7 days |
| Competitor ad | (1 + log2(days live / 14)) x (1 + 0.3 log2(versions)) | 30 days |
| Thread | 1 + log10(1 + replies and votes) | 7 days |
| News story | 2 | 3 days |
| Own post | 2 x lift(views / your median) | 30 days |
| Own comment | 2 | 30 days |
| Question asked on a call or in a message | 3 | 60 days |

lift(m) is 1 + log2(m) above 1x, capped at 32x, and 1 otherwise.

Several different creators on one idea multiply the creators part (up to 2x). The score is 100 x (1 - e^(-raw / 25)) x audience fit (1.0, 0.85, 0.6 or 0.3), so strong evidence approaches 100 instead of running away. Filing is keyword-based and deterministic: an item goes to the idea whose keywords cover the most of its words, ties go to the idea listed first. The full formula and its tests are in `src/content_engine/ideas/rank.py` and `tests/test_rank.py`.

## Connectors

| Connector | Status | What it does |
|---|---|---|
| YouTube Data API | Real, read-only | Searches your queries, measures each video against its channel's normal (`YOUTUBE_API_KEY`) |
| Google News RSS | Real, read-only, no key | Headlines from the last few days for your queries |
| CSV import | Real | Your own posts and comments, competitor ads, or any export (templates in [examples/csv](examples/csv)); IDs are scoped to the configured source name |
| Notes | Built in | `content-engine note "..."`: a question someone asked on a call or in a message |
| ElevenLabs | Real, optional, off by default | Reads each card's question in an AI voice; billed per character (`ELEVENLABS_API_KEY`) |
| faster-whisper | Optional extra, local | Word timestamps for takes (`uv sync --extra whisper`); any tool that outputs word timestamps works instead |
| ffmpeg | Optional, local | Renders the cut; burns captions and graphics in when built with libass, otherwise renders the clean cut and keeps them in `out/<session>/captions/<card>.ass` |
| Facebook Page reels | Real, dry-run by default | `video_reels` upload and publish |
| Instagram reels | Real, dry-run by default | Verify hosted bytes, container from an immutable `video_url`, then `media_publish` |
| Threads | Real, dry-run by default | Verify hosted bytes, container from an immutable `video_url`, then `threads_publish` |
| X | Real, dry-run by default | v2 chunked media upload, then a post (OAuth 1.0a) |
| YouTube Shorts | Real, dry-run by default | Resumable upload; private by default |
| TikTok | Real, dry-run by default | Inbox draft through the Content Posting API; you finish it in the app |

Not included: comment watching, scraping ad libraries (export competitor ads to CSV instead), carousel images and carousel posting (the engine writes the slide text only), uploading renders to a public host (Instagram and Threads fetch the video from `[publish] video_base_url`, which you provide), and notifications (each run prints its summary; cron can mail it). Every connector is tested against a fake transport; tests never call real APIs.

## Safety rules

- **Dry-run by default.** Live posting needs `[publish] mode = "live"` in the config; no command-line flag turns it on, and `--dry-run` turns it off for one run. Dry runs never read the publishing credentials.
- **At most once per platform.** Held platforms are never retried automatically, repair only retries definite failures, and one item starts per day. Publish and repair check `state/PAUSED` before each platform attempt and again immediately before each irreversible request, after persisting the commit record. A request already in flight may finish; pause stops requests that have not been sent. An OS file lock stays held for the entire run and releases on exit or crash. Its file remains in place: do not delete it while a run is active. The legacy `lock_stale_minutes` setting has no effect; active locks never expire.
- **Only what you approved posts.** An approval fingerprints the cut, graphics, complete subtitle document (text, timing, layout and disclosure), source video bytes, rendered video bytes, hosted media URL and post texts. Changing or removing these artifacts requires approval again. Post texts over a platform's limit, or with a banned word, can't be approved.
- **Live runs check before sending.** A missing credential or current verified render stops the run before anything is attempted. A successful render stores its presentation fingerprint and media digest. Editing without rendering preserves that proof only when its inputs and output are unchanged; skipped or failed renders cannot authorize an old MP4. Before the irreversible call, live publish and repair reload `session.json` and compare its approval, fingerprint and artifacts with the prepared post. Missing, malformed, rejected or changed persisted state stops that attempt before commit.
- **Session writes and commit share a lock.** Match, edit, approve and reject commands reload the session under its OS lock and hold it through their changes and save. Saving a session snapshot also checks that its content revision still matches `session.json`; a stale, deleted or damaged snapshot is refused. Matching and editing check before changing artifacts, so an older command cannot erase a saved rejection or approve another card by restoring old state. The publisher holds the same lock from its final reload and verification through the irreversible request. A busy operation reports `another run holds session.lock`; retry it after the active operation finishes. A `stale session` error requires reloading and applying the change to current state; rerunning the CLI command does this. Never delete the lock file. Before editing session files, transcripts or media directly in another application, pause publishing and let any active run finish; direct file writes do not take the engine's lock.
- **Secrets come from the environment.** Errors and the ledger never include request headers or URL queries. Credentials used in requests, including refreshed access tokens and rotated refresh tokens, are redacted from response-derived errors before logging or storage. TikTok's rotated refresh token is kept in `state/tiktok-token.json` (mode 0600).
- **Collected text is scrubbed** of emails and phone numbers before it is stored. Idea sources only read.
- **Metered calls are explicit**: ElevenLabs only through `content-engine audio` with `[tts] enabled = true`; YouTube searches use your API quota; X API access depends on your plan.

## Configuration

Everything lives in `content-engine.toml` (`content-engine init` writes it; defaults are in `src/content_engine/config.py`): `[creator]` (name, time zone for "today"), `[ideas]` (cooldown, trending window), `[sessions]` (cards per session), `[cards]` (length limits, banned words), `[tts]`, `[matching]` (threshold and margin), `[edit]` (pause length, padding, caption size, the on-screen AI disclosure), `[captions]` (call to action, footer, hashtags), `[carousel]`, `[publish]` (mode, platforms, posting time, `video_base_url`, attempts) and `[[sources]]`.

Per-card edit notes go in `sessions/<session>/session.json` under `edit_notes`, keyed by card number:

```json
"edit_notes": {"1": {"cut_phrases": ["so the first thing let me say that again"], "cut": [[31.2, 32.0]],
                     "cut_fillers": false,
                     "graphics": [{"type": "stat", "at": "day 1 7 and 14", "value": "1 · 7 · 14",
                                   "label": "follow-up days", "seconds": 3}]}}
```

Times are seconds in the raw take. Post texts are regenerated from the cut on every `edit` unless you change them by hand in `captions`; `edit --rewrite-captions` regenerates them anyway. A misheard word is fixed in the take's transcript file, then `edit` again.

Before going live, run `edit --render`, review the resulting media, then approve. Existing sessions without render proofs or with older approval fingerprints need this sequence once. Keep the subtitle, source video and render files available while publishing. Instagram and Threads must fetch the same approved render you upload to `video_base_url`.

For Instagram and Threads, set `video_base_url` before editing. A successful render records the hosted URL as `<base>/<session>-<card>-<sha256>.mp4`, where `sha256` is the full render digest. The review page shows the exact URL to upload to; the local render remains `out/<session>/renders/<card>.mp4`. Use a host that forbids replacing an object at an existing URL. Each connector downloads the complete hosted video and compares its SHA-256 digest with the approved render before creating a container; missing, partial or different content stops that platform before publication. Changing the base URL requires `edit` and approval again. Older approvals must be renewed to cover the URL; dry runs simulate the same check with synthetic media and never fetch a real host.

Keep private CSV exports in `data/` (git-ignored); `examples/csv/` contains synthetic templates only. CSV IDs are namespaced by source, so keep each configured source name stable. When upgrading an existing evidence store from unscoped CSV IDs, back up `state/evidence.json`, remove its old CSV records by source, then run `ideas --force` to reimport them without counting both formats.

## Repository layout

```text
src/content_engine/
  cli.py              commands
  config.py           defaults and validation for content-engine.toml
  session.py          the recording session and its rules (the unit of work)
  ideas/              evidence store, keyword filing, the score (rank.py), sources/ (youtube, news, csv, demo)
  cards.py            question cards and the recording sheet
  tts.py              optional ElevenLabs question audio
  transcript.py       transcript formats and optional local Whisper
  matching.py         takes to cards
  trims.py            the cut: segments and the source-to-reel time map
  captions.py         word-by-word cues and post texts
  overlay.py          graphics and the .ass subtitle track
  render.py           ffmpeg
  pipeline.py         match and edit for a session
  review.py           the numbered review page and approvals
  carousel.py         carousel text from the approved caption
  publish/            ledger.py, plan.py, run.py, simulate.py, platforms/ (six connectors and OAuth 1.0a)
  demo.py, demo_data.py   the synthetic demo
  templates/          content-engine.example.toml, ideas.example.toml
examples/             scheduling (cron, launchd, systemd) and CSV templates
tests/                pytest, with fake transports and synthetic transcripts
```

## Development

```bash
uv run pytest
uv run ruff check
```

Runtime code uses only the Python standard library. The `whisper` extra adds faster-whisper for local transcripts.

## License

MIT. See [LICENSE](LICENSE).
