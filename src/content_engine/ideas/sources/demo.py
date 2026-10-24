"""Synthetic evidence for `content-engine demo` (see demo_data.py). No network."""

from __future__ import annotations

import datetime as dt

from ... import demo_data
from .base import Collected


class DemoSource:
    def __init__(self, name: str, group: str, every_hours: float = 6):
        self.name, self.group, self.every_hours = name, group, every_hours

    def collect(self, now: dt.datetime) -> Collected:
        items = demo_data.evidence(self.group, now)
        for it in items:
            it["source"] = self.name
        return Collected(items, "synthetic")
