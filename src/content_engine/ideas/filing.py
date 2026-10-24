"""Ideas and filing. An idea is a topic the creator could answer on camera, defined in ideas.toml:

    [[idea]]
    id = "late-invoices"
    title = "Getting paid on time"                       # the card's hook
    question = "How do I get clients to pay on time?"    # what the AI voice reads, unless a fresh note replaces it
    context = "Hypothetical: a client is 45 days late."  # optional, read after the question
    keywords = ["late invoice", "unpaid", "net 30"]      # phrases that file evidence under this idea
    points = ["Send it the day the work ships"]          # talking points on the card
    fit = 3                                              # audience fit 0-3, scales the score

Filing is deterministic: an item goes to the idea whose keywords cover the most words of its text (longer phrases
win ties, then file order). Items no idea claims stay unfiled, and their most common words are reported as possible
new topics.
"""

from __future__ import annotations

import datetime as dt
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..store import parse_time
from ..textutil import STOPWORDS, stem, tokens, words


@dataclass
class Idea:
    id: str
    title: str
    question: str
    keywords: list[str]
    context: str = ""
    points: list[str] = field(default_factory=list)
    fit: int = 2

    def __post_init__(self) -> None:
        self._phrases = [tuple(tokens(k)) for k in self.keywords if tokens(k)]

    def match(self, text_tokens: list[str]) -> int:
        """Words of the text covered by this idea's keyword phrases."""
        hits = 0
        for phrase in self._phrases:
            n = len(phrase)
            if any(tuple(text_tokens[i:i + n]) == phrase for i in range(len(text_tokens) - n + 1)):
                hits += n
        return hits


def parse_ideas(data: dict[str, Any]) -> list[Idea]:
    ideas, seen = [], set()
    for raw in data.get("idea", []):
        iid = str(raw.get("id") or "").strip()
        if not iid or iid in seen:
            raise ValueError("every [[idea]] needs a unique id (got %r)" % iid)
        seen.add(iid)
        for key in ("title", "question", "keywords"):
            if not raw.get(key):
                raise ValueError("idea %r is missing %s" % (iid, key))
        fit = int(raw.get("fit", 2))
        if fit not in (0, 1, 2, 3):
            raise ValueError("idea %r: fit must be 0, 1, 2 or 3" % iid)
        ideas.append(Idea(iid, str(raw["title"]), str(raw["question"]), [str(k) for k in raw["keywords"]],
                          str(raw.get("context") or ""), [str(p) for p in raw.get("points") or []], fit))
    return ideas


def load_ideas(path: Path) -> list[Idea]:
    if not path.exists():
        return []
    with path.open("rb") as f:
        return parse_ideas(tomllib.load(f))


def file_item(text: str, ideas: list[Idea]) -> str | None:
    toks = tokens(text)
    best, best_hits = None, 0
    for idea in ideas:
        hits = idea.match(toks)
        if hits > best_hits:
            best, best_hits = idea.id, hits
    return best


def file_all(items: list[dict[str, Any]], ideas: list[Idea]) -> tuple[dict[str, list[dict[str, Any]]],
                                                                        list[dict[str, Any]]]:
    by_idea: dict[str, list[dict[str, Any]]] = {i.id: [] for i in ideas}
    unfiled = []
    for it in items:
        iid = file_item(it.get("text") or "", ideas)
        if iid is None:
            unfiled.append(it)
        else:
            by_idea[iid].append(it)
    return by_idea, unfiled


def unfiled_terms(unfiled: list[dict[str, Any]], now: dt.datetime, days: int = 7, min_items: int = 2,
                  top: int = 8) -> list[tuple[str, int]]:
    """Words that keep showing up in recent unfiled items: candidates for a new idea."""
    counts: Counter[str] = Counter()
    since = now - dt.timedelta(days=days)
    for it in unfiled:
        t = parse_time(it.get("published"))
        if t is not None and t < since:
            continue
        counts.update({stem(w) for w in words(it.get("text") or "") if len(w) >= 4 and w not in STOPWORDS})
    found = [(term, n) for term, n in counts.items() if n >= min_items]
    found.sort(key=lambda x: (-x[1], x[0]))
    return found[:top]
