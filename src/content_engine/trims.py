"""Trims: which seconds of a matched take survive, and where they land in the reel. Pure: no ffmpeg needed.

The reel is the AI voice reading the question (kept as one piece), then the answer:
- everything before the question and after the last answer word goes;
- a pause longer than `max_gap` seconds becomes a jump cut;
- phrases and time ranges listed in the session's edit notes are cut (false starts, restatements);
- fillers ("um", "uh") stay in the audio and out of the captions, unless `cut_fillers` is on.
Each kept span gets `pad` seconds of air on each side, but never reaches into a removed word.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .matching import FILLERS, tokenize
from .textutil import tokens

Word = tuple[str, float, float]


@dataclass
class Segment:
    a: float  # source seconds
    b: float
    kind: str  # question | answer
    o: float = 0.0  # where it starts in the reel

    @property
    def d(self) -> float:
        return self.b - self.a


@dataclass
class Edit:
    take: str
    segments: list[Segment]
    words: list[tuple[str, float, float, str]]  # kept words: text, source start, source end, "ai" | "creator"
    removed: list[dict[str, Any]] = field(default_factory=list)
    duration_raw: float = 0.0
    duration: float = 0.0
    problems: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {"take": self.take,
                "segments": [[round(s.a, 3), round(s.b, 3), s.kind] for s in self.segments],
                "removed": self.removed, "duration_raw": round(self.duration_raw, 2),
                "duration": round(self.duration, 2), "problems": self.problems}


def is_filler(word: str) -> bool:
    return all(t in FILLERS for t in tokens(word)) and bool(tokens(word))


def find_phrase(words: list[Word], phrase: str, start: int = 0) -> tuple[int, int] | None:
    """(first, last) word indices of `phrase` at or after word `start`. Fillers and punctuation are ignored."""
    target = [t for t in tokens(phrase) if t not in FILLERS]
    toks, idx = tokenize(words[start:])
    n = len(target)
    if not n:
        return None
    for i in range(len(toks) - n + 1):
        if toks[i:i + n] == target:
            return start + idx[i], start + idx[i + n - 1]
    return None


def plan_edit(take: str, words: list[Word], q_first: int, q_last: int, notes: dict[str, Any] | None,
              cfg: dict[str, Any]) -> Edit:
    notes = notes or {}
    max_gap, pad, lead_in = float(cfg["max_gap"]), float(cfg["pad"]), float(cfg["lead_in"])
    answer = range(q_last + 1, len(words))
    gone: set[int] = set()
    removed: list[dict[str, Any]] = []
    problems: list[str] = []

    def cut(first: int, last: int, kind: str) -> None:
        idx = [i for i in range(first, last + 1) if i not in gone]
        if idx:
            gone.update(idx)
            removed.append({"kind": kind, "a": round(words[idx[0]][1], 2), "b": round(words[idx[-1]][2], 2),
                            "text": " ".join(words[i][0] for i in idx)})

    for rng in notes.get("cut", []):
        hit = [i for i in answer if words[i][1] < float(rng[1]) and words[i][2] > float(rng[0])]
        if hit:
            cut(hit[0], hit[-1], "range")
        else:
            problems.append("cut range %s has no words after the question" % list(rng))
    for phrase in notes.get("cut_phrases", []):
        found = find_phrase(words, phrase, q_last + 1)
        if found is None:
            problems.append("cut phrase not found: %r" % phrase)
        else:
            cut(found[0], found[1], "phrase")
    if notes.get("cut_fillers", cfg.get("cut_fillers", False)):
        for i in answer:
            if i not in gone and is_filler(words[i][0]):
                cut(i, i, "filler")

    runs: list[list[int]] = []
    for i in (i for i in answer if i not in gone):
        prev = runs[-1][-1] if runs else None
        if prev is not None and i == prev + 1 and words[i][1] - words[prev][2] <= max_gap:
            runs[-1].append(i)
            continue
        if prev is not None and i == prev + 1:
            removed.append({"kind": "pause", "a": round(words[prev][2], 2), "b": round(words[i][1], 2),
                            "text": "%.1f s pause" % (words[i][1] - words[prev][2])})
        runs.append([i])

    qa = max(0.0, words[q_first][1] - lead_in)
    qb = words[q_last][2] + pad
    if q_last + 1 < len(words):
        qb = min(qb, words[q_last + 1][1])
    segments = [Segment(qa, qb, "question")]
    for run in runs:
        first, last = run[0], run[-1]
        lo = max(words[first][1] - pad, words[first - 1][2], segments[-1].b)
        hi = words[last][2] + pad
        if last + 1 < len(words):
            hi = min(hi, words[last + 1][1])
        if hi - lo > 0.05:  # overlapping word timings can leave nothing between the bounds
            segments.append(Segment(lo, hi, "answer"))
    t = 0.0
    for s in segments:
        s.o = t
        t += s.d

    kept_words = [(w, a, b, "ai") for w, a, b in words[q_first:q_last + 1]]
    kept_words += [(words[i][0], words[i][1], words[i][2], "creator") for run in runs for i in run]
    n_answer = sum(1 for run in runs for i in run if not is_filler(words[i][0]))
    if n_answer < int(cfg.get("min_answer_words", 8)):
        problems.append("the answer is only %d words: check the match or re-record" % n_answer)
    raw = (words[-1][2] if len(words) > q_last + 1 else qb) - qa
    removed.sort(key=lambda r: r["a"])
    return Edit(take, segments, kept_words, removed, raw, t, problems)


def map_time(segments: list[Segment], s: float) -> float | None:
    """Source second -> reel second, or None when that moment was cut."""
    for seg in segments:
        if seg.a - 1e-6 <= s <= seg.b + 1e-6:
            return seg.o + (min(max(s, seg.a), seg.b) - seg.a)
    return None
