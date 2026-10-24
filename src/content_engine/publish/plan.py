"""What to post today and what repair may retry. Pure: no network, no clock, no files.

Today (one item per day):
1. An item the daily run already worked on today: only its platforms that were never attempted (a crash, or a
   platform switched on since), and only while it is still approved. Nothing else today.
2. Otherwise, the last started item if it is still approved and has platforms that were never attempted.
3. Otherwise, the next approved item in queue order that has never been started.
4. Nothing approved: "waiting" if edited items wait for review, "empty" if it's time to record the next session.

The daily run never retries a failure. Repair does, under its own rules:
- only platforms whose state is failed (definitely not posted), never held, never done;
- at most `max_attempts` attempts per item and platform, the first try included;
- the scheduled repair only retries failures from today, so an old item never lands on a platform the same day as a
  new one. `content-engine repair --item` retries an older failure on purpose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ledger import PlatformState


@dataclass
class Today:
    kind: str  # new | resume | done | waiting | empty | off
    item: str | None = None
    platforms: list[str] = field(default_factory=list)
    left: int = 0  # approved items never started, not counting this one


def _state(states: dict[tuple[str, str], PlatformState], item: str, platform: str) -> str:
    st = states.get((item, platform))
    return st.state if st else "new"


def started_items(states: dict[tuple[str, str], PlatformState]) -> dict[str, tuple[str, str]]:
    """item -> (first day, first timestamp) over all its platforms."""
    out: dict[str, tuple[str, str]] = {}
    for (item, _), st in states.items():
        if st.attempts:
            cur = out.get(item)
            out[item] = min(cur, (st.first_day, st.first_ts)) if cur else (st.first_day, st.first_ts)
    return out


def plan_today(queue: list[str], enabled: list[str], states: dict[tuple[str, str], PlatformState], today: str,
               waiting: int = 0) -> Today:
    if not enabled:
        return Today("off")
    started = started_items(states)
    unstarted = [i for i in queue if i not in started]
    touched = sorted({it for (it, _), st in states.items() if st.daily_day == today}, key=lambda i: started[i])
    if touched:
        item = touched[0]
        fresh = [p for p in enabled if _state(states, item, p) == "new"]
        if fresh and item in queue:
            return Today("resume", item, fresh, len(unstarted))
        return Today("done", item, [], len(unstarted))
    if started:
        last = max(started, key=lambda i: started[i][1])
        fresh = [p for p in enabled if _state(states, last, p) == "new"]
        if fresh and last in queue:
            return Today("resume", last, fresh, len(unstarted))
    if unstarted:
        return Today("new", unstarted[0], list(enabled), len(unstarted) - 1)
    return Today("waiting" if waiting else "empty")


def repair_candidates(states: dict[tuple[str, str], PlatformState], enabled: list[str], today: str,
                      max_attempts: int, item: str | None = None,
                      platform: str | None = None) -> dict[str, list[tuple[str, str]]]:
    """{"retry": [...], "held": [...], "exhausted": [...]} as (item, platform) pairs."""
    out: dict[str, list[tuple[str, str]]] = {"retry": [], "held": [], "exhausted": []}
    for (it, p), st in sorted(states.items()):
        if p not in enabled or (item and it != item) or (platform and p != platform):
            continue
        if st.state == "held":
            out["held"].append((it, p))
        elif st.state == "failed" and (item is not None or st.last_day == today):
            out["exhausted" if st.attempts >= max_attempts else "retry"].append((it, p))
    return out
