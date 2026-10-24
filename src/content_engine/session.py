"""The recording session is the unit of work.

Content tools tend to plan around an ideal process: calendars, briefs, scripts and approval chains. When the
creator doesn't work that way, the plan goes stale and the tool goes unused. The habit that reliably happens is
sitting down once a week and answering questions on camera, so every stage hangs off that session:

    plan     the top ranked ideas become the session's question cards
    record   takes are matched to that session's cards
    edit     trims, captions, graphics and post texts per matched card
    review   approve by card number
    publish  the queue is the approved items of sessions, oldest session first

Rules, enforced here and tested in tests/test_session.py:

1. Cards exist only inside a session. There is no card backlog and no content calendar.
2. At most one session is unrecorded at a time. Re-plan it (`replace=True`); you can't stack another on top.
3. A session holds its ideas, so they leave the ranking for the cooldown. Once takes are matched, the ideas of cards
   that were not recorded go back to the ranking.
4. Takes are matched only against their own session's cards.
5. The publish queue holds only approved items whose cut, graphics and post texts are unchanged since approval.

A session is a folder: sessions/<id>/session.json (text only), takes/ (videos and transcripts), audio/ (questions).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import cards as cards_mod
from . import ideas
from .store import Lock, iso, read_json, write_json


class SessionRuleError(RuntimeError):
    pass


@dataclass
class Session:
    id: str
    record_on: str
    planned_at: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    takes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edits: dict[str, dict[str, Any]] = field(default_factory=dict)
    captions: dict[str, dict[str, str]] = field(default_factory=dict)
    review: dict[str, dict[str, Any]] = field(default_factory=dict)
    edit_notes: dict[str, dict[str, Any]] = field(default_factory=dict)
    dir: Path = Path(".")

    @classmethod
    def load(cls, folder: Path) -> Session:
        data = read_json(folder / "session.json")
        if not isinstance(data, dict):
            raise SessionRuleError("%s has no readable session.json" % folder)
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data and k != "dir"}
        return cls(**known, dir=folder)

    def save(self) -> None:
        with self.lock():
            self._save()

    def lock(self) -> Lock:
        return Lock(self.dir / "session.lock")

    def _save(self) -> None:
        """Write session state while the caller holds the session lock."""
        data = asdict(self)
        data.pop("dir")
        write_json(self.dir / "session.json", data)

    @property
    def takes_dir(self) -> Path:
        return self.dir / "takes"

    @property
    def audio_dir(self) -> Path:
        return self.dir / "audio"

    def card(self, n: int | str) -> dict[str, Any]:
        for c in self.cards:
            if c["n"] == int(n):
                return c
        raise KeyError("session %s has no card %s" % (self.id, n))

    def key(self, n: int | str) -> str:
        return "%s#%s" % (self.id, n)

    @property
    def recorded_cards(self) -> list[int]:
        return sorted({t["card"] for t in self.takes.values() if t.get("status") == "matched"})

    @property
    def recorded(self) -> bool:
        return bool(self.recorded_cards)


def load_all(cfg: Any) -> list[Session]:
    root = cfg.sessions_dir
    if not root.exists():
        return []
    found = [Session.load(p.parent) for p in sorted(root.glob("*/session.json"))]
    return sorted(found, key=lambda s: (s.record_on, s.id))


def get(sessions: list[Session], sid: str | None = None) -> Session:
    if not sessions:
        raise SessionRuleError("no sessions yet: run `content-engine plan` first")
    if sid is None:
        return sessions[-1]
    for s in sessions:
        if s.id == sid:
            return s
    raise SessionRuleError("no session %r (have: %s)" % (sid, ", ".join(s.id for s in sessions)))


def open_session(sessions: list[Session]) -> Session | None:
    """The session that is planned but not recorded yet (rule 2 allows at most one)."""
    return next((s for s in sessions if not s.recorded), None)


def idea_usage(sessions: list[Session]) -> dict[str, str]:
    """Idea id -> when a session took it (rule 3)."""
    usage: dict[str, str] = {}
    for s in sessions:
        held = {c["n"] for c in s.cards} if not s.recorded else set(s.recorded_cards)
        for c in s.cards:
            if c["n"] in held and c.get("idea"):
                usage[c["idea"]] = max(usage.get(c["idea"], ""), s.planned_at)
    return usage


def plan(cfg: Any, sessions: list[Session], now: dt.datetime, record_on: str | None = None,
         n_cards: int | None = None, replace: bool = False, sid: str | None = None) -> Session:
    """Open the next recording session: the top ranked ideas become its cards (rules 1-3)."""
    current = open_session(sessions)
    if current and not replace:
        raise SessionRuleError("session %s is planned but not recorded yet. Record it (then `content-engine match`), "
                               "or re-plan it with --replace." % current.id)
    if replace and current is None:
        raise SessionRuleError("there is no unrecorded session to replace")
    others = [s for s in sessions if s is not current]
    result = ideas.ranked(cfg, now, idea_usage(others))
    picked = result["ideas"][: int(n_cards or cfg["sessions"]["cards"])]
    if not picked:
        raise SessionRuleError("no ranked ideas yet: add ideas to ideas.toml and run `content-engine ideas`")
    topics = {t.id: t for t in result["topics"]}
    new_cards = [cards_mod.write_card(n, topics[entry["id"]], entry) for n, entry in enumerate(picked, 1)]
    if current is not None:  # card numbers now mean different questions, so per-card state starts over
        current.cards, current.planned_at = new_cards, iso(now) or ""
        current.record_on = record_on or current.record_on
        current.edits, current.captions, current.review, current.edit_notes = {}, {}, {}, {}
        current.save()
        return current
    day = record_on or cfg.today(now)
    base = sid or day
    taken = {s.id for s in sessions}
    session_id = base
    k = 2
    while session_id in taken or (cfg.sessions_dir / session_id).exists():
        session_id, k = "%s-%d" % (base, k), k + 1
    s = Session(session_id, day, iso(now) or "", new_cards, dir=cfg.sessions_dir / session_id)
    s.save()
    return s


def file_hash(path: Path) -> str | None:
    try:
        with path.open("rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest()
    except OSError:
        return None


def _fingerprint(spec: Any) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def presentation_fingerprint(session: Session, n: int | str) -> str | None:
    """The current render inputs, including all subtitle text, styling and timing."""
    edit = session.edits.get(str(n)) or {}
    artifacts = edit.get("artifacts") or {}
    if not artifacts.get("subtitles") or not artifacts.get("render"):
        return None
    subtitles = file_hash(session.dir / artifacts["subtitles"])
    source = file_hash(session.takes_dir / edit["video"]) if edit.get("video") else None
    if subtitles is None or (edit.get("video") and source is None):
        return None
    return _fingerprint({"take": edit.get("take"), "video": edit.get("video"), "source": source,
                         "segments": edit.get("segments"), "graphics": edit.get("graphics"),
                         "subtitles": subtitles})


def render_current(session: Session, n: int | str) -> bool:
    edit = session.edits.get(str(n)) or {}
    proof = edit.get("render_proof") or {}
    current = presentation_fingerprint(session, n)
    if current is None or proof.get("fingerprint") != current or not proof.get("sha256"):
        return False
    return file_hash(session.dir / edit["artifacts"]["render"]) == proof["sha256"]


def hosted_url(base: str, sid: str, n: int | str, digest: str) -> str:
    return "%s/%s-%s-%s.mp4" % (base.rstrip("/"), sid, n, digest) if base and digest else ""


def fingerprint(session: Session, n: int | str) -> str | None:
    """What an approval covers: current presentation, source and rendered bytes, and post texts."""
    presentation = presentation_fingerprint(session, n)
    texts = session.captions.get(str(n))
    if presentation is None or not texts:
        return None
    edit = session.edits[str(n)]
    return _fingerprint({"presentation": presentation, "texts": texts,
                         "video_url": edit.get("video_url", ""),
                         "render": file_hash(session.dir / edit["artifacts"]["render"])})


def review_status(session: Session, n: int | str) -> str:
    """pending | approved | changed (approved, then edited) | rejected | not edited."""
    if str(n) not in session.edits:
        return "not edited"
    r = session.review.get(str(n)) or {}
    if r.get("status") == "approved":
        current = fingerprint(session, n)
        return "approved" if current is not None and r.get("fingerprint") == current else "changed"
    return r.get("status") or "pending"


def queue(sessions: list[Session]) -> tuple[list[tuple[Session, int]], list[str]]:
    """(approved items in publish order, item keys that changed after approval). Rule 5."""
    items, changed = [], []
    for s in sessions:
        for n in sorted(int(k) for k in s.review):
            status = review_status(s, n)
            if status == "approved":
                items.append((s, n))
            elif status == "changed":
                changed.append(s.key(n))
    return items, changed


def stage(session: Session, finished: set[str] | None = None) -> str:
    """planned -> recorded -> in review -> publishing -> done. `finished` = item keys done on every platform."""
    if not session.recorded:
        return "planned"
    if not session.edits:
        return "recorded"
    statuses = {n: review_status(session, n) for n in session.edits}
    if any(st in ("pending", "changed") for st in statuses.values()):
        return "in review"
    approved = [n for n, st in statuses.items() if st == "approved"]
    if any(session.key(n) not in (finished or set()) for n in approved):
        return "publishing"
    return "done"
