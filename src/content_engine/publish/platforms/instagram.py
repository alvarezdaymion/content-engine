"""Instagram reel (Instagram API with Instagram Login, graph.instagram.com).

Env:
  INSTAGRAM_USER_ID        the professional account's user ID
  INSTAGRAM_ACCESS_TOKEN   a token with instagram_business_basic and instagram_business_content_publish

Instagram fetches the video from a public URL, so the rendered file must be reachable at
`[publish] video_base_url` + "<session>-<card>.mp4" (any static host or bucket works).

Calls:
  HEAD video_url                                          the file is reachable (checked before anything is created)
  POST /{user}/media media_type=REELS video_url caption   -> container id
  GET  /{container}?fields=status_code                    until FINISHED
  POST /{user}/media_publish creation_id                  the commit: the reel goes live
  GET  /{media}?fields=permalink                          best effort
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ...http import Http
from .base import Commit, Post, Result, best_effort, need, wait_for

GRAPH = "https://graph.instagram.com/v23.0"


class Instagram:
    name = "instagram"

    def __init__(self, http: Http, user_id: str, token: str, sleep: Callable[[float], None] = time.sleep):
        self.http, self.user, self.token, self.sleep = http, user_id, token, sleep

    def publish(self, post: Post, commit: Commit) -> Result:
        need(post.video_url, "set [publish] video_base_url: Instagram fetches the video from a public URL")
        self.http.request("HEAD", post.video_url)
        c = self.http.json("POST", "%s/%s/media" % (GRAPH, self.user), form={
            "media_type": "REELS", "video_url": post.video_url, "caption": post.texts["full"],
            "share_to_feed": "true", "access_token": self.token})
        cid = str(c.get("id") or "")
        need(cid, "Instagram returned no container id")
        wait_for(lambda: self.http.json("GET", "%s/%s" % (GRAPH, cid),
                                        params={"fields": "status_code", "access_token": self.token}),
                 lambda s: s.get("status_code") == "FINISHED",
                 lambda s: s.get("status_code") if s.get("status_code") in ("ERROR", "EXPIRED") else None,
                 "Instagram processing", self.sleep, every=10)
        pub = commit(lambda: self.http.json("POST", "%s/%s/media_publish" % (GRAPH, self.user),
                                            form={"creation_id": cid, "access_token": self.token}))
        media_id = str(pub.get("id") or "")
        need(media_id, "Instagram publish returned no media id")
        info = best_effort(lambda: self.http.json("GET", "%s/%s" % (GRAPH, media_id),
                                                  params={"fields": "permalink", "access_token": self.token}), {})
        link = str((info or {}).get("permalink") or "")
        return Result("posted", media_id, link, "" if link else "posted; permalink not read")
