"""Load content-engine.toml and fill in defaults. Every threshold the engine uses lives here."""

from __future__ import annotations

import copy
import datetime as dt
import re
import tomllib
import zoneinfo
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLATFORMS = ("facebook", "instagram", "threads", "x", "youtube", "tiktok")
SOURCE_TYPES = ("youtube", "news", "csv", "demo")
MODES = ("off", "dry-run", "live")

DEFAULTS: dict[str, Any] = {
    "creator": {"name": "Your name", "timezone": "UTC"},
    "paths": {"sessions": "sessions", "state": "state", "out": "out", "ideas": "ideas.toml"},
    "ideas": {"cooldown_days": 21, "trending_hours": 72, "keep_days": 90, "top": 10},
    "sessions": {"cards": 8},
    "cards": {"max_hook_chars": 60, "max_question_words": 35, "max_context_chars": 320, "max_points": 6,
              "banned_words": []},
    "tts": {"enabled": False, "voice_id": "", "model": "eleven_multilingual_v2"},
    "transcribe": {"model": "small"},
    "matching": {"threshold": 0.6, "margin": 0.15, "window_words": 80},
    "edit": {"max_gap": 0.5, "lead_in": 0.15, "pad": 0.12, "cut_fillers": False, "min_answer_words": 8,
             "caption_words": 3, "caption_chars": 16, "disclosure": "Question read by an AI voice"},
    "captions": {"cta": "Send me your question and I may answer it next.",
                 "footer": "The question in this video is read by an AI voice.", "hashtags": []},
    "carousel": {"max_words": 30, "max_slides": 6, "closing": "Save this for later."},
    "publish": {
        "mode": "dry-run",
        "platforms": list(PLATFORMS),
        "post_after": "11:30",
        "video_base_url": "",
        "max_attempts": 3,
        "low_queue": 3,
        "lock_stale_minutes": 120,  # accepted for older configs; OS locks never expire
        "youtube": {"privacy": "private", "category_id": "22", "synthetic_media": True},
    },
    "sources": [],
}


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


@dataclass
class Config:
    data: dict[str, Any]
    root: Path

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def path(self, key: str) -> Path:
        return (self.root / self.data["paths"][key]).resolve()

    @property
    def state_dir(self) -> Path:
        return self.path("state")

    @property
    def sessions_dir(self) -> Path:
        return self.path("sessions")

    @property
    def out_dir(self) -> Path:
        return self.path("out")

    @property
    def tz(self) -> zoneinfo.ZoneInfo:
        return zoneinfo.ZoneInfo(self.data["creator"]["timezone"])

    def localize(self, t: dt.datetime) -> dt.datetime:
        return t.astimezone(self.tz)

    def today(self, now: dt.datetime) -> str:
        return self.localize(now).date().isoformat()


def validate(data: dict[str, Any]) -> None:
    try:
        zoneinfo.ZoneInfo(data["creator"]["timezone"])
    except (zoneinfo.ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError("creator.timezone %r is not a known time zone" % data["creator"]["timezone"]) from e
    pub = data["publish"]
    if pub["mode"] not in MODES:
        raise ValueError("publish.mode must be off, dry-run or live")
    bad = [p for p in pub["platforms"] if p not in PLATFORMS]
    if bad or len(set(pub["platforms"])) != len(pub["platforms"]):
        raise ValueError("publish.platforms must be distinct names from %s" % ", ".join(PLATFORMS))
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(pub["post_after"])):
        raise ValueError("publish.post_after must be HH:MM (24-hour)")
    if int(pub["max_attempts"]) < 1:
        raise ValueError("publish.max_attempts must be at least 1")
    if pub["youtube"]["privacy"] not in ("private", "unlisted", "public"):
        raise ValueError("publish.youtube.privacy must be private, unlisted or public")
    m = data["matching"]
    if not 0 < float(m["threshold"]) <= 1 or not 0 <= float(m["margin"]) < 1:
        raise ValueError("matching.threshold must be in (0, 1] and matching.margin in [0, 1)")
    if int(data["sessions"]["cards"]) < 1:
        raise ValueError("sessions.cards must be at least 1")
    if float(data["edit"]["max_gap"]) <= 0 or int(data["edit"]["caption_words"]) < 1:
        raise ValueError("edit.max_gap must be above 0 and edit.caption_words at least 1")
    names = [s.get("name") for s in data["sources"]]
    if any(not n for n in names) or len(names) != len(set(names)):
        raise ValueError("every [[sources]] entry needs a unique name")
    for s in data["sources"]:
        if s.get("type") not in SOURCE_TYPES:
            raise ValueError("source %r: type must be one of %s" % (s["name"], ", ".join(SOURCE_TYPES)))
        if s["type"] in ("youtube", "news") and not s.get("queries"):
            raise ValueError("source %r needs queries = [...]" % s["name"])
        if s["type"] == "csv" and not s.get("path"):
            raise ValueError("source %r needs path = \"...\"" % s["name"])


def from_dict(data: dict[str, Any], root: Path | str = ".") -> Config:
    merged = _merge(DEFAULTS, data)
    validate(merged)
    return Config(merged, Path(root).resolve())


def load(path: Path | str) -> Config:
    p = Path(path)
    with p.open("rb") as f:
        data = tomllib.load(f)
    return from_dict(data, p.parent)
