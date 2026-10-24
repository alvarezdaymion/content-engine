"""TikTok inbox upload (Content Posting API): the video lands as a draft that you finish in the TikTok app, where the
caption is pasted. Direct posting needs an audited app, so drafts are the default.

Env:
  TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET   the app's credentials
  TIKTOK_REFRESH_TOKEN                      a refresh token for the account, with the video.upload scope

TikTok can rotate the refresh token on refresh. In live mode the new one is kept in state/tiktok-token.json
(mode 0600) and used until TIKTOK_REFRESH_TOKEN itself changes.

Calls:
  POST /v2/oauth/token/ (refresh)                                       -> access token
  POST /v2/post/publish/inbox/video/init/ {source_info: FILE_UPLOAD}    -> publish_id, upload_url
  PUT  upload_url per chunk (Content-Range); the last chunk is the commit: TikTok then creates the draft
  POST /v2/post/publish/status/fetch/ {publish_id}                      best effort
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

from ...http import Http
from .base import Commit, Post, PublishError, Result, best_effort, need

API = "https://open.tiktokapis.com"
CHUNK = 10_000_000
SINGLE_MAX = 64_000_000


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, chunk_count): one chunk up to 64 MB, else 10 MB chunks with the remainder in the last one."""
    if size <= SINGLE_MAX:
        return size, 1
    return CHUNK, size // CHUNK


def _ok(body: dict[str, Any], what: str) -> dict[str, Any]:
    err = body.get("error") or {}
    if isinstance(err, dict) and err.get("code") not in (None, "ok"):
        raise PublishError("%s: %s %s" % (what, err.get("code"), err.get("message", "")))
    return body.get("data") or {}


class TikTok:
    name = "tiktok"

    def __init__(self, http: Http, client_key: str, client_secret: str, refresh_token: str,
                 token_file: Path | None = None):
        self.http, self.client = http, (client_key, client_secret)
        self.env_token, self.token_file = refresh_token, token_file

    def _refresh_token(self) -> str:
        if self.token_file and self.token_file.exists():
            saved = json.loads(self.token_file.read_text())
            if saved.get("from") == self._tag(self.env_token):
                return str(saved["refresh_token"])
        return self.env_token

    def _tag(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()[:16]

    def _access_token(self) -> str:
        current = self._refresh_token()
        tok = self.http.json("POST", API + "/v2/oauth/token/", form={
            "client_key": self.client[0], "client_secret": self.client[1], "grant_type": "refresh_token",
            "refresh_token": current})
        need(tok.get("access_token"), "TikTok token refresh failed: %s" % tok.get("error", "no access token"))
        new = str(tok.get("refresh_token") or "")
        if self.token_file and new and new != current:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"from": self._tag(self.env_token), "refresh_token": new}, f)
        return str(tok["access_token"])

    def publish(self, post: Post, commit: Commit) -> Result:
        auth = {"Authorization": "Bearer " + self._access_token()}
        data = post.video_bytes()
        size = len(data)
        chunk, count = chunk_plan(size)
        init = _ok(self.http.json("POST", API + "/v2/post/publish/inbox/video/init/", headers=auth, json_body={
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk,
                            "total_chunk_count": count}}), "TikTok init")
        publish_id, upload_url = str(init.get("publish_id") or ""), str(init.get("upload_url") or "")
        host = urllib.parse.urlsplit(upload_url).hostname or ""
        need(publish_id and upload_url.startswith("https://") and host.endswith(".tiktokapis.com"),
             "TikTok returned no usable upload URL")
        for i in range(count):
            start = i * chunk
            end = size if i == count - 1 else start + chunk

            def put(start: int = start, end: int = end) -> Any:
                return self.http.request("PUT", upload_url, data=data[start:end], timeout=600, headers={
                    "Content-Type": "video/mp4", "Content-Range": "bytes %d-%d/%d" % (start, end - 1, size)})

            commit(put) if i == count - 1 else put()
        status = best_effort(lambda: _ok(self.http.json("POST", API + "/v2/post/publish/status/fetch/",
                                                        headers=auth, json_body={"publish_id": publish_id}),
                                         "TikTok status"), {})
        if (status or {}).get("status") == "FAILED":  # after the commit, so the runner holds it for a person
            raise PublishError("TikTok reported %s after the upload" % status.get("fail_reason", "a failure"))
        return Result("drafted", publish_id, "", "draft in the TikTok inbox: finish it in the app")
