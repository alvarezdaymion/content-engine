"""Match recorded takes to a session's cards. Pure: transcripts in, matches out.

The question is played (or read) aloud while recording, so the first words of every take are the card's question.
For each take, the first `window` words are aligned against every card's question with difflib; the score is the
share of the question's words heard in order. The best card wins when it scores at least `threshold` and beats the
runner-up by `margin`. That tolerates a take that starts mid-question and a few misheard words, and refuses to guess
between two similar questions. A take with no question (a pickup) stays unmatched. New takes supersede known
selections; otherwise the saved selection wins, even when its transcript is missing. Filename order breaks ties
among newly discovered takes. Correcting an unmatched take makes it eligible to replace the selection.
"""

from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass
from typing import Any

from .textutil import tokens

FILLERS = frozenset({"uh", "um", "uhm", "erm", "er", "ah", "hmm", "mm"})


@dataclass
class Match:
    take: str
    status: str  # matched | superseded | unmatched
    card: int | None = None
    score: float = 0.0
    q_first: int = -1  # index of the first word of the question in the take's transcript
    q_last: int = -1  # index of the last word the AI voice read (question, then context)
    q_start: float = 0.0
    q_end: float = 0.0
    reason: str = ""
    by: str = ""
    missing: bool = False

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def tokenize(words: list[tuple[str, float, float]], limit: int | None = None) -> tuple[list[str], list[int]]:
    """Matching tokens and, for each, the index of the transcript word it came from. Fillers are skipped."""
    toks, idx = [], []
    for i, (w, _, _) in enumerate(words):
        for t in tokens(w):
            if t not in FILLERS:
                toks.append(t)
                idx.append(i)
        if limit is not None and len(toks) >= limit:
            break
    return toks, idx


def align(q: list[str], t: list[str]) -> tuple[float, int, int]:
    """(share of q heard in order in t, first token index, last token index). Only blocks within one reading span
    count, so words of the question repeated later in the answer don't stretch the match."""
    if not q or not t:
        return 0.0, -1, -1
    blocks = [b for b in difflib.SequenceMatcher(None, q, t, autojunk=False).get_matching_blocks() if b.size]
    if not blocks:
        return 0.0, -1, -1
    span = int(len(q) * 1.5) + 3
    best = (0, -1, -1)
    for i, first in enumerate(blocks):
        inside = [b for b in blocks[i:] if b.b < first.b + span]
        got = sum(b.size for b in inside)
        if got > best[0]:
            best = (got, first.b, inside[-1].b + inside[-1].size - 1)
    return best[0] / len(q), best[1], best[2]


def match_take(name: str, words: list[tuple[str, float, float]], cards: list[dict[str, Any]],
               threshold: float = 0.6, margin: float = 0.15, window: int = 80) -> Match:
    toks, idx = tokenize(words, window)
    scored = []
    for card in cards:
        s, i0, i1 = align(tokens(card["question"]), toks)
        scored.append((round(s, 3), -card["n"], i0, i1, card))
    if not scored:
        return Match(name, "unmatched", reason="the session has no cards")
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best = scored[0]
    if best[0] < threshold:
        return Match(name, "unmatched", score=best[0], reason="no card's question heard at the start (best: card "
                     "%d at %.2f)" % (-best[1], best[0]))
    if len(scored) > 1 and best[0] - scored[1][0] < margin:
        return Match(name, "unmatched", score=best[0], reason="ambiguous: cards %d and %d both match"
                     % (-best[1], -scored[1][1]))
    i1 = best[3]
    context = tokens(best[4].get("context") or "")
    if context:  # the AI voice reads the context line right after the question
        cs, _, c1 = align(context, toks[i1 + 1:i1 + 1 + len(context) * 2 + 3])
        if cs >= threshold:
            i1 = i1 + 1 + c1
    first, last = idx[best[2]], idx[i1] if i1 < len(idx) else idx[-1]
    return Match(name, "matched", card=best[4]["n"], score=best[0], q_first=first, q_last=last,
                 q_start=words[first][1], q_end=words[last][2])


def match_takes(cards: list[dict[str, Any]], takes: dict[str, list[tuple[str, float, float]]],
                threshold: float = 0.6, margin: float = 0.15, window: int = 80,
                previous: dict[str, dict[str, Any]] | None = None) -> dict[str, Match]:
    history = previous or {}
    results = {name: match_take(name, words, cards, threshold, margin, window) for name, words in sorted(takes.items())}
    for name, old in sorted(history.items()):
        if name not in results and old.get("status") in ("matched", "superseded"):
            saved = {k: v for k, v in old.items() if k in Match.__dataclass_fields__}
            results[name] = Match(**{**saved, "take": name, "missing": True})
    by_card: dict[int, list[str]] = {}
    for name, m in results.items():
        if m.status in ("matched", "superseded") and m.card is not None:
            by_card.setdefault(m.card, []).append(name)
    for card, names in by_card.items():
        fresh = [name for name in names if not results[name].missing and results[name].status == "matched"
                 and (name not in history or history[name].get("card") != card
                      or history[name].get("status") == "unmatched")]
        selected = [name for name in names if results[name].status == "matched"
                    and history.get(name, {}).get("status") == "matched"]
        candidates = fresh or selected
        winner = candidates[-1] if candidates else None
        for name in names:
            if name != winner:
                results[name].status = "superseded"
                results[name].by = winner or str(history.get(name, {}).get("by") or "")
    return dict(sorted(results.items()))
