"""Command line. Run `content-engine demo` first; SETUP.md covers real use.

    demo                     the whole loop on synthetic data, no keys
    init, check              write the config and ideas files; validate them and show what is set
    run                      the scheduled tick: ideas when due, today's post, repair
    ideas, note              rank ideas now; add a question someone asked
    plan                     open the next recording session (cards from the top ideas)
    audio, transcribe        optional: AI question audio; local Whisper transcripts
    match, edit              match takes to cards; trims, captions, graphics, post texts, review page
    approve, reject          decide review items by number
    publish, repair          post today's item; retry platforms that definitely did not get it
    reconcile                record what you found on a held platform
    status                   sessions, queue and held platforms
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
from pathlib import Path

from . import config as config_mod
from . import demo as demo_mod
from . import ideas, pipeline, review
from . import session as session_mod
from .cards import sheet_html, validate
from .publish import run as publish
from .publish.platforms import ENV, ORDER
from .render import ffmpeg, has_filter
from .store import now_utc, write_text

TEMPLATES = Path(__file__).parent / "templates"


def _cfg(args: argparse.Namespace) -> config_mod.Config:
    if not Path(args.config).exists():
        raise RuntimeError("no %s here: run `content-engine init` first, or pass --config" % args.config)
    return config_mod.load(args.config)


def _session(cfg: config_mod.Config, sid: str | None) -> session_mod.Session:
    return session_mod.get(session_mod.load_all(cfg), sid)


def _live(cfg: config_mod.Config, args: argparse.Namespace) -> bool:
    return cfg["publish"]["mode"] == "live" and not getattr(args, "dry_run", False)


def cmd_demo(args: argparse.Namespace) -> int:
    return demo_mod.run_demo(Path(args.dir))


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path(args.config)
    ideas_dest = dest.parent / "ideas.toml"
    wrote = []
    for src, target in ((TEMPLATES / "content-engine.example.toml", dest),
                        (TEMPLATES / "ideas.example.toml", ideas_dest)):
        if target.exists() and not args.force:
            print("%s already exists (use --force to overwrite)" % target)
            continue
        shutil.copyfile(src, target)
        wrote.append(str(target))
    if wrote:
        print("Wrote %s. Edit them, then run: content-engine check" % " and ".join(wrote))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    print("Config OK: %s" % Path(args.config).resolve())
    topics = ideas.load_ideas(cfg.path("ideas"))
    print("Ideas: %d in %s" % (len(topics), cfg.path("ideas")) if topics else
          "Ideas: none yet. Add [[idea]] entries to %s" % cfg.path("ideas"))
    if not cfg["sources"]:
        print("Sources: none configured; only notes (`content-engine note`) feed the ranking")
    for s in cfg["sources"]:
        state = "ready"
        if s["type"] == "youtube":
            state = "env set" if os.environ.get("YOUTUBE_API_KEY") else "missing env: YOUTUBE_API_KEY"
        elif s["type"] == "csv":
            p = cfg.root / s["path"]
            state = "file found" if p.exists() else "file missing: %s" % p
        print("  source %-12s type=%-8s every %sh  %s" % (s["name"], s["type"], s.get("every_hours", 6), state))
    tts = cfg["tts"]
    if tts["enabled"]:
        need = [v for v in ("ELEVENLABS_API_KEY",) if not os.environ.get(v)]
        if not (tts["voice_id"] or os.environ.get("ELEVENLABS_VOICE_ID")):
            need.append("ELEVENLABS_VOICE_ID or [tts] voice_id")
        print("Question audio: on, %s" % ("missing: " + ", ".join(need) if need else "env set"))
    else:
        print("Question audio: off ([tts] enabled = false); cards are text only")
    print("ffmpeg: %s" % ("found%s" % ("" if has_filter("ass") else ", without libass (captions stay in .ass files)")
                          if ffmpeg() else "not found (edits work; rendering is skipped)"))
    pub = cfg["publish"]
    print("Publish mode: %s, after %s %s" % (pub["mode"], pub["post_after"], cfg["creator"]["timezone"]))
    for p in ORDER:
        if p not in pub["platforms"]:
            print("  %-10s off" % p)
            continue
        missing = [v for v in ENV[p] if not os.environ.get(v)]
        print("  %-10s on   %s" % (p, "env set" if not missing else "missing env: " + ", ".join(missing)))
    if {"instagram", "threads"} & set(pub["platforms"]) and not pub["video_base_url"]:
        print("  note: Instagram and Threads need [publish] video_base_url in live mode")
    sessions = session_mod.load_all(cfg)
    open_s = session_mod.open_session(sessions)
    print("Sessions: %d%s" % (len(sessions), ", unrecorded: %s" % open_s.id if open_s else ""))
    return 0


def cmd_ideas(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    now = now_utc()
    result = ideas.refresh(cfg, now, session_mod.idea_usage(session_mod.load_all(cfg)), force=args.force)
    for r in result["sources"]:
        state = ("ok, %d items (%d new)" % (r["items"], r["new"]) if not r["error"] else "error: " + r["error"]) \
            if r["ran"] else "not due"
        print("source %-12s %s" % (r["name"], state))
    print_ranking(result, args.top)
    print("Wrote %s" % (cfg.out_dir / "ideas.json"))
    return 0


def print_ranking(result: dict, top: int) -> None:
    if not result["ideas"]:
        print("No ranked ideas yet: add ideas to ideas.toml and evidence (sources or notes).")
    for i in result["ideas"][:top]:
        print("%3d. %-40s %5.1f%s  %s" % (i["rank"], i["title"][:40], i["score"], "  trending" if i["trending"] else "",
                                          " · ".join("%s %.1f" % kv for kv in i["parts"].items())))
    for c in result["cooling"]:
        print("     cooling until %s: %s (taken by a session)" % (c["until"][:10], c["title"]))
    if result["unfiled_terms"]:
        print("Unfiled evidence keeps mentioning: %s" % ", ".join("%s (%d)" % t for t in result["unfiled_terms"]))


def cmd_note(args: argparse.Namespace) -> int:
    item = ideas.add_note(_cfg(args), args.text, now_utc())
    print("Added question: %s" % item["text"])
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    now = now_utc()
    s = session_mod.plan(cfg, session_mod.load_all(cfg), now, args.date, args.cards, args.replace, args.id)
    sheet = write_text(cfg.out_dir / s.id / "sheet.html", sheet_html(s, _audio_links(cfg, s)))
    print("Session %s, recording on %s. Cards: %d" % (s.id, s.record_on, len(s.cards)))
    for c in s.cards:
        print("  %d. %s\n     %s" % (c["n"], c["hook"], c["question"]))
    for p in validate(s.cards, cfg):
        print("  check: %s" % p)
    print("Recording sheet: %s\nPut each take's video and transcript in %s, then run: content-engine match"
          % (sheet, s.takes_dir))
    return 0


def _audio_links(cfg: config_mod.Config, s: session_mod.Session) -> dict[int, str]:
    sheet_dir = cfg.out_dir / s.id
    return {c["n"]: os.path.relpath(s.audio_dir / ("%d.mp3" % c["n"]), sheet_dir)
            for c in s.cards if (s.audio_dir / ("%d.mp3" % c["n"])).exists()}


def cmd_audio(args: argparse.Namespace) -> int:
    from .tts import ElevenLabs, make_audio
    cfg = _cfg(args)
    if not cfg["tts"]["enabled"]:
        print("Question audio is off. Set [tts] enabled = true (it is billed per character).")
        return 1
    s = _session(cfg, args.session)
    done = make_audio(s, ElevenLabs(cfg["tts"]["voice_id"], cfg["tts"]["model"]), cfg.state_dir / "tts-cache")
    write_text(cfg.out_dir / s.id / "sheet.html", sheet_html(s, _audio_links(cfg, s)))
    print("Audio for %d cards (%d new, %d cached, %d characters billed). Sheet updated."
          % (len(s.cards), len(done["made"]), len(done["cached"]), done["characters"]))
    return 0


def cmd_transcribe(args: argparse.Namespace) -> int:
    from .transcript import transcribe_missing
    cfg = _cfg(args)
    s = _session(cfg, args.session)
    done = transcribe_missing(s.takes_dir, cfg["transcribe"]["model"])
    print("Transcribed: %s" % (", ".join(done) or "nothing new"))
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    s = _session(cfg, args.session)
    results, waiting = pipeline.match(cfg, s)
    for name, m in results.items():
        detail = "card %s, score %.2f" % (m.card, m.score) if m.status == "matched" else \
            "card %s, replaced by %s" % (m.card, m.by) if m.status == "superseded" else m.reason
        print("  %-16s %-10s %s" % (name, m.status, detail))
    if waiting:
        print("  no transcript yet: %s (run content-engine transcribe, or add <take>.json)" % ", ".join(waiting))
    missing = [str(c["n"]) for c in s.cards if c["n"] not in s.recorded_cards]
    print("Recorded cards: %s%s" % (", ".join(map(str, s.recorded_cards)) or "none",
                                     "; not recorded: " + ", ".join(missing) if missing else ""))
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    s = _session(cfg, args.session)
    rows = pipeline.edit(cfg, s, render_video=args.render, rewrite_captions=args.rewrite_captions)
    for r in rows:
        print("  card %d  %.1f s -> %.1f s, %d cut(s), %d caption cues, %d graphic(s); %s"
              % (r["card"], r["raw"], r["cut"], len(r["removed"]), r["cues"], r["graphics"], r["render"]))
        for p in r["problems"]:
            print("     check: %s" % p)
    print("Review page: %s" % (cfg.out_dir / s.id / "review" / "index.html"))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    s = _session(cfg, args.session)
    now = now_utc()
    problems = review.approve(s, args.numbers, now, cfg["cards"]["banned_words"]) if args.cmd == "approve" else \
        review.reject(s, args.numbers, args.note, now)
    s.save()
    review.write_page(cfg, s)
    for p in problems:
        print("  %s" % p)
    for n in args.numbers:
        print("  %s: %s" % (n, session_mod.review_status(s, n)))
    return 1 if problems else 0


def cmd_publish(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if cfg["publish"]["mode"] == "off":
        print("Publishing is off ([publish] mode = \"off\").")
        return 0
    return publish.daily(cfg, session_mod.load_all(cfg), now_utc(), _live(cfg, args))


def cmd_repair(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if cfg["publish"]["mode"] == "off":
        print("Publishing is off ([publish] mode = \"off\").")
        return 0
    return publish.repair(cfg, session_mod.load_all(cfg), now_utc(), _live(cfg, args), item=args.item,
                          platform=args.platform)


def cmd_reconcile(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    try:
        print(publish.reconcile(cfg, args.item, args.platform, args.posted, now_utc(), _live(cfg, args), args.url))
    except ValueError as e:
        print(str(e))
        return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    now = now_utc()
    sessions = session_mod.load_all(cfg)
    result = ideas.refresh(cfg, now, session_mod.idea_usage(sessions))
    ran = [r for r in result["sources"] if r["ran"]]
    lines = ["Ideas: %d ranked; sources run: %s" % (len(result["ideas"]), ", ".join(
        "%s (%s)" % (r["name"], "error" if r["error"] else "%d items" % r["items"]) for r in ran) or "none due")]
    code = 0
    mode = cfg["publish"]["mode"]
    after = dt.time.fromisoformat(cfg["publish"]["post_after"])
    if mode != "off" and cfg.localize(now).time() >= after:
        live = mode == "live"
        code = publish.daily(cfg, sessions, now, live, out=lines.append)
        publish.repair(cfg, sessions, now, live, out=lines.append)
    elif mode != "off":
        lines.append("Publish: waits until %s %s" % (cfg["publish"]["post_after"], cfg["creator"]["timezone"]))
    if not args.quiet:
        print("\n".join(lines))
    return code


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    sessions = session_mod.load_all(cfg)
    live = cfg["publish"]["mode"] == "live"
    states = publish.ledger_for(cfg, live).states()
    finished = publish.finished_items(states, list(cfg["publish"]["platforms"]))
    print("Sessions:" if sessions else "Sessions: none yet (content-engine plan)")
    for s in sessions:
        recorded = s.recorded_cards
        print("  %-14s %-11s %d cards, %d recorded, %d edited, %d approved" % (
            s.id, session_mod.stage(s, finished), len(s.cards), len(recorded), len(s.edits),
            sum(1 for n in s.edits if session_mod.review_status(s, n) == "approved")))
    queue, changed = session_mod.queue(sessions)
    left = [s.key(n) for s, n in queue if not any(it == s.key(n) for it, _ in states)]
    print("Queue (%s ledger): %d approved, never posted: %s" % ("live" if live else "dry-run", len(left),
                                                               ", ".join(left) or "none"))
    if changed:
        print("Changed since approval: %s" % ", ".join(changed))
    held = sorted("%s %s" % k for k, st in states.items() if st.state == "held")
    print("Held for a person to check: %s" % (", ".join(held) or "none"))
    if (cfg.state_dir / "PAUSED").exists():
        print("Publishing is PAUSED (state/PAUSED)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="content-engine", description="A Q&A video pipeline built around one weekly "
                                 "recording session. Start with `content-engine demo`; SETUP.md covers real use.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default="content-engine.toml")

    def add(name: str, fn, help_text: str, **kw) -> argparse.ArgumentParser:  # type: ignore[no-untyped-def]
        p = sub.add_parser(name, help=help_text, parents=[common], **kw)
        p.set_defaults(fn=fn)
        return p

    d = sub.add_parser("demo", help="run the whole loop on synthetic data, no keys needed")
    d.add_argument("--dir", default="content-engine-demo", help="where the demo writes everything")
    d.set_defaults(fn=cmd_demo)
    add("init", cmd_init, "write content-engine.toml and ideas.toml from the examples").add_argument(
        "--force", action="store_true")
    add("check", cmd_check, "validate the config and show which credentials are set")
    add("run", cmd_run, "scheduled tick: ideas when due, today's post, repair").add_argument(
        "--quiet", action="store_true")
    p = add("ideas", cmd_ideas, "collect due sources and rank ideas now")
    p.add_argument("--force", action="store_true", help="run every source, due or not")
    p.add_argument("--top", type=int, default=10)
    add("note", cmd_note, "add a question someone asked (heaviest evidence)").add_argument("text")
    p = add("plan", cmd_plan, "open the next recording session from the top ideas")
    p.add_argument("--date", help="recording day, YYYY-MM-DD (default today)")
    p.add_argument("--cards", type=int, help="cards in the session (default [sessions] cards)")
    p.add_argument("--replace", action="store_true", help="re-plan the unrecorded session")
    p.add_argument("--id", help="session id (default the date)")
    for name, fn, text in (("audio", cmd_audio, "AI voice audio for each card (ElevenLabs, optional, billed)"),
                           ("transcribe", cmd_transcribe, "word timestamps for takes with local Whisper (optional)"),
                           ("match", cmd_match, "match the session's takes to its cards")):
        add(name, fn, text).add_argument("session", nargs="?", help="session id (default the latest)")
    p = add("edit", cmd_edit, "trims, captions, graphics, post texts and the review page")
    p.add_argument("session", nargs="?")
    p.add_argument("--render", action="store_true", help="render with ffmpeg when installed")
    p.add_argument("--rewrite-captions", action="store_true", help="regenerate post texts you edited by hand")
    for name, text in (("approve", "approve review items by number"),
                       ("reject", "send review items back by number")):
        p = add(name, cmd_decide, text)
        p.add_argument("session")
        p.add_argument("numbers", type=int, nargs="+")
        if name == "reject":
            p.add_argument("--note", default="", help="what to change")
    add("publish", cmd_publish, "post today's approved item ([publish] mode)").add_argument(
        "--dry-run", action="store_true", help="simulate even when mode is live")
    p = add("repair", cmd_repair, "retry platforms that definitely did not get the post")
    p.add_argument("--item", help="retry an older item on purpose, e.g. 2026-11-02#3")
    p.add_argument("--platform", choices=ORDER)
    p.add_argument("--dry-run", action="store_true")
    p = add("reconcile", cmd_reconcile, "record what you found on a held platform")
    p.add_argument("item")
    p.add_argument("platform", choices=ORDER)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--posted", dest="posted", action="store_true")
    g.add_argument("--not-posted", dest="posted", action="store_false")
    p.add_argument("--url", default="")
    p.add_argument("--dry-run", action="store_true", help="reconcile in the dry-run ledger")
    add("status", cmd_status, "sessions, queue and held platforms")
    args = ap.parse_args(argv)
    try:
        return int(args.fn(args))
    except (session_mod.SessionRuleError, FileNotFoundError, ValueError, RuntimeError) as e:
        print("content-engine %s: %s" % (args.cmd, e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
