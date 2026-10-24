"""Word-by-word caption cues and the post texts. Pure.

Cues: every kept word is captioned, the AI voice's in italics and the creator's upright, at most `max_words` words or
`max_chars` characters per cue, broken at punctuation and pauses. Fillers ("um") and cut-off fragments ("pro-") are
heard but not captioned, and a restart inside one word ("ob-obviously") is captioned as the finished word. Cue
times are reel times, mapped through the cut (trims.map_time).

Post texts come from the card and what the creator actually said: the question as the first line, the first
sentences of the answer, the call to action and the AI-voice footer, fitted to each platform's limit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .textutil import sentences
from .trims import Edit, is_filler, map_time

LIMITS = {"title": 100, "full": 2200, "short": 500, "x": 280}


def is_stutter(word: str) -> bool:
    return word.rstrip('.,?!"”').endswith("-")


def display_word(w: str) -> str:
    w = re.sub(r'[.,;:]+(["”]?)$', r"\1", w)
    m = re.match(r'^(["“]?)([A-Za-z]+)-([A-Za-z].*)$', w)
    if m and m.group(3).lower().startswith(m.group(2).lower()):
        rest = m.group(3)
        w = m.group(1) + (rest[0].upper() + rest[1:] if m.group(2)[0].isupper() else rest)
    return w


@dataclass
class Cue:
    who: str  # ai | creator
    start: float
    end: float
    words: list[tuple[str, float, float]]  # display word, reel start, reel end

    @property
    def text(self) -> str:
        return " ".join(w for w, _, _ in self.words)


def build_cues(edit: Edit, max_words: int = 3, max_chars: int = 16, gap: float = 0.32) -> list[Cue]:
    timed = []
    for w, a, b, who in edit.words:
        if is_filler(w) or is_stutter(w):
            continue
        ra = map_time(edit.segments, a)
        if ra is None:
            continue
        rb = map_time(edit.segments, b)
        timed.append((display_word(w), ra, rb if rb is not None else ra + (b - a), who, w))
    groups: list[list[tuple[str, float, float, str, str]]] = []
    cur: list[tuple[str, float, float, str, str]] = []
    for t in timed:
        if cur and (len(cur) >= max_words or len(" ".join(x[0] for x in cur + [t])) > max_chars
                    or t[1] - cur[-1][2] > gap or t[3] != cur[-1][3]):
            groups.append(cur)
            cur = []
        cur.append(t)
        if re.search(r'[.?!,]["”]?$', t[4]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    cues = []
    for i, g in enumerate(groups):
        nxt = groups[i + 1][0][1] if i + 1 < len(groups) else g[-1][2] + 0.6
        start = max(0.0, g[0][1] - 0.04, cues[-1].end if cues else 0.0)
        cues.append(Cue(g[0][3], start, max(start + 0.1, min(nxt - 0.02, g[-1][2] + 0.6)),
                        [(x[0], x[1], x[2]) for x in g]))
    return cues


def answer_text(edit: Edit) -> str:
    """What the creator says in the cut, without fillers and fragments."""
    out = [w for w, _, _, who in edit.words if who == "creator" and not is_filler(w) and not is_stutter(w)]
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def _fit(head: str, body: str, tail: list[str], limit: int) -> str:
    """head, body and tail as paragraphs; whole sentences drop off the end of the body until it fits `limit`."""
    parts = sentences(body)
    while parts:
        text = "\n\n".join(p for p in [head, " ".join(parts), *tail] if p)
        if len(text) <= limit:
            return text
        parts.pop()
    rest = "\n\n".join(tail)
    room = limit - (len(rest) + 2 if rest else 0)
    if len(head) > room:
        head = head[: max(0, room - 1)].rstrip() + "…"
    return "\n\n".join(p for p in [head, *tail] if p)[:limit]


def post_texts(card: dict[str, Any], edit: Edit, cfg: Any) -> dict[str, str]:
    c = cfg["captions"]
    said = answer_text(edit)
    body = " ".join(sentences(said)[:3])
    tags = " ".join("#" + t.lstrip("#") for t in c.get("hashtags", []))
    tail = [p for p in (c["cta"], tags, c["footer"]) if p]
    return {"title": card["hook"][: LIMITS["title"]],
            "full": _fit(card["question"], body, tail, LIMITS["full"]),
            "short": _fit(card["question"], body, tail, LIMITS["short"]),
            "x": _fit(card["question"], body, [p for p in (c["footer"],) if p], LIMITS["x"])}


def validate_texts(texts: dict[str, str], banned: list[str]) -> list[str]:
    problems = []
    for kind, limit in LIMITS.items():
        if kind not in texts or not texts[kind].strip():
            problems.append("%s text is missing" % kind)
        elif len(texts[kind]) > limit:
            problems.append("%s text is %d characters (limit %d)" % (kind, len(texts[kind]), limit))
    joined = " ".join(texts.values()).lower()
    problems += ["uses the banned word %r" % b for b in banned if b and b.lower() in joined]
    return problems
