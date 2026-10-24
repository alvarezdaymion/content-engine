"""The idea score. Pure functions, no I/O; every constant is here and tested in tests/test_rank.py.

Each piece of evidence filed under an idea adds points to one of three parts:

creators  proof the idea performs for others.
          creator post:   r(7) x lift(multiple)               multiple = views / that creator's normal views
                          r(7) x 0.5 x (1 + log10(1 + views / 10,000))      when the normal is unknown
          competitor ad:  r(30) x (1 + log2(days live / 14)) x (1 + 0.3 x log2(versions))
                          (an ad still running after two weeks, in several versions, is an angle someone pays for)
market    people asking or talking about it.
          thread:         r(7) x (1 + log10(1 + replies and votes))
          news story:     2 x r(3)
ours      the creator's own audience, weighted highest.
          own post:       2 x r(30) x lift(multiple)          multiple = views / the creator's median views
          own comment:    2 x r(30)
          question:       3 x r(60)                           asked on a call or in a message (`content-engine note`)

r(h) = 0.5 ^ (age in days / h), so evidence halves in value every h days (an unknown date counts 0.5).
lift(m) = 1 + log2(m) for m above 1, capped at 32x so one freak post can't outweigh everything; 1 otherwise.
Several different creators on one idea multiply the creators part by 1 + 0.2 per extra creator (at most 2x).

score = 100 x (1 - e^(-raw / 25)) x fit factor, where raw = creators + market + ours and the fit factor is
1.0 / 0.85 / 0.6 / 0.3 for audience fit 3 / 2 / 1 / 0. Strong evidence saturates toward 100 instead of running away.

Trending: at least half of the points arrived in the last `trending_hours`, with a 3x+ creator post or 3+ items in
that window. An idea a session took in the last `cooldown_days` leaves the ranking; after that only evidence newer
than the session counts, so it comes back only if something new happened.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any

from ..store import parse_time

HALF_LIFE_DAYS = {"creator": 7.0, "ad": 30.0, "thread": 7.0, "news": 3.0, "own_post": 30.0, "own_comment": 30.0,
                  "question": 60.0}
PART = {"creator": "creators", "ad": "creators", "thread": "market", "news": "market", "own_post": "ours",
        "own_comment": "ours", "question": "ours"}
KINDS = tuple(PART)
FIT = {3: 1.0, 2: 0.85, 1: 0.6, 0: 0.3}
SATURATION = 25.0
MAX_MULTIPLE = 32.0


def recency(published: Any, now: dt.datetime, half_life_days: float) -> float:
    t = parse_time(published)
    if t is None:
        return 0.5
    age = max(0.0, (now - t).total_seconds() / 86400)
    return 0.5 ** (age / half_life_days)


def lift(multiple: float | None) -> float:
    if not multiple or multiple <= 1:
        return 1.0
    return 1 + math.log2(min(float(multiple), MAX_MULTIPLE))


def points(item: dict[str, Any], now: dt.datetime) -> tuple[str, float]:
    """(part, points) for one piece of evidence."""
    kind = item.get("kind", "")
    if kind not in PART:
        return "market", 0.0
    r = recency(item.get("published"), now, HALF_LIFE_DAYS[kind])
    if kind == "creator":
        if item.get("multiple") is not None:
            return "creators", r * lift(item["multiple"])
        return "creators", r * 0.5 * (1 + math.log10(1 + float(item.get("views") or 0) / 10_000))
    if kind == "ad":
        days = float(item.get("days_live") or 14)
        versions = max(1.0, float(item.get("versions") or 1))
        return "creators", r * (1 + math.log2(max(1.0, days / 14))) * (1 + 0.3 * math.log2(versions))
    if kind == "thread":
        return "market", r * (1 + math.log10(1 + float(item.get("engagement") or 0)))
    if kind == "news":
        return "market", 2 * r
    if kind == "own_post":
        return "ours", 2 * r * lift(item.get("multiple"))
    if kind == "own_comment":
        return "ours", 2 * r
    return "ours", 3 * r  # question


def score_idea(fit: int, items: list[dict[str, Any]], now: dt.datetime, trending_hours: float = 72) -> dict[str, Any]:
    parts = {"creators": 0.0, "market": 0.0, "ours": 0.0}
    creators: set[str] = set()
    total = recent = 0.0
    recent_n, recent_hot = 0, False
    scored = []
    for it in items:
        part, p = points(it, now)
        parts[part] += p
        total += p
        scored.append((p, it))
        if it.get("kind") == "creator" and it.get("author"):
            creators.add("%s/%s" % (it.get("source"), it["author"]))
        t = parse_time(it.get("published"))
        if t is not None and (now - t).total_seconds() <= trending_hours * 3600:
            recent += p
            recent_n += 1
            recent_hot |= it.get("kind") == "creator" and float(it.get("multiple") or 0) >= 3
    parts["creators"] *= min(2.0, 1 + 0.2 * max(0, len(creators) - 1))
    raw = sum(parts.values())
    score = 100 * (1 - math.exp(-raw / SATURATION)) * FIT.get(int(fit), FIT[0])
    trending = total > 0 and recent >= total / 2 and (recent_hot or recent_n >= 3)
    scored.sort(key=lambda x: -x[0])
    counts: dict[str, int] = {}
    for it in items:
        counts[it.get("kind", "")] = counts.get(it.get("kind", ""), 0) + 1
    return {"score": round(score, 1), "parts": {k: round(v, 1) for k, v in parts.items()}, "raw": round(raw, 2),
            "trending": trending, "creators": len(creators), "counts": counts,
            "evidence": [dict(it, points=round(p, 2)) for p, it in scored]}


def rank(ideas: list[Any], items_by_idea: dict[str, list[dict[str, Any]]], now: dt.datetime,
         usage: dict[str, str] | None = None, cooldown_days: float = 21,
         trending_hours: float = 72) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(ranked ideas, cooling ideas). `usage` maps idea id -> when a session took it."""
    usage = usage or {}
    out, cooling = [], []
    for idea in ideas:
        items = items_by_idea.get(idea.id, [])
        used = parse_time(usage.get(idea.id))
        if used is not None:
            until = used + dt.timedelta(days=cooldown_days)
            if now < until:
                cooling.append({"id": idea.id, "title": idea.title, "until": until.isoformat(timespec="minutes")})
                continue
            items = [i for i in items if (parse_time(i.get("published")) or used) > used]
        if not items:
            continue
        out.append({"id": idea.id, "title": idea.title, "question": idea.question, "fit": idea.fit,
                    **score_idea(idea.fit, items, now, trending_hours)})
    out.sort(key=lambda r: (-r["score"], r["id"]))
    for n, r in enumerate(out, 1):
        r["rank"] = n
    return out, cooling
