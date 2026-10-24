"""Question cards and the recording sheet.

A card is what the creator answers on camera: a hook (the on-screen title), the question the AI voice reads, an
optional context line read right after it, talking points to glance at, plus what the audience asked recently and
why the idea ranked. The question is the idea's own question, unless someone asked it on a call or in a message
lately (`content-engine note`): then the AI voice reads that real question.
"""

from __future__ import annotations

import html
from typing import Any

from .ideas.filing import Idea
from .textutil import words

LABELS = {"creator": "creator post", "ad": "competitor ad", "thread": "thread", "news": "news story",
          "own_post": "own post", "own_comment": "comment", "question": "question"}


def why(entry: dict[str, Any]) -> str:
    parts = []
    for kind, label in LABELS.items():
        n = entry.get("counts", {}).get(kind, 0)
        if n:
            parts.append("%d %s%s" % (n, label, "" if n == 1 else "s"))
    best = max((e.get("multiple") or 0 for e in entry.get("evidence", []) if e.get("kind") == "creator"), default=0)
    if best >= 1.5:
        parts.append("best creator post %.1fx" % best)
    return "score %.1f%s: %s" % (entry.get("score", 0), ", trending" if entry.get("trending") else "",
                                 ", ".join(parts) or "no evidence")


def write_card(n: int, topic: Idea, entry: dict[str, Any] | None) -> dict[str, Any]:
    evidence = (entry or {}).get("evidence", [])
    notes = sorted((e for e in evidence if e.get("kind") == "question"), key=lambda e: e.get("published") or "")
    question = notes[-1]["text"] if notes else topic.question
    asked = []
    for e in evidence:
        text = e.get("text") or ""
        if e.get("kind") in ("question", "own_comment", "thread") and text.endswith("?") and text != question \
                and text not in asked:
            asked.append(text)
    return {"n": n, "idea": topic.id, "hook": topic.title, "question": question, "context": topic.context,
            "points": list(topic.points), "asked": asked[:3], "why": why(entry or {})}


def spoken(card: dict[str, Any]) -> str:
    """Exactly what the AI voice reads: the question, then the context line."""
    return " ".join(p for p in (card["question"], card.get("context", "")) if p)


def validate(cards: list[dict[str, Any]], cfg: Any) -> list[str]:
    c = cfg["cards"]
    banned = [b.lower() for b in c["banned_words"]]
    problems = []
    for card in cards:
        n = card["n"]
        if len(card["hook"]) > int(c["max_hook_chars"]):
            problems.append("%s: hook is %d characters (max %s)" % (n, len(card["hook"]), c["max_hook_chars"]))
        q = card["question"].strip()
        if not q.endswith("?"):
            problems.append("%s: the question should end with a question mark" % n)
        if len(words(q)) > int(c["max_question_words"]):
            problems.append("%s: the question is %d words (max %s)" % (n, len(words(q)), c["max_question_words"]))
        if len(card.get("context", "")) > int(c["max_context_chars"]):
            problems.append("%s: context is over %s characters" % (n, c["max_context_chars"]))
        if len(card.get("points", [])) > int(c["max_points"]):
            problems.append("%s: more than %s talking points" % (n, c["max_points"]))
        text = " ".join([card["hook"], q, card.get("context", ""), *card.get("points", [])]).lower()
        problems += ["%s: uses the banned word %r" % (n, b) for b in banned if b and b in text]
    return problems


CSS = """:root{--bg:#fff;--fg:#111;--muted:#666;--line:#e3e3e3;--accent:#4F070D;--chip:#f4f4f4}
@media (prefers-color-scheme:dark){:root{--bg:#0b0b0b;--fg:#eee;--muted:#999;--line:#262626;--accent:#E5484D;
--chip:#171717}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:16px}h1{font-size:22px;margin:12px 0 4px}
.sub{color:var(--muted);font-size:14px;margin:0 0 12px}
.card{border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.top{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.num{background:var(--accent);color:#fff;border-radius:6px;padding:0 8px;font-weight:700}
.hook{font-weight:700;font-size:18px}.lab{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;
letter-spacing:.05em;margin-top:10px}p{margin:2px 0}ul{margin:4px 0 0;padding-left:20px}
.why{color:var(--muted);font-size:13px;margin-top:8px}audio{width:100%;margin-top:8px}
code{background:var(--chip);padding:1px 5px;border-radius:4px}"""


def sheet_html(session: Any, audio: dict[int, str] | None = None) -> str:
    """The recording sheet: one numbered card per question, with the AI question audio when it exists."""
    e = html.escape
    out = []
    for card in session.cards:
        n = card["n"]
        player = '<audio controls preload="none" src="%s"></audio>' % e(audio[n]) if audio and n in audio else ""
        asked = "".join("<li>%s</li>" % e(a) for a in card.get("asked", []))
        points = "".join("<li>%s</li>" % e(p) for p in card.get("points", []))
        out.append(
            '<section class="card" id="c%d"><div class="top"><span class="num">%d</span>'
            '<span class="hook">%s</span></div>%s'
            '<span class="lab">Question (read by the AI voice)</span><p>%s</p>%s%s%s'
            '<div class="why">%s</div></section>'
            % (n, n, e(card["hook"]), player, e(card["question"]),
               '<span class="lab">Context</span><p>%s</p>' % e(card["context"]) if card.get("context") else "",
               '<span class="lab">Talking points</span><ul>%s</ul>' % points if points else "",
               '<span class="lab">Asked recently</span><ul>%s</ul>' % asked if asked else "",
               e(card.get("why", ""))))
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><title>Recording sheet</title><style>%s</style></head>'
            '<body><main><h1>Recording sheet: session %s</h1><div class="sub">Recording on %s. Play each '
            'question, then answer it like a real call. Keep the question audible in the take so it can be '
            'matched to its card.</div>%s</main></body></html>'
            % (CSS, e(session.id), e(session.record_on), "\n".join(out)))
