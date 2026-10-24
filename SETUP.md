# Setting up the content engine (for a coding agent)

You are helping a creator or small-business owner set up the content engine. Work through the steps in order, ask before each choice that is theirs to make, and run the check at the end of each step. Never write credentials into `content-engine.toml`, into the repo, or into chat; they go in `.env` (git-ignored) or the scheduler's environment. Nothing posts until the owner sets `[publish] mode = "live"` themselves in step 7.

## 1. Run the demo

```bash
uv run content-engine demo
```

Walk the owner through the printed steps and open `content-engine-demo/out/demo/review/index.html`. Explain the weekly loop: ideas are ranked continuously, one recording session a week turns the top ideas into question cards, takes are matched and cut, the owner approves by number, and one approved reel posts per day. Done when the demo prints "7. Carousel text" and the review page opens.

## 2. Write the config and the ideas

Run `uv run content-engine init`, then ask:

1. **Name and time zone** (`[creator]`). The time zone decides when "today" starts for one-post-a-day and `post_after`.
2. **Ideas** (`ideas.toml`). Ask what questions their audience asks most. Write 8 to 20 `[[idea]]` entries: a short title (the on-screen hook), the question the AI voice should read, an optional context line, talking points, `fit` 0 to 3, and keywords. Keywords decide filing: pick phrases that appear in posts and comments about that topic, and avoid words two ideas share (ties go to the idea listed first).
3. **Cards per session** (`[sessions] cards`, 8 by default): how many questions they answer in one sitting.
4. **Words to avoid** (`[cards] banned_words`): anything that must never appear on a card or in a post.
5. **Call to action and footer** (`[captions]`). Keep the footer that says the question is read by an AI voice if they use one.

Done when `uv run content-engine check` prints "Config OK" and the number of ideas.

## 3. Connect idea sources

Copy `.env.example` to `.env` and fill in only the variables they need. The CLI does not load this file automatically: use `uv run --env-file .env` for the commands below, or export the variables in the process environment. Keep private CSV exports in `data/` (git-ignored), and keep each source name stable so repeated imports update the same evidence IDs.

Ask which signals they have and add a `[[sources]]` block for each (examples are in the config file):

- **Other creators** (`type = "youtube"`): they create an API key in Google Cloud with the YouTube Data API v3 enabled and put it in `YOUTUBE_API_KEY`. Each search costs 100 quota units of the 10,000 a day; keep queries few and specific.
- **News** (`type = "news"`): Google News RSS, no key. Two or three queries about their field.
- **Their own posts, comments and competitor ads** (`type = "csv"`): there are no built-in connectors for platform analytics or ad libraries. Set up an export to CSV in the shape of `examples/csv/` (`kind = "own_post"`, `"own_comment"` or `"ad"`). For ads, `days_live` and `versions` matter most.
- **Questions from calls and messages**: no setup. Show them `uv run --env-file .env content-engine note "the question"`; these count most in the score.

Run `uv run --env-file .env content-engine ideas --force`. Done when every source shows `ok` and the ranking lists ideas. If an idea never gets evidence, its keywords don't match how people write about it; if "Unfiled evidence keeps mentioning" shows a topic they care about, add an idea for it.

## 4. Plan and record the first session

1. `uv run --env-file .env content-engine plan --date YYYY-MM-DD` (their recording day). Review the cards with them; `plan --replace` re-plans after editing ideas.
2. Optional AI voice: they create an ElevenLabs key and pick a voice, you set `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` and `[tts] enabled = true`, then run `uv run --env-file .env content-engine audio`. It prints the characters billed. The recording sheet (`out/<session>/sheet.html`) then has a player per card.
3. They record: play each question, answer it, and keep the question audible in the take. Any camera works.
4. Copy each video into `sessions/<session>/takes/` and add a word-timestamp transcript with the same name (`take-01.mp4` and `take-01.json`). Either use their transcription tool's word-level JSON (formats are listed in `src/content_engine/transcript.py`) or run `uv sync --extra whisper` and `uv run --env-file .env content-engine transcribe`.

Done when `uv run --env-file .env content-engine match` lists each take as matched to a card. A take that should match but doesn't usually started after the question; check its transcript's first lines.

## 5. Edit and review

1. `uv run --env-file .env content-engine edit --render` (ffmpeg needed for `--render`; without libass the captions stay in `out/<session>/captions/`).
2. Open `out/<session>/review/index.html` with the owner. For each item: the cut, what was removed, graphics, captions and the post texts.
3. Changes go in `sessions/<session>/session.json`: `edit_notes` per card (`cut_phrases`, `cut`, `graphics`, as in the README) or the `captions` texts. Fix misheard words in the take's transcript. Run `edit --render` again after each change, then review the new output. A render proof binds the successful output to its inputs; editing without rendering cannot authorize an old video. Keep source video, subtitle and render files while publishing. Changes to their bytes, subtitle text or timing, disclosure, graphics or post texts require approval again. Older sessions without render proofs also need rendering and approval again.
4. The owner approves by number: `uv run --env-file .env content-engine approve <session> 1 2 4`. Never approve on their behalf.

Done when `uv run --env-file .env content-engine status` shows the approved count you expect for the session.

## 6. Rehearse publishing

Keep `[publish] mode = "dry-run"`. Run `uv run --env-file .env content-engine publish`. Every connector runs against the built-in simulator and writes to `state/dry-run/ledger.jsonl`; nothing leaves the machine. Show them the summary: what would post where, and the to-do list.

Then schedule `content-engine run` hourly with the example for their system in `examples/scheduling/` (replace `/path/to/content-engine` and `/path/to/uv`). Done when the scheduler's log shows a tick every hour and `uv run --env-file .env content-engine status` shows one fewer never-posted item in the dry-run queue each day after `post_after`.

## 7. Go live, one platform at a time

Only when the owner asks for it:

1. Set `[publish] platforms` to one platform and add its variables to `.env` (`.env.example` lists them with the permissions each token needs). Instagram and Threads also need `[publish] video_base_url`: a public folder where the renders are uploaded as `<session>-<card>.mp4` (any static host or bucket). The engine does not upload them.
2. `uv run --env-file .env content-engine check` must show "env set" for it.
3. The owner sets `[publish] mode = "live"` and you run `uv run --env-file .env content-engine publish` once together, then check the post on the platform.
4. Add the next platform and repeat. YouTube uploads stay private until their API project passes YouTube's audit; TikTok lands as an inbox draft they finish in the app.

If a platform is **held**, the publish call got no clear answer. Do not retry it. Ask the owner to look at the account, then record what they found: `uv run --env-file .env content-engine reconcile <item> <platform> --posted --url <link>` or `--not-posted` (then `repair --item <item>` posts it). To stop everything at once: `touch state/PAUSED`. A corrupt ledger stops publishing; preserve it for inspection instead of deleting it. The OS releases the publish lock when the process exits or crashes, regardless of file age; never delete an active lock file. Successful repair also writes carousel text, even if every initial publish failed.

Do not change `publish/run.py`, `publish/ledger.py` or `publish/plan.py` to get a post through. If something is wrong, pause, explain, and let the owner decide.
