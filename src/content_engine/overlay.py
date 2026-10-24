"""Graphics and the subtitle track that carries captions and graphics. Pure.

Graphics come from the session's edit notes and are anchored to a phrase the creator says, so they survive re-cuts:

    {"type": "stat", "at": "day 1 7 and 14", "value": "1 · 7 · 14", "label": "follow-up days", "seconds": 3}
    {"type": "callout", "at": "pay yourself by transfer", "text": "Pay yourself by transfer", "seconds": 2.5}

A graphic starts when its phrase is said in the cut. One whose phrase was cut or never said is dropped and reported.
The hook stays on screen as a header for the whole reel, with the AI disclosure under it while the AI voice reads.

Everything goes into one ASS subtitle file (1080x1920) in three bands, so a graphic never covers a caption: header
at the top, graphics in the middle, captions low, all inside the area that platform buttons and labels leave free.
"""

from __future__ import annotations

from typing import Any

from .captions import Cue
from .trims import Edit, Word, find_phrase, map_time

W, H = 1080, 1920
SIDE = 120  # clear of the like / comment / share column
HEADER_Y = 300  # below the top 14% where the account name sits
GRAPHIC_Y = 1060
CAPTION_BASE = 1440  # nothing below this line: the caption and description overlay covers it
WHITE, GREY, ACTIVE, BOX = "&H00FFFFFF", "&H00DDDDDD", "&H000AD6FF", "&H90000000"
KINDS = ("stat", "callout")


def place(specs: list[dict[str, Any]], edit: Edit, words: list[Word], q_last: int) -> tuple[list[dict[str, Any]],
                                                                                         list[str]]:
    placed, problems = [], []
    for g in specs:
        if g.get("type") not in KINDS or not g.get("at"):
            problems.append("graphic needs type (%s) and at (a phrase): %r" % (" or ".join(KINDS), g))
            continue
        found = find_phrase(words, str(g["at"]), q_last + 1)
        start = map_time(edit.segments, words[found[0]][1]) if found else None
        if start is None:
            problems.append("graphic dropped: %r is %s" % (g["at"], "not in the cut" if found else "never said"))
            continue
        end = min(edit.duration, start + float(g.get("seconds", 2.5)))
        item = {"type": g["type"], "at": g["at"], "start": round(start, 2), "end": round(end, 2)}
        item.update({k: str(g[k]) for k in ("value", "label", "text") if g.get(k)})
        placed.append(item)
    return placed, problems


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    return "%d:%02d:%02d.%02d" % (cs // 360000, cs // 6000 % 60, cs // 100 % 60, cs % 100)


def esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _style(name: str, size: int, color: str, bold: bool, italic: bool, align: int, margin_v: int,
           border: int = 1, outline: int = 4, back: str = "&H80000000") -> str:
    return "Style: %s,Arial,%d,%s,%s,&H00000000,%s,%d,%d,0,0,100,100,0,0,%d,%d,0,%d,%d,%d,%d,1" % (
        name, size, color, color, back, -1 if bold else 0, -1 if italic else 0, border, outline, align, SIDE, SIDE,
        margin_v)


def ass_document(cues: list[Cue], graphics: list[dict[str, Any]], header: str, disclosure: str,
                 question_end: float, duration: float) -> str:
    lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: %d" % W, "PlayResY: %d" % H, "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
             "MarginL, MarginR, MarginV, Encoding",
             _style("Header", 64, WHITE, True, False, 8, HEADER_Y),
             _style("Disclosure", 34, GREY, False, True, 8, HEADER_Y + 180, outline=2),
             _style("Caption", 76, WHITE, True, False, 2, H - CAPTION_BASE, outline=5),
             _style("CaptionAI", 76, WHITE, True, True, 2, H - CAPTION_BASE, outline=5),
             _style("Stat", 120, ACTIVE, True, False, 5, 0, outline=5),
             _style("StatLabel", 48, WHITE, True, False, 5, 0, outline=3),
             _style("Callout", 56, WHITE, True, False, 5, 0, border=3, outline=18, back=BOX),
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

    def event(start: float, end: float, style: str, text: str, layer: int = 0) -> None:
        if end > start:
            lines.append("Dialogue: %d,%s,%s,%s,,0,0,0,,%s" % (layer, ass_time(start), ass_time(end), style, text))

    if header:
        event(0, duration, "Header", esc(header))
    if disclosure and question_end > 0:
        event(0, question_end, "Disclosure", esc(disclosure))
    for cue in cues:
        style = "CaptionAI" if cue.who == "ai" else "Caption"
        for i, (_, ws, _) in enumerate(cue.words):
            start = cue.start if i == 0 else ws
            end = cue.words[i + 1][1] if i + 1 < len(cue.words) else cue.end
            text = " ".join("{\\c%s&}%s{\\r}" % (ACTIVE, esc(w)) if j == i else esc(w)
                            for j, (w, _, _) in enumerate(cue.words))
            event(start, end, style, text, layer=1)
    for g in graphics:
        if g["type"] == "stat":
            event(g["start"], g["end"], "Stat", "{\\pos(%d,%d)}%s" % (W // 2, GRAPHIC_Y, esc(g.get("value", ""))), 2)
            if g.get("label"):
                event(g["start"], g["end"], "StatLabel",
                      "{\\pos(%d,%d)}%s" % (W // 2, GRAPHIC_Y + 110, esc(g["label"])), 2)
        else:
            event(g["start"], g["end"], "Callout", "{\\pos(%d,%d)}%s" % (W // 2, GRAPHIC_Y, esc(g.get("text", ""))), 2)
    return "\n".join(lines) + "\n"
