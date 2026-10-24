"""Facebook Page reel (Graph API, video_reels).

Env:
  FACEBOOK_PAGE_ID      the Page's numeric ID
  FACEBOOK_PAGE_TOKEN   a Page access token with pages_manage_posts and pages_read_engagement

Calls:
  POST /{page}/video_reels upload_phase=start                         -> video_id, upload_url
  POST rupload.facebook.com/video-upload/<version>/{video_id}         the file bytes
  POST /{page}/video_reels upload_phase=finish video_state=PUBLISHED  the commit: the reel goes live
"""

from __future__ import annotations

from ...http import Http
from .base import Commit, Post, Result, need

GRAPH = "https://graph.facebook.com/v23.0"
RUPLOAD = "https://rupload.facebook.com/video-upload/"


class Facebook:
    name = "facebook"

    def __init__(self, http: Http, page_id: str, token: str):
        self.http, self.page, self.token = http, page_id, token

    def publish(self, post: Post, commit: Commit) -> Result:
        url = "%s/%s/video_reels" % (GRAPH, self.page)
        start = self.http.json("POST", url, form={"upload_phase": "start", "access_token": self.token})
        video_id = str(start.get("video_id") or "")
        need(video_id, "Facebook start phase returned no video_id")
        upload_url = str(start.get("upload_url") or RUPLOAD + "v23.0/" + video_id)
        need(upload_url.startswith(RUPLOAD), "unexpected Facebook upload host")
        data = post.video_bytes()
        up = self.http.json("POST", upload_url, data=data, timeout=600,
                            headers={"Authorization": "OAuth " + self.token, "offset": "0",
                                     "file_size": str(len(data))})
        need(up.get("success"), "Facebook did not accept the upload")
        fin = commit(lambda: self.http.json("POST", url, form={
            "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
            "description": post.texts["full"], "access_token": self.token}))
        need(fin.get("success"), "Facebook answered the publish call without success")
        return Result("posted", video_id, "https://www.facebook.com/reel/%s" % video_id)
