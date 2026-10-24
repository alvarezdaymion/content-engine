"""Threads video post (graph.threads.net), with the short text (500 characters).

Env:
  THREADS_USER_ID        the Threads user ID
  THREADS_ACCESS_TOKEN   a token with threads_basic and threads_content_publish

Like Instagram, Threads fetches the video from `[publish] video_base_url` + "<session>-<card>.mp4".

Calls:
  HEAD video_url
  POST /{user}/threads media_type=VIDEO video_url text   -> container id
  GET  /{container}?fields=status,error_message          until FINISHED
  POST /{user}/threads_publish creation_id               the commit: the post goes live
  GET  /{post}?fields=permalink                          best effort
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ...http import Http
from .base import Commit, Post, Result, best_effort, need, wait_for

GRAPH = "https://graph.threads.net/v1.0"


class Threads:
    name = "threads"

    def __init__(self, http: Http, user_id: str, token: str, sleep: Callable[[float], None] = time.sleep):
        self.http, self.user, self.token, self.sleep = http, user_id, token, sleep

    def publish(self, post: Post, commit: Commit) -> Result:
        need(post.video_url, "set [publish] video_base_url: Threads fetches the video from a public URL")
        self.http.request("HEAD", post.video_url)
        c = self.http.json("POST", "%s/%s/threads" % (GRAPH, self.user), form={
            "media_type": "VIDEO", "video_url": post.video_url, "text": post.texts["short"],
            "access_token": self.token})
        cid = str(c.get("id") or "")
        need(cid, "Threads returned no container id")
        wait_for(lambda: self.http.json("GET", "%s/%s" % (GRAPH, cid),
                                        params={"fields": "status,error_message", "access_token": self.token}),
                 lambda s: s.get("status") == "FINISHED",
                 lambda s: str(s.get("error_message") or s.get("status")) if s.get("status") in ("ERROR", "EXPIRED")
                 else None, "Threads processing", self.sleep, every=10)
        pub = commit(lambda: self.http.json("POST", "%s/%s/threads_publish" % (GRAPH, self.user),
                                            form={"creation_id": cid, "access_token": self.token}))
        post_id = str(pub.get("id") or "")
        need(post_id, "Threads publish returned no post id")
        info = best_effort(lambda: self.http.json("GET", "%s/%s" % (GRAPH, post_id),
                                                  params={"fields": "permalink", "access_token": self.token}), {})
        link = str((info or {}).get("permalink") or "")
        return Result("posted", post_id, link, "" if link else "posted; permalink not read")
