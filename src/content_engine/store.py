"""Files: atomic JSON writes, fsynced appends and a lock file. Standard library only."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
from typing import Any


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str) + "\n")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def append_line(path: Path, obj: dict[str, Any]) -> None:
    """Append one JSON line and fsync it before returning, so it survives a crash right after."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        raw = path.read_bytes()
        _, end = _lines(raw)
        if end != len(raw):
            os.ftruncate(fd, end)
        prefix = b"\n" if end and raw[end - 1:end] != b"\n" else b""
        data = prefix + (json.dumps(obj, sort_keys=True, ensure_ascii=False) + "\n").encode()
        while data:
            written = os.write(fd, data)
            if written <= 0:
                raise OSError("could not complete ledger write")
            data = data[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _lines(raw: bytes) -> tuple[list[dict[str, Any]], int]:
    out, end = [], 0
    for line in raw.splitlines(keepends=True):
        try:
            obj = json.loads(line)
        except (ValueError, UnicodeError):
            if not line.endswith(b"\n") and end + len(line) == len(raw):
                return out, end
            raise RuntimeError("corrupt ledger: invalid complete record") from None
        if not isinstance(obj, dict):
            raise RuntimeError("corrupt ledger: record must be an object")
        out.append(obj)
        end += len(line)
    return out, end


def read_lines(path: Path) -> list[dict[str, Any]]:
    """Read complete records; only an incomplete, unterminated final record may be ignored."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return []
    return _lines(raw)[0]


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def iso(t: dt.datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


def parse_time(s: Any) -> dt.datetime | None:
    """ISO 8601 (or a bare date) to an aware datetime; naive values are taken as UTC."""
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


class LockedError(RuntimeError):
    pass


class Lock:
    """One run at a time, held by the process until close or exit. The file stays in place."""

    def __init__(self, path: Path, stale_minutes: float = 120):
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> Lock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            raise LockedError("another run holds %s" % self.path.name) from e
        self.fd = fd
        return self

    def __exit__(self, *exc: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
