"""The publish ledger: what makes posting at most once per platform.

One JSON line per event, append-only, fsynced before the next step, mode 0600. Events for one item on one platform:

    attempt     written before anything is sent ("by": daily or repair)
    commit      written right before the one irreversible call, the call that makes the post public
    posted, uploaded, drafted
                the platform has it (uploaded = private on YouTube, drafted = TikTok inbox). Done, never re-sent.
    failed      it definitely did not post: the error came before the irreversible call, or the platform refused it
    uncertain   the irreversible call got no clear answer (timeout, dropped connection, server error, no post id)
    reconciled  a person checked a held platform and recorded "posted" or "not_posted"

Folding the events gives each item and platform one state: new, failed, held or done.
- An attempt with nothing after it (the process died before the irreversible call) is failed: nothing that could
  post was sent, so it is safe to try again.
- A commit with nothing after it (the process died during the call) is held, and so is uncertain. Nothing retries a
  held platform; only `content-engine reconcile` moves it, to done or to failed.
- Done is final: later events for it are ignored.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..store import append_line, iso, parse_time, read_lines

DONE = ("posted", "uploaded", "drafted")
OUTCOMES = (*DONE, "failed", "uncertain")


@dataclass
class PlatformState:
    state: str = "new"  # new | failed | held | done
    status: str = ""  # posted | uploaded | drafted, when done
    attempts: int = 0
    first_day: str = ""
    last_day: str = ""
    first_ts: str = ""
    daily_day: str = ""  # day of the last attempt by the daily run (repair attempts don't count)
    error: str = ""
    url: str = ""
    post_id: str = ""
    note: str = ""
    pending: str = ""  # "attempt" or "commit" while an attempt has no outcome yet

    def settle(self) -> None:
        """Resolve an attempt that never got an outcome line (the process died)."""
        if self.pending == "attempt":
            self.state, self.error = "failed", "interrupted before the publish call; nothing was sent"
        elif self.pending == "commit":
            self.state, self.error = "held", "interrupted during the publish call; it may have posted"
        self.pending = ""


def fold(events: list[dict[str, Any]]) -> dict[tuple[str, str], PlatformState]:
    states: dict[tuple[str, str], PlatformState] = {}
    for e in events:
        key = (str(e.get("item")), str(e.get("platform")))
        st = states.setdefault(key, PlatformState())
        if st.state == "done":
            continue
        kind = e.get("event")
        if kind in ("attempt", "reconciled"):
            st.settle()
        if kind == "attempt":
            st.attempts += 1
            st.pending = "attempt"
            st.last_day = str(e.get("day", ""))
            st.first_day = st.first_day or st.last_day
            st.first_ts = st.first_ts or str(e.get("ts", ""))
            if e.get("by", "daily") == "daily":
                st.daily_day = st.last_day
        elif kind == "commit":
            if not st.attempts:
                st.attempts = 1
                st.first_day = st.last_day = str(e.get("day", ""))
                st.first_ts = str(e.get("ts", ""))
                st.daily_day = st.last_day
            st.pending = "commit"
        elif kind in DONE:
            st.state, st.status, st.pending = "done", kind, ""
            st.url, st.post_id, st.note, st.error = (str(e.get(k) or "") for k in ("url", "post_id", "note", "error"))
        elif kind == "failed":
            st.state, st.pending, st.error = "failed", "", str(e.get("error") or "")
        elif kind == "uncertain":
            st.state, st.pending, st.error = "held", "", str(e.get("error") or "")
        elif kind == "reconciled" and st.state == "held":
            if e.get("outcome") == "posted":
                st.state, st.status, st.url, st.note = "done", "posted", str(e.get("url") or ""), "reconciled"
            else:
                st.state, st.error = "failed", "checked by hand: not posted"
    for st in states.values():
        st.settle()
    return states


class Ledger:
    def __init__(self, path: Path):
        self.path = path

    def events(self) -> list[dict[str, Any]]:
        events = read_lines(self.path)
        for e in events:
            if (e.get("event") not in ("attempt", "commit", "reconciled", *OUTCOMES)
                    or any(not isinstance(e.get(k), str) or not e[k] for k in ("item", "platform", "day", "ts"))
                    or parse_time(e.get("ts")) is None
                    or (e["event"] == "reconciled" and e.get("outcome") not in ("posted", "not_posted"))):
                raise RuntimeError("corrupt ledger: invalid event fields")
            try:
                dt.date.fromisoformat(e["day"])
            except ValueError:
                raise RuntimeError("corrupt ledger: invalid event day") from None
        return events

    def states(self) -> dict[tuple[str, str], PlatformState]:
        return fold(self.events())

    def append(self, item: str, platform: str, event: str, now: dt.datetime, day: str, **fields: Any) -> None:
        self.events()
        append_line(self.path, {"ts": iso(now), "day": day, "item": item, "platform": platform, "event": event,
                                **{k: v for k, v in fields.items() if v not in (None, "")}})
