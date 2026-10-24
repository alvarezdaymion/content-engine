"""The evidence store: every collected item, keyed by id, in state/evidence.json.

An item is a dict:
    id, source, kind (creator | ad | thread | news | own_post | own_comment | question), text, url, author,
    published (ISO), views, baseline, multiple, engagement, days_live, versions, collected (ISO)

Collecting the same item again refreshes its numbers and keeps its first `collected` time. Items older than
`ideas.keep_days` are dropped. Filing is not stored: it is recomputed from ideas.toml on every ranking.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from ..store import iso, parse_time, read_json, write_json

REFRESH = ("text", "url", "author", "views", "baseline", "multiple", "engagement", "days_live", "versions")


class EvidenceStore:
    def __init__(self, path: Path):
        self.path = path
        self.items: dict[str, dict[str, Any]] = (read_json(path, {}) or {}).get("items", {})

    def merge(self, items: list[dict[str, Any]], now: dt.datetime) -> int:
        """Add or refresh items. Returns how many were new."""
        new = 0
        for it in items:
            old = self.items.get(it["id"])
            if old is None:
                self.items[it["id"]] = dict(it, collected=iso(now))
                new += 1
            else:
                old.update({k: it[k] for k in REFRESH if it.get(k) is not None})
        return new

    def prune(self, now: dt.datetime, keep_days: float) -> int:
        cutoff = now - dt.timedelta(days=keep_days)
        old = [k for k, it in self.items.items()
               if (parse_time(it.get("published")) or parse_time(it.get("collected")) or now) < cutoff]
        for k in old:
            del self.items[k]
        return len(old)

    def all(self) -> list[dict[str, Any]]:
        return sorted(self.items.values(), key=lambda it: (it.get("published") or "", it["id"]))

    def save(self) -> None:
        write_json(self.path, {"items": self.items})
