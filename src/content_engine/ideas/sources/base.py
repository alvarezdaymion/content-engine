"""What every idea source implements.

A source has a `name`, an `every_hours` interval and `collect(now) -> Collected`. It only reads. Items follow the
evidence schema in ideas/evidence.py, and every text field goes through textutil.scrub.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import statistics
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Collected:
    items: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""


@runtime_checkable
class Source(Protocol):
    name: str
    every_hours: float

    def collect(self, now: dt.datetime) -> Collected: ...


def item_id(source: str, *parts: Any) -> str:
    return "%s:%s" % (source, hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16])


def multiple(value: float | None, baseline: float | None) -> float | None:
    if value is None or not baseline or baseline <= 0:
        return None
    return round(float(value) / float(baseline), 2)


def median(values: list[float | None], min_n: int = 3) -> float | None:
    vals = [float(v) for v in values if v is not None and v >= 0]
    return float(statistics.median(vals)) if len(vals) >= min_n else None
