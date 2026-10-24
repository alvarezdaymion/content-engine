"""The numbered review page and approvals.

Each edited card is one numbered item: the cut (raw take beside the render when both exist), what was removed and
why, the graphics, the caption preview and every post text. The creator approves by number
(`content-engine approve <session> 1 3`). An approval records a fingerprint of the take, the cut, the graphics and
the post texts; if any of them changes afterwards, the item shows as changed and leaves the publish queue until it
is approved again (session.fingerprint).
"""

from __future__ import annotations

import datetime as dt
import html
import os
from pathlib import Path
from typing import Any

from .captions import validate_texts
from .cards import CSS
from .session import Session, fingerprint, render_current, review_status
from .store import iso


def approve(session: Session, numbers: list[int], now: dt.datetime, banned: list[str] | None = None) -> list[str]:
    """Approve items by number. Post texts that break a platform limit or use a banned word are refused."""
    problems = []
    for n in numbers:
        fp = fingerprint(session, n)
        if fp is None:
            problems.append("card %s has no edit yet (run `content-engine edit`)" % n)
            continue
        text_problems = validate_texts(session.captions[str(n)], banned or [])
        if text_problems:
            problems.append("card %s not approved: %s" % (n, "; ".join(text_problems)))
            continue
        session.review[str(n)] = {"status": "approved", "fingerprint": fp, "at": iso(now)}
    return problems


def reject(session: Session, numbers: list[int], note: str, now: dt.datetime) -> list[str]:
    problems = []
    for n in numbers:
        if str(n) not in session.edits:
            problems.append("card %s has no edit yet" % n)
            continue
        session.review[str(n)] = {"status": "rejected", "note": note, "at": iso(now)}
    return problems


def _clock(t: float) -> str:
    return "%d:%04.1f" % (t // 60, t % 60)


def _rel(target: Path, page_dir: Path) -> str:
    return os.path.relpath(target, page_dir).replace(os.sep, "/")


def _item(session: Session, n: int, page_dir: Path, render_dir: Path) -> str:
    e = html.escape
    card, edit, texts = session.card(n), session.edits[str(n)], session.captions.get(str(n), {})
    status = review_status(session, n)
    media = []
    if edit.get("video"):
        media.append('<figure><figcaption>Raw take</figcaption><video src="%s" controls preload="metadata">'
                     '</video></figure>' % e(_rel(session.takes_dir / edit["video"], page_dir)))
    if render_current(session, n):
        render = session.dir / edit["artifacts"]["render"]
        media.append('<figure><figcaption>Edited</figcaption><video src="%s" controls preload="metadata">'
                     '</video></figure>' % e(_rel(render, page_dir)))
    removed = "".join("<li>%s at %s: %s</li>" % (e(r["kind"]), _clock(r["a"]), e(r["text"]))
                      for r in edit.get("removed", []))
    graphics = "".join("<li>%s at %s: %s</li>" % (e(g["type"]), _clock(g["start"]),
                                                   e(g.get("text") or "%s %s" % (g.get("value", ""),
                                                                                 g.get("label", ""))))
                       for g in edit.get("graphics", []))
    problems = "".join("<li>%s</li>" % e(p) for p in edit.get("problems", []))
    posts = "".join('<span class="lab">%s · %d characters</span><pre>%s</pre>' % (k, len(v), e(v))
                    for k, v in texts.items())
    note = (session.review.get(str(n)) or {}).get("note")
    return ('<section class="card" id="i%d"><div class="top"><span class="num">%d</span><span class="hook">%s</span>'
            '<span class="chip %s">%s</span></div><p>%s</p><p class="why">%s · %.1f s raw, %.1f s cut · %s · '
            '%s caption cues</p>%s%s%s%s%s<span class="lab">Captions start</span><p>%s</p>%s'
            '<p class="why">Approve: <code>content-engine approve %s %d</code></p></section>'
            % (n, n, e(card["hook"]), e(status.replace(" ", "-")), e(status), e(card["question"]),
               e(edit.get("take", "")), edit.get("duration_raw", 0), edit.get("duration", 0), e(edit.get("render", "")),
               edit.get("cues", 0), '<div class="pair">%s</div>' % "".join(media) if media else "",
               '<p class="why">Note: %s</p>' % e(note) if note else "",
               '<span class="lab">Check</span><ul class="warn">%s</ul>' % problems if problems else "",
               '<span class="lab">Removed (times in the raw take)</span><ul>%s</ul>' % removed if removed else "",
               '<span class="lab">Graphics</span><ul>%s</ul>' % graphics if graphics else "",
               e(" / ".join(edit.get("cue_preview", []))), posts, e(session.id), n))


def page_html(session: Session, page_dir: Path, render_dir: Path) -> str:
    numbers = sorted(int(k) for k in session.edits)
    waiting = [n for n in numbers if review_status(session, n) != "approved"]
    done = [n for n in numbers if review_status(session, n) == "approved"]
    body = "\n".join(_item(session, n, page_dir, render_dir) for n in waiting) or \
        '<p class="sub">Nothing waiting. Every edited item is approved.</p>'
    if done:
        body += '<details><summary>Approved (%d)</summary>%s</details>' % (
            len(done), "\n".join(_item(session, n, page_dir, render_dir) for n in done))
    unrecorded = [c["n"] for c in session.cards if c["n"] not in session.recorded_cards]
    extra = ".pair{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:10px}" \
            "video{width:100%;max-height:70vh;background:#000;border-radius:8px}figure{margin:0}" \
            "figcaption{font-size:12px;color:var(--muted)}pre{white-space:pre-wrap;background:var(--chip);" \
            "padding:8px;border-radius:6px;font:13px/1.45 ui-monospace,Menlo,monospace;margin:4px 0}" \
            ".chip{font-size:12px;border:1px solid var(--line);border-radius:10px;padding:0 8px}" \
            ".chip.changed,.chip.rejected,.warn{color:var(--accent)}details{margin-top:20px}"
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>Review</title><style>%s%s</style></head><body><main>'
            '<h1>Review: session %s</h1><div class="sub">%d waiting, %d approved%s. Approve by number: '
            '<code>content-engine approve %s 1 2</code>. Send one back: <code>content-engine reject %s 3 --note '
            '"..."</code>.</div>%s</main></body></html>'
            % (CSS, extra, html.escape(session.id), len(waiting), len(done),
               ", not recorded: %s" % ", ".join(map(str, unrecorded)) if unrecorded else "",
               html.escape(session.id), html.escape(session.id), body))


def write_page(cfg: Any, session: Session) -> Path:
    page_dir = cfg.out_dir / session.id / "review"
    page_dir.mkdir(parents=True, exist_ok=True)
    path = page_dir / "index.html"
    path.write_text(page_html(session, page_dir, cfg.out_dir / session.id / "renders"))
    return path
