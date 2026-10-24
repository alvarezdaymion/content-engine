"""An in-process stand-in for the six platforms' APIs, used by dry runs and the demo.

Every connector runs its real request sequence against it, so a dry run exercises the same code, texts, file
handling and ledger as a live run, and nothing leaves the machine. `fail` injects one outcome per platform:

    "commit-timeout"       the publish call gets no answer       -> uncertain, held
    "commit-5xx"           the publish call returns HTTP 503     -> uncertain, held
    "commit-reject"        the publish call returns HTTP 400     -> failed (the platform refused it)
    "prepare-error-once"   the first preparation call returns 503, later ones work -> failed, then repairable
"""

from __future__ import annotations

import itertools
import json
import urllib.parse
from typing import Any

from ..http import TransportError, strip_query


def _ok(data: Any, status: int = 200, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    return status, headers or {}, json.dumps(data).encode()


class Simulator:
    def __init__(self, fail: dict[str, str] | None = None):
        self.fail = dict(fail or {})
        self.calls: list[tuple[str, str]] = []
        self._ids = itertools.count(1)
        self._failed_once: set[str] = set()

    def _id(self, prefix: str) -> str:
        return "sim-%s-%d" % (prefix, next(self._ids))

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None,
                 timeout: float) -> tuple[int, dict[str, str], bytes]:
        self.calls.append((method, strip_query(url)))
        parts = urllib.parse.urlsplit(url)
        platform, step, resp = self._route(method, parts.hostname or "", parts.path, parts.query, headers, body)
        mode = self.fail.get(platform, "")
        if step == "commit" and mode == "commit-timeout":
            raise TransportError("%s %s: no response (simulated timeout)" % (method, strip_query(url)))
        if step == "commit" and mode == "commit-5xx":
            return _ok({"error": {"message": "simulated server error"}}, 503)
        if step == "commit" and mode == "commit-reject":
            return _ok({"error": {"message": "simulated rejection"}}, 400)
        if step == "prepare" and mode == "prepare-error-once" and platform not in self._failed_once:
            self._failed_once.add(platform)
            return _ok({"error": {"message": "simulated server error"}}, 503)
        return resp

    def _route(self, method: str, host: str, path: str, query: str, headers: dict[str, str],
               body: bytes | None) -> tuple[str, str, tuple[int, dict[str, str], bytes]]:
        form = {k: v[0] for k, v in urllib.parse.parse_qs((body or b"").decode(errors="ignore")).items()} \
            if headers.get("Content-Type", "").startswith("application/x-www-form-urlencoded") else {}
        q = dict(urllib.parse.parse_qsl(query))
        last = path.rstrip("/").rsplit("/", 1)[-1]
        if host == "graph.facebook.com" and last == "video_reels":
            if form.get("upload_phase") == "start":
                vid = self._id("fb")
                return "facebook", "prepare", _ok({"video_id": vid,
                                                   "upload_url": "https://rupload.facebook.com/video-upload/v23.0/"
                                                                 + vid})
            return "facebook", "commit", _ok({"success": True})
        if host == "rupload.facebook.com":
            return "facebook", "upload", _ok({"success": True})
        if method == "HEAD":
            return "media", "check", _ok({})
        if host in ("graph.instagram.com", "graph.threads.net"):
            name = "instagram" if host == "graph.instagram.com" else "threads"
            if last in ("media", "threads"):
                return name, "prepare", _ok({"id": self._id(name[:2] + "-container")})
            if last in ("media_publish", "threads_publish"):
                return name, "commit", _ok({"id": self._id(name[:2])})
            if q.get("fields") == "permalink":
                link = ("https://www.instagram.com/reel/%s/" if name == "instagram"
                        else "https://www.threads.com/t/%s") % last
                return name, "read", _ok({"permalink": link})
            return name, "status", _ok({"status_code": "FINISHED", "status": "FINISHED"})
        if host == "api.x.com":
            if last == "initialize":
                return "x", "prepare", _ok({"data": {"id": self._id("x-media")}})
            if last in ("append", "finalize"):
                return "x", "upload", _ok({"data": {"processing_info": {"state": "succeeded"}}})
            if last == "upload":
                return "x", "status", _ok({"data": {"processing_info": {"state": "succeeded"}}})
            if last == "tweets":
                return "x", "commit", _ok({"data": {"id": self._id("x")}})
        if host == "oauth2.googleapis.com":
            return "youtube", "token", _ok({"access_token": "sim-access", "expires_in": 3599})
        if host == "www.googleapis.com" and path.startswith("/upload/youtube/v3/videos"):
            if method == "POST":
                loc = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=%s" % self._id("upload")
                return "youtube", "prepare", _ok({}, 200, {"Location": loc})
            return "youtube", "commit", _ok({"id": self._id("yt"), "status": {"privacyStatus": "private"}})
        if host == "open.tiktokapis.com":
            if last == "token":
                return "tiktok", "token", _ok({"access_token": "sim-access", "refresh_token": form.get(
                    "refresh_token", ""), "scope": "video.upload"})
            if last == "init":
                return "tiktok", "prepare", _ok({"data": {"publish_id": self._id("tt"), "upload_url":
                                                          "https://open-upload.tiktokapis.com/video/?upload_id=sim"},
                                                 "error": {"code": "ok"}})
            if last == "fetch":
                return "tiktok", "read", _ok({"data": {"status": "SEND_TO_USER_INBOX"}, "error": {"code": "ok"}})
        if host == "open-upload.tiktokapis.com":
            rng = headers.get("Content-Range", "")
            done = rng.rsplit("/", 1)[-1] == str(int(rng.split("-")[1].split("/")[0]) + 1) if "-" in rng else True
            return "tiktok", "commit" if done else "upload", _ok({}, 201)
        return "unknown", "unknown", _ok({"error": {"message": "the simulator has no route for %s %s%s"
                                                    % (method, host, path)}}, 404)
