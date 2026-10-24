"""Text helpers shared by the idea bank, matching and captions.

Collected public posts can carry emails, phone numbers and HTML, so everything collected goes through `scrub`
before it is stored or shown.
"""

from __future__ import annotations

import html
import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]*[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w/])(?:\+?\d[\s.\-]?)?(?:\(\d{3}\)|\d{3})[\s.\-]?\d{3}[\s.\-]?\d{4}(?![\w/])")
TAG = re.compile(r"<[^>]+>")
SPACE = re.compile(r"\s+")
WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")

STOPWORDS = frozenset("""
a about after again all also am an and any are as at be because been before being but by can could did do does
doing don't down during each even every few for from get gets getting go going got had has have having he her here
hers him his how i i'm if in into is isn't it it's its just know let like make many may me more most much my need
no not now of off on once one only or other our out over own really said same say says she should so some still
such take than that that's the their them then there these they thing things think this those through to too
under until up us very want was way we well were what when where which while who why will with would you you're
your yours
""".split())


def scrub(text: str | None) -> str:
    """Drop HTML tags and entities, replace emails and phone numbers, collapse whitespace."""
    if not text:
        return ""
    text = html.unescape(TAG.sub(" ", str(text)))
    text = EMAIL.sub("[email]", text)
    text = PHONE.sub("[phone]", text)
    return SPACE.sub(" ", text).strip()


def clip(text: str | None, n: int) -> str:
    text = scrub(text)
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def words(text: str) -> list[str]:
    """Lowercase words and numbers, apostrophes inside words kept."""
    return WORD.findall(text.lower().replace("’", "'"))


def stem(word: str) -> str:
    """A light fold applied the same way to keywords and text: owner's -> owner, receipts -> receipt."""
    if word.endswith("'s"):
        word = word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word


def tokens(text: str) -> list[str]:
    return [stem(w) for w in words(text)]


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", SPACE.sub(" ", text).strip())
    return [p for p in parts if p]
