"""The idea bank: collect evidence from the sources that are due, file it under the ideas in ideas.toml, rank them
(rank.py) and write out/ideas.json. It never plans anything: a recording session pulls ideas in (session.py).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..store import iso, write_json
from ..textutil import clip
from .evidence import EvidenceStore
from .filing import Idea, file_all, load_ideas, unfiled_terms
from .rank import rank
from .sources import run_due
from .sources.base import item_id

__all__ = ["Idea", "add_note", "ranked", "refresh"]


def _store(cfg: Any) -> EvidenceStore:
    return EvidenceStore(cfg.state_dir / "evidence.json")


def ranked(cfg: Any, now: dt.datetime, usage: dict[str, str] | None = None) -> dict[str, Any]:
    """Rank the stored evidence without collecting. `usage` maps idea id -> when a session took it."""
    store = _store(cfg)
    topics = load_ideas(cfg.path("ideas"))
    by_idea, unfiled = file_all(store.all(), topics)
    ic = cfg["ideas"]
    ideas, cooling = rank(topics, by_idea, now, usage, float(ic["cooldown_days"]), float(ic["trending_hours"]))
    return {"ideas": ideas, "cooling": cooling, "topics": topics, "by_idea": by_idea, "unfiled": unfiled,
            "unfiled_terms": unfiled_terms(unfiled, now), "items": len(store.items)}


def refresh(cfg: Any, now: dt.datetime, usage: dict[str, str] | None = None, force: bool = False,
            built: dict[str, Any] | None = None, transport: Any = None) -> dict[str, Any]:
    """Run the due sources, prune old evidence, rank, and write out/ideas.json."""
    store = _store(cfg)
    rows = run_due(cfg, store, now, force, built, transport)
    store.prune(now, float(cfg["ideas"]["keep_days"]))
    store.save()
    result = ranked(cfg, now, usage)
    result["sources"] = rows
    write_json(cfg.out_dir / "ideas.json", snapshot(result, now))
    return result


def snapshot(result: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    keep = ("kind", "source", "text", "url", "author", "published", "views", "multiple", "points")
    filed = sum(len(v) for v in result["by_idea"].values())
    return {
        "generated_at": iso(now),
        "sources": result.get("sources", []),
        "totals": {"items": result["items"], "filed": filed, "unfiled": len(result["unfiled"]),
                   "ideas": len(result["ideas"]), "trending": sum(1 for i in result["ideas"] if i["trending"])},
        "ideas": [{**{k: i[k] for k in ("rank", "id", "title", "question", "score", "parts", "trending", "counts")},
                   "evidence": [{k: e.get(k) for k in keep} for e in i["evidence"][:5]]} for i in result["ideas"]],
        "cooling": result["cooling"],
        "unfiled_terms": result["unfiled_terms"],
    }


def add_note(cfg: Any, text: str, now: dt.datetime) -> dict[str, Any]:
    """A question from a call or a message: the heaviest evidence there is (3x, 60-day half-life)."""
    text = clip(text, 400)
    if not text:
        raise ValueError("the note is empty")
    item = {"id": item_id("note", text), "source": "notes", "kind": "question", "text": text,
            "published": iso(now)}
    store = _store(cfg)
    store.merge([item], now)
    store.save()
    return item
