"""The six platform connectors, in posting order, and the environment variables each one reads.

In dry-run mode connectors get placeholder credentials and the in-process simulator (publish/simulate.py), so real
secrets are never read and nothing leaves the machine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...http import Http
from .base import Connector, Post, PublishError, Result
from .facebook import Facebook
from .instagram import Instagram
from .threads import Threads
from .tiktok import TikTok
from .x import X
from .youtube import YouTube

__all__ = ["ENV", "ORDER", "Connector", "Post", "PublishError", "Result", "build", "missing_env"]

ORDER = ("facebook", "instagram", "threads", "x", "youtube", "tiktok")
ENV = {
    "facebook": ("FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_TOKEN"),
    "instagram": ("INSTAGRAM_USER_ID", "INSTAGRAM_ACCESS_TOKEN"),
    "threads": ("THREADS_USER_ID", "THREADS_ACCESS_TOKEN"),
    "x": ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"),
    "youtube": ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"),
    "tiktok": ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "TIKTOK_REFRESH_TOKEN"),
}


def missing_env(platform: str) -> list[str]:
    return [v for v in ENV[platform] if not os.environ.get(v)]


def build(platform: str, http: Http, cfg: Any, live: bool, sleep: Any = None) -> Connector:
    if live:
        missing = missing_env(platform)
        if missing:
            raise PublishError("set %s" % ", ".join(missing))
        v = [os.environ[k] for k in ENV[platform]]
    else:
        v = ["dry-run-%s" % k.lower() for k in ENV[platform]]
    extra: dict[str, Any] = {"sleep": sleep} if sleep else {}
    if platform == "facebook":
        return Facebook(http, v[0], v[1])
    if platform == "instagram":
        return Instagram(http, v[0], v[1], **extra)
    if platform == "threads":
        return Threads(http, v[0], v[1], **extra)
    if platform == "x":
        return X(http, v[0], v[1], v[2], v[3], **extra)
    if platform == "youtube":
        return YouTube(http, v[0], v[1], v[2], cfg["publish"].get("youtube"))
    if platform == "tiktok":
        token_file: Path | None = cfg.state_dir / "tiktok-token.json" if live else None
        return TikTok(http, v[0], v[1], v[2], token_file)
    raise PublishError("unknown platform %r" % platform)
