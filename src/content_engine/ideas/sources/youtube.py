"""YouTube Data API v3: what's working for other creators (read-only).

Env:
  YOUTUBE_API_KEY   an API key from Google Cloud with the YouTube Data API v3 enabled

Options ([[sources]] with type = "youtube"):
  queries        search terms (required)
  days           only videos published in the last N days (default 7)
  per_query      results per search (default 15, max 50)
  duration       "any" (default), "short" (under 4 minutes), "medium" or "long"
  every_hours    how often it runs (default 6)

Calls: search.list per query (100 quota units each), then videos.list and channels.list (1 unit per 50 IDs). The
default daily quota is 10,000 units, so 5 queries every 6 hours uses about 2,000.

Each video becomes a `creator` item. Its multiple is views / the channel's normal, where the normal is the channel's
lifetime views / its video count. A video at 4x is doing four times what that channel usually does.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

from ...http import Http, Transport
from ...textutil import clip
from .base import Collected, multiple

API = "https://www.googleapis.com/youtube/v3"


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class YouTubeSource:
    def __init__(self, name: str, queries: list[str], every_hours: float = 6, days: int = 7, per_query: int = 15,
                 duration: str = "any", api_key: str | None = None, transport: Transport | None = None):
        self.name, self.queries, self.every_hours = name, list(queries), float(every_hours)
        self.days, self.per_query, self.duration = int(days), min(50, int(per_query)), duration
        self.key = api_key or os.environ.get("YOUTUBE_API_KEY", "")
        if not self.key:
            raise RuntimeError("set YOUTUBE_API_KEY")
        self.http = Http(transport)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.http.json("GET", API + path, params={**params, "key": self.key})

    def collect(self, now: dt.datetime) -> Collected:
        after = (now - dt.timedelta(days=self.days)).astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids: list[str] = []
        for q in self.queries:
            params = {"part": "snippet", "q": q, "type": "video", "order": "viewCount", "publishedAfter": after,
                      "maxResults": self.per_query}
            if self.duration != "any":
                params["videoDuration"] = self.duration
            for it in self._get("/search", params).get("items", []):
                vid = (it.get("id") or {}).get("videoId")
                if vid and vid not in ids:
                    ids.append(vid)
        videos = []
        for i in range(0, len(ids), 50):
            videos += self._get("/videos", {"part": "snippet,statistics", "id": ",".join(ids[i:i + 50])}).get(
                "items", [])
        channels = sorted({(v.get("snippet") or {}).get("channelId") for v in videos} - {None})
        normal: dict[str, float] = {}
        for i in range(0, len(channels), 50):
            for ch in self._get("/channels", {"part": "statistics", "id": ",".join(channels[i:i + 50])}).get(
                    "items", []):
                st = ch.get("statistics") or {}
                views, count = _int(st.get("viewCount")), _int(st.get("videoCount"))
                if views and count:
                    normal[ch["id"]] = views / count
        items = []
        for v in videos:
            sn, st = v.get("snippet") or {}, v.get("statistics") or {}
            views = _int(st.get("viewCount"))
            base = normal.get(sn.get("channelId", ""))
            items.append({"id": "youtube:%s" % v["id"], "source": self.name, "kind": "creator",
                          "text": clip(sn.get("title"), 300), "url": "https://www.youtube.com/watch?v=%s" % v["id"],
                          "author": clip(sn.get("channelTitle"), 80), "published": sn.get("publishedAt"),
                          "views": views, "baseline": round(base, 1) if base else None,
                          "multiple": multiple(views, base),
                          "engagement": (_int(st.get("likeCount")) or 0) + (_int(st.get("commentCount")) or 0)})
        return Collected(items, "%d queries, %d videos" % (len(self.queries), len(items)))
