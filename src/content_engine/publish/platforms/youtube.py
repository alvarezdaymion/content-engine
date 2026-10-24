"""YouTube Short (Data API v3, resumable videos.insert).

Env:
  YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET   an OAuth client from Google Cloud
  YOUTUBE_REFRESH_TOKEN                      a refresh token for the channel, with the youtube.upload scope

Options ([publish.youtube]): privacy ("private" by default), category_id, synthetic_media (true: the question is
read by an AI voice, which YouTube asks creators to disclose).

Calls:
  POST oauth2.googleapis.com/token (refresh)                           -> access token
  POST /upload/youtube/v3/videos?uploadType=resumable&part=snippet,status   -> upload URL (Location header)
  PUT  <upload URL> with the file                                      the commit: the video now exists

Uploads from API projects that YouTube has not audited stay private, so a private upload counts as done
("uploaded"): publish it in YouTube Studio.
"""

from __future__ import annotations

from typing import Any

from ...http import Http
from .base import Commit, Post, Result, need

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"


class YouTube:
    name = "youtube"

    def __init__(self, http: Http, client_id: str, client_secret: str, refresh_token: str,
                 options: dict[str, Any] | None = None):
        self.http, self.client = http, (client_id, client_secret, refresh_token)
        self.opt = {"privacy": "private", "category_id": "22", "synthetic_media": True, **(options or {})}

    def _access_token(self) -> str:
        tok = self.http.json("POST", TOKEN_URL, form={
            "client_id": self.client[0], "client_secret": self.client[1], "refresh_token": self.client[2],
            "grant_type": "refresh_token"})
        need(tok.get("access_token"), "YouTube token refresh returned no access token")
        return str(tok["access_token"])

    def publish(self, post: Post, commit: Commit) -> Result:
        auth = {"Authorization": "Bearer " + self._access_token()}
        data = post.video_bytes()
        meta = {"snippet": {"title": post.title, "description": post.texts["full"],
                            "categoryId": str(self.opt["category_id"])},
                "status": {"privacyStatus": self.opt["privacy"], "selfDeclaredMadeForKids": False,
                           "containsSyntheticMedia": bool(self.opt["synthetic_media"])}}
        start = self.http.request("POST", UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                                  json_body=meta, headers={**auth, "X-Upload-Content-Length": str(len(data)),
                                                           "X-Upload-Content-Type": "video/mp4"})
        location = start.headers.get("location", "")
        need(location.startswith(UPLOAD), "YouTube gave no upload location")
        video = commit(lambda: self.http.json("PUT", location, data=data, timeout=900,
                                              headers={**auth, "Content-Type": "video/mp4"}))
        vid = str(video.get("id") or "")
        need(vid, "YouTube returned no video id")
        landed = str((video.get("status") or {}).get("privacyStatus") or self.opt["privacy"])
        url = "https://www.youtube.com/shorts/%s" % vid
        if landed == "public":
            return Result("posted", vid, url)
        return Result("uploaded", vid, url, "%s: publish it in YouTube Studio" % landed)
