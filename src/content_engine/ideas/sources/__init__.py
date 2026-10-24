"""Build the configured idea sources and run the ones that are due.

Each [[sources]] entry in content-engine.toml names a `type`:
  youtube   YouTube Data API search (real connector, sources/youtube.py)
  news      Google News RSS search (real connector, sources/news.py)
  csv       a CSV export from any other tool: your own posts, comments, competitor ads (sources/csv_file.py)
  demo      synthetic data for `content-engine demo` (sources/demo.py)
Questions from calls and messages come in with `content-engine note`, not a source.

A source runs when `every_hours` have passed since its last success (default 6). A failing source records its error
and keeps its earlier evidence; it never stops the other sources.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from ...store import iso, parse_time, read_json, write_json
from ..evidence import EvidenceStore
from .base import Collected, Source

__all__ = ["Collected", "Source", "build_source", "run_due"]


def build_source(spec: dict[str, Any], root: Path, transport: Any = None, now: dt.datetime | None = None) -> Source:
    kind, name = spec.get("type"), spec["name"]
    every = float(spec.get("every_hours", 6))
    if kind == "youtube":
        from .youtube import YouTubeSource
        return YouTubeSource(name, spec["queries"], every, int(spec.get("days", 7)), int(spec.get("per_query", 15)),
                             str(spec.get("duration", "any")), transport=transport)
    if kind == "news":
        from .news import NewsSource
        return NewsSource(name, spec["queries"], every, int(spec.get("days", 3)), int(spec.get("per_query", 10)),
                          str(spec.get("language", "en-US")), transport=transport)
    if kind == "csv":
        from .csv_file import CsvSource
        return CsvSource(name, root / spec["path"], str(spec.get("kind", "")), every)
    if kind == "demo":
        from .demo import DemoSource
        return DemoSource(name, str(spec.get("group", name)), every)
    raise ValueError("unknown source type %r for %r" % (kind, name))


def due(last_success: str | None, every_hours: float, now: dt.datetime) -> bool:
    t = parse_time(last_success)
    return t is None or (now - t).total_seconds() >= every_hours * 3600 - 600  # 10 minutes of scheduler slack


def run_due(cfg: Any, store: EvidenceStore, now: dt.datetime, force: bool = False,
            built: dict[str, Source] | None = None, transport: Any = None) -> list[dict[str, Any]]:
    """Run every due source into the store. Returns one status row per configured source."""
    state_path = cfg.state_dir / "sources.json"
    state = read_json(state_path, {}) or {}
    rows = []
    for spec in cfg["sources"]:
        name = spec["name"]
        st = state.setdefault(name, {})
        row = {"name": name, "type": spec.get("type"), "ran": False, "items": 0, "new": 0, "note": "",
               "error": st.get("last_error"), "last_success": st.get("last_success")}
        if force or due(st.get("last_success"), float(spec.get("every_hours", 6)), now):
            row["ran"] = True
            try:
                src = (built or {}).get(name) or build_source(spec, cfg.root, transport, now)
                got = src.collect(now)
                row.update(items=len(got.items), new=store.merge(got.items, now), note=got.note, error=None,
                           last_success=iso(now))
                st.update(last_success=iso(now), last_error=None, last_items=len(got.items))
            except Exception as e:  # isolate every source
                row["error"] = "%s: %s" % (type(e).__name__, str(e)[:240])
                st.update(last_error=row["error"], last_failure=iso(now))
        rows.append(row)
    write_json(state_path, state)
    return rows
