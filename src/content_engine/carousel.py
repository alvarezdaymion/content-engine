"""Carousel slide text from a posted reel's approved caption. Pure.

Slide 1 is the caption's first paragraph (the question). The answer's sentences follow, grouped into slides of at
most `max_words` words, and the last slide is the configured closing line. Every slide except the closing line is
word for word from the approved caption (`check`), so a carousel never says anything that wasn't approved.
Rendering slide images and posting carousels are not part of this module.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .textutil import sentences


def _norm(text: str) -> str:
    return " ".join(text.split())


def slides(texts: dict[str, str], cfg: dict[str, Any], skip: tuple[str, ...] = ()) -> list[str]:
    """`skip` holds caption paragraphs that are not answer text (call to action, hashtags, footer)."""
    paras = [p.strip() for p in texts["full"].split("\n\n") if p.strip() and p.strip() not in skip]
    if not paras:
        return []
    out = [paras[0]]
    group: list[str] = []
    for s in sentences(" ".join(paras[1:])):
        if group and len(" ".join(group + [s]).split()) > int(cfg["max_words"]):
            out.append(" ".join(group))
            group = []
        group.append(s)
    if group:
        out.append(" ".join(group))
    out = out[: max(1, int(cfg["max_slides"]) - (1 if cfg.get("closing") else 0))]
    if cfg.get("closing"):
        out.append(cfg["closing"])
    return out


def check(slide_texts: list[str], approved: str, closing: str = "") -> list[str]:
    source = _norm(approved)
    problems = []
    for i, s in enumerate(slide_texts, 1):
        if closing and i == len(slide_texts) and s == closing:
            continue
        if _norm(s) not in source:
            problems.append("slide %d is not word for word from the approved caption" % i)
    return problems


def build(texts: dict[str, str], cfg: Any) -> dict[str, Any]:
    c = cfg["captions"]
    skip = tuple(p for p in (c["cta"], c["footer"], " ".join("#" + t.lstrip("#") for t in c["hashtags"])) if p)
    s = slides(texts, cfg["carousel"], skip)
    return {"slides": s, "problems": check(s, texts["full"], cfg["carousel"].get("closing", "")),
            "caption_fingerprint": hashlib.sha256(texts["full"].encode()).hexdigest()[:16]}
