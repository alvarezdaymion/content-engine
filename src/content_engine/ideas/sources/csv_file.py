"""CSV import: your own numbers, competitor ads, or anything another tool can export (read-only).

Options ([[sources]] with type = "csv"):
  path         the CSV file, relative to content-engine.toml (required)
  kind         the evidence kind for every row, unless the file has a `kind` column
  every_hours  how often it is re-read (default 6)

Columns (all optional except text): kind, id, date, text, url, author, views, normal, multiple, engagement,
days_live, versions. Kinds: creator, ad, thread, news, own_post, own_comment, question.

A row's multiple is the `multiple` column, else views / `normal`. For own_post rows with neither, the normal is the
median views of the own posts in the same file (at least 3), so a post at 2x is twice your usual.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Any

from ...store import iso, parse_time
from ...textutil import clip
from ..rank import KINDS
from .base import Collected, item_id, median, multiple


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


class CsvSource:
    def __init__(self, name: str, path: Path, kind: str = "", every_hours: float = 6):
        self.name, self.path, self.kind, self.every_hours = name, Path(path), kind, float(every_hours)

    def collect(self, now: dt.datetime) -> Collected:
        with self.path.open(newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        valid = [(r, (r.get("kind") or self.kind).strip(), clip(r.get("text"), 400)) for r in rows]
        valid = [(r, kind, text) for r, kind, text in valid if kind in KINDS and text]
        own_normal = median([_num(r.get("views")) for r, kind, _ in valid if kind == "own_post"])
        items = []
        for r, kind, text in valid:
            views = _num(r.get("views"))
            normal = _num(r.get("normal")) or (own_normal if kind == "own_post" else None)
            mult = _num(r.get("multiple"))
            published = parse_time(r.get("date"))
            url = (r.get("url") or "").strip()
            rid = item_id(self.name, "id", r["id"]) if r.get("id") else item_id(self.name, url or text, r.get("date"))
            items.append({"id": rid, "source": self.name,
                          "kind": kind, "text": text, "url": url if url.startswith(("https://", "http://")) else None,
                          "author": clip(r.get("author"), 80) or None, "published": iso(published),
                          "views": int(views) if views is not None else None, "baseline": normal,
                          "multiple": mult if mult is not None else multiple(views, normal),
                          "engagement": _num(r.get("engagement")), "days_live": _num(r.get("days_live")),
                          "versions": _num(r.get("versions"))})
        skipped = len(rows) - len(valid)
        return Collected(items, "%d rows%s" % (len(rows), ", %d skipped" % skipped if skipped else ""))
