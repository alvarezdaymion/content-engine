"""`content-engine demo`: the whole loop on synthetic data, with no keys, no network and no ffmpeg.

It ranks ideas for a made-up bookkeeping studio, plans a session, writes synthetic take transcripts, matches them to
the cards, cuts them, builds captions and graphics, renders the numbered review page, approves two items, runs a
dry-run publish to all six platforms against the simulator (Instagram's publish call times out and ends held,
Threads fails before publishing and repair fills it), and builds the carousel text.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config, demo_data, ideas, pipeline, review
from . import session as session_mod
from .cards import sheet_html, spoken
from .publish import run as publish
from .store import read_json, write_text

MARKER = ".content-engine-demo"
SIMULATE = {"instagram": "commit-timeout", "threads": "prepare-error-once"}
TAKE_NOTES = {"take-04": " (recording started mid-question, one word misheard)", "take-05": " (a pickup)"}


def demo_config(root: Path) -> config.Config:
    return config.from_dict({
        "creator": {"name": demo_data.BUSINESS, "timezone": "UTC"},
        "sessions": {"cards": 4},
        "publish": {"mode": "dry-run"},
        "sources": [{"name": g, "type": "demo"} for g in ("creators", "ads", "news", "own")],
    }, root)


def prepare(root: Path) -> None:
    """Start from an empty folder. Only a folder the demo made itself (it holds the marker file) is cleared."""
    if root.exists() and any(root.iterdir()):
        if not (root / MARKER).exists():
            raise RuntimeError("%s is not empty and was not made by the demo; pick another --dir" % root)
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / MARKER).write_text("made by content-engine demo; safe to delete\n")


def write_takes(s: session_mod.Session) -> None:
    """Synthetic transcripts: card 1 clean, card 2 an aborted take then a retake, card 3 started late with a
    misheard word, card 4 not recorded, plus one pickup with no question."""
    def save(name: str, words: list[list[Any]]) -> None:
        write_text(s.takes_dir / ("%s.json" % name), json.dumps(words))

    cards = s.cards
    answer = {c["n"]: demo_data.ANSWERS[c["idea"]] for c in cards}
    save("take-01", demo_data.take_words(spoken(cards[0]), answer[1]))
    if len(cards) > 1:
        save("take-02", demo_data.take_words(spoken(cards[1]), "Hmm, hold on. Let me start that one over."))
        save("take-03", demo_data.take_words(spoken(cards[1]), answer[2]))
    if len(cards) > 2:
        longest = max(cards[2]["question"].split(), key=len)
        save("take-04", demo_data.take_words(spoken(cards[2]), answer[3], skip_question_words=2,
                                             misheard={longest: longest[: len(longest) // 2] + " "
                                                       + longest[len(longest) // 2:]}))
    words, _ = demo_data.speak(demo_data.PICKUP, 0.4)
    save("take-05", words)


def run_demo(root: Path, now: dt.datetime | None = None, out: Callable[[str], None] = print) -> int:
    root = root.resolve()
    prepare(root)
    now = now or dt.datetime.now(dt.UTC)
    cfg = demo_config(root)
    write_text(cfg.path("ideas"), demo_data.ideas_toml())
    for text, hours in demo_data.NOTES:
        ideas.add_note(cfg, text, now - dt.timedelta(hours=hours))
    lines = ["Content engine demo: a made-up bookkeeping studio, synthetic data, no keys, no network.",
             "Nothing is posted anywhere. Everything is written under %s." % root, ""]

    result = ideas.refresh(cfg, now, {}, force=True)
    filed = sum(len(v) for v in result["by_idea"].values())
    lines.append("1. Ideas ranked from %d sources: %d items, %d filed under %d ideas, %d unfiled"
                 % (len(result["sources"]), result["items"], filed, len(result["ideas"]), len(result["unfiled"])))
    for i in result["ideas"][:5]:
        p = i["parts"]
        lines.append("   %d. %-34s score %5.1f  creators %4.1f · market %4.1f · ours %4.1f%s"
                     % (i["rank"], i["title"], i["score"], p["creators"], p["market"], p["ours"],
                        "  trending" if i["trending"] else ""))
    if result["unfiled_terms"]:
        lines.append("   Possible new ideas in unfiled items: %s"
                     % ", ".join("%s (%d)" % t for t in result["unfiled_terms"][:4]))

    s = session_mod.plan(cfg, [], now, sid="demo")
    sheet = write_text(cfg.out_dir / s.id / "sheet.html", sheet_html(s))
    lines += ["", "2. Session %r planned with %d question cards from the top ideas. They cool down in the ranking "
              "while the session holds them." % (s.id, len(s.cards))]
    for c in s.cards:
        lines.append("   card %d  %-34s  AI voice reads: %s" % (c["n"], c["hook"], c["question"]))
    lines.append("   Recording sheet: %s" % sheet.relative_to(root.parent))
    try:
        session_mod.plan(cfg, [s], now)
    except session_mod.SessionRuleError as e:
        lines.append("   Planning another session now is refused: %s" % e)

    write_takes(s)
    matches, _ = pipeline.match(cfg, s)
    lines += ["", "3. Takes matched to this session's cards from their transcripts:"]
    for name, m in matches.items():
        if m.status == "matched":
            lines.append("   %s  card %d  score %.2f%s" % (name, m.card, m.score, TAKE_NOTES.get(name, "")))
        elif m.status == "superseded":
            lines.append("   %s  card %d  superseded by %s (an aborted take)" % (name, m.card, m.by))
        else:
            lines.append("   %s  no card: %s%s" % (name, m.reason, TAKE_NOTES.get(name, "")))
    missing = [c for c in s.cards if c["n"] not in s.recorded_cards]
    if missing:
        back = ideas.ranked(cfg, now, session_mod.idea_usage([s]))["ideas"]
        returned = [c["hook"] for c in missing if any(i["id"] == c["idea"] for i in back)]
        lines.append("   Not recorded: card %s. Back in the ranking: %s."
                     % (", ".join(str(c["n"]) for c in missing), ", ".join(returned) or "none"))

    for c in s.cards:
        if c["idea"] in demo_data.OVERRIDES:
            s.edit_notes[str(c["n"])] = demo_data.OVERRIDES[c["idea"]]
    s.save()
    rows = pipeline.edit(cfg, s)
    lines += ["", "4. Edits (trims, word-by-word captions, graphics, post texts; rendering needs ffmpeg and real "
              "video, so it is skipped here):"]
    for r in rows:
        kinds: dict[str, int] = {}
        for rem in r["removed"]:
            kinds[rem["kind"]] = kinds.get(rem["kind"], 0) + 1
        lines.append("   card %d  %5.1f s -> %5.1f s  cut: %-18s %3d caption cues, %d graphic(s)%s"
                     % (r["card"], r["raw"], r["cut"], ", ".join("%d %s" % (v, k) for k, v in kinds.items()) or "-",
                        r["cues"], r["graphics"], "  check: " + "; ".join(r["problems"]) if r["problems"] else ""))
    page = cfg.out_dir / s.id / "review" / "index.html"
    lines.append("   Review page: %s" % page.relative_to(root.parent))

    approve = sorted(int(n) for n in s.edits)[:2]
    review.approve(s, approve, now)
    s.save()
    review.write_page(cfg, s)
    lines += ["", "5. Approved by number: %s. Approval covers the cut, graphics and post texts exactly as shown."
              % ", ".join(map(str, approve))]

    captured: list[str] = []
    sessions = session_mod.load_all(cfg)
    publish.daily(cfg, sessions, now, live=False, simulate=SIMULATE, out=captured.append)
    lines += ["", "6. Dry-run publish to six platforms (simulated APIs, separate ledger):"]
    lines += ["   " + ln for block in captured for ln in block.splitlines()]
    captured.clear()
    publish.daily(cfg, sessions, now, live=False, out=captured.append)
    again = [ln.strip() for ln in captured[0].splitlines() if "already ran" in ln]
    lines.append("   A second run the same day: " + (again[0] if again else captured[0].splitlines()[-1]))
    captured.clear()
    publish.repair(cfg, sessions, now, live=False, out=captured.append)
    lines.append("   Repair (only platforms that definitely did not get the post):")
    lines += ["   " + ln for ln in captured[0].splitlines()[1:]]

    first = s.key(approve[0])
    deck = read_json(cfg.out_dir / s.id / "carousels" / ("%d.json" % approve[0]), {}) or {}
    lines += ["", "7. Carousel text for %s, word for word from the approved caption:" % first]
    lines += ["   %d. %s" % (i, t) for i, t in enumerate(deck.get("slides", []), 1)]
    lines += ["", "Next: open the review page, then run `content-engine init` to set up your own ideas, sources "
              "and platforms (SETUP.md walks a coding agent through it)."]
    out("\n".join(lines))
    return 0
