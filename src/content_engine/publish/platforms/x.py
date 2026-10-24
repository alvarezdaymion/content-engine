"""X post with the video (API v2, OAuth 1.0a user context), with the X text (280 characters).

Env:
  X_API_KEY, X_API_SECRET                 the app's consumer key and secret
  X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET   the posting account's Read and Write access token

Calls:
  POST /2/media/upload/initialize {media_type, total_bytes, media_category}   -> media id
  POST /2/media/upload/{id}/append   multipart, 4 MB segments
  POST /2/media/upload/{id}/finalize
  GET  /2/media/upload?command=STATUS&media_id={id}                            until succeeded
  POST /2/tweets {text, media: {media_ids}}                                    the commit: the post goes live

X API access is paid per use or by plan; check your plan's posting limits.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import Any

from ...http import Http
from . import oauth1
from .base import Commit, Post, Result, need, wait_for

API = "https://api.x.com"
SEGMENT = 4 * 1024 * 1024


def multipart(fields: dict[str, str], name: str, blob: bytes) -> tuple[bytes, str]:
    boundary = "----content-engine-" + secrets.token_hex(12)
    out = [('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)).encode()
           for k, v in fields.items()]
    out.append(('--%s\r\nContent-Disposition: form-data; name="%s"; filename="blob"\r\nContent-Type: '
                'application/octet-stream\r\n\r\n' % (boundary, name)).encode() + blob + b"\r\n")
    out.append(("--%s--\r\n" % boundary).encode())
    return b"".join(out), "multipart/form-data; boundary=" + boundary


def _info(body: Any) -> dict[str, Any]:
    return ((body or {}).get("data") or {}).get("processing_info") or {}


class X:
    name = "x"

    def __init__(self, http: Http, api_key: str, api_secret: str, access_token: str, access_secret: str,
                 sleep: Callable[[float], None] = time.sleep):
        self.http, self.keys, self.sleep = http, (api_key, api_secret, access_token, access_secret), sleep

    def _call(self, method: str, path: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        url = API + path
        auth = oauth1.header(method, url, params or {}, *self.keys)
        return self.http.json(method, url, params=params, headers={"Authorization": auth, **kw.pop("headers", {})},
                              **kw)

    def publish(self, post: Post, commit: Commit) -> Result:
        data = post.video_bytes()
        init = self._call("POST", "/2/media/upload/initialize", json_body={
            "media_type": "video/mp4", "total_bytes": len(data), "media_category": "tweet_video"})
        mid = str((init.get("data") or {}).get("id") or "")
        need(mid, "X returned no media id")
        for i in range(0, len(data), SEGMENT):
            body, ctype = multipart({"segment_index": str(i // SEGMENT)}, "media", data[i:i + SEGMENT])
            self._call("POST", "/2/media/upload/%s/append" % mid, data=body, headers={"Content-Type": ctype},
                       timeout=300)
        fin = self._call("POST", "/2/media/upload/%s/finalize" % mid)
        if _info(fin).get("state") not in (None, "succeeded"):
            wait_for(lambda: self._call("GET", "/2/media/upload", {"command": "STATUS", "media_id": mid}),
                     lambda b: _info(b).get("state") == "succeeded",
                     lambda b: str((_info(b).get("error") or {}).get("message") or "failed")
                     if _info(b).get("state") == "failed" else None,
                     "X video processing", self.sleep, every=float(_info(fin).get("check_after_secs") or 5))
        tweet = commit(lambda: self._call("POST", "/2/tweets", json_body={
            "text": post.texts["x"], "media": {"media_ids": [mid]}}))
        post_id = str((tweet.get("data") or {}).get("id") or "")
        need(post_id, "X returned no post id")
        return Result("posted", post_id, "https://x.com/i/status/%s" % post_id)
