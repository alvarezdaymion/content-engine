"""Google News RSS search: what changed in your field this week (free, no key, read-only).

Options ([[sources]] with type = "news"):
  queries      search terms (required)
  days         only stories from the last N days (default 3)
  per_query    stories kept per query (default 10)
  language     feed language and region, default "en-US"
  every_hours  how often it runs (default 6)

Each story becomes a `news` item (headline, link, publisher, date).
"""

from __future__ import annotations

import datetime as dt
import email.utils
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

from ...http import Http, Transport
from ...store import iso
from ...textutil import clip
from .base import Collected, item_id

FEED = "https://news.google.com/rss/search"


def _date(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        t = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def parse_feed(xml: bytes, source: str, since: dt.datetime, limit: int) -> list[dict[str, Any]]:
    out = []
    for it in ET.fromstring(xml).iter("item"):
        title, link = it.findtext("title") or "", it.findtext("link") or ""
        published = _date(it.findtext("pubDate"))
        if not title or not link.startswith(("https://", "http://")) or (published and published < since):
            continue
        publisher = it.find("source")
        out.append({"id": item_id("news", it.findtext("guid") or link), "source": source, "kind": "news",
                    "text": clip(title, 300), "url": link,
                    "author": clip(publisher.text, 80) if publisher is not None else None,
                    "published": iso(published)})
        if len(out) >= limit:
            break
    return out


class NewsSource:
    def __init__(self, name: str, queries: list[str], every_hours: float = 6, days: int = 3, per_query: int = 10,
                 language: str = "en-US", transport: Transport | None = None):
        self.name, self.queries, self.every_hours = name, list(queries), float(every_hours)
        self.days, self.per_query = int(days), int(per_query)
        lang, _, region = language.partition("-")
        self.params = {"hl": language, "gl": region or "US", "ceid": "%s:%s" % (region or "US", lang)}
        self.http = Http(transport)

    def collect(self, now: dt.datetime) -> Collected:
        since = now - dt.timedelta(days=self.days)
        items, failed = [], 0
        for q in self.queries:
            url = FEED + "?" + urllib.parse.urlencode({"q": "%s when:%dd" % (q, self.days), **self.params})
            try:
                items += parse_feed(self.http.request("GET", url).body, self.name, since, self.per_query)
            except Exception:  # one bad feed never loses the others
                failed += 1
        if self.queries and failed == len(self.queries):
            raise RuntimeError("every news query failed")
        return Collected(items, "%d queries%s" % (len(self.queries), ", %d failed" % failed if failed else ""))
