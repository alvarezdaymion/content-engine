"""What every platform connector gets and returns.

A connector's `publish(post, commit)` does its preparation (uploads, containers, processing waits) and then makes
exactly one irreversible call through `commit(fn)`: the call that makes the post exist on the platform. The runner
(publish/run.py) writes the ledger's commit line before that call and classifies every outcome:

    error before commit            -> failed (nothing that could post was sent)
    commit refused with HTTP 4xx   -> failed (the platform rejected it), except 408 and 409
    commit timeout, 5xx, 408, 409  -> uncertain, held for a person to check
    any error after commit         -> uncertain (it went through; something after it broke)

So connectors don't decide what is safe to retry; the commit boundary does. Reads after the commit (a permalink, a
processing status) are best effort and never turn a post into a failure.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

Commit = Callable[[Callable[[], Any]], Any]
PLACEHOLDER = b"\x00" * 2048  # stands in for a missing render in dry runs only


class PublishError(RuntimeError):
    """A known, explainable failure; the message is shown as-is."""


@dataclass
class Post:
    item: str  # "<session>#<card>"
    title: str  # YouTube title
    texts: dict[str, str]  # full, short, x
    video: Path | None
    video_url: str = ""  # where Instagram and Threads fetch the file
    placeholder: bool = False
    video_sha256: str = ""
    verify: Callable[[], None] | None = None

    def video_bytes(self) -> bytes:
        if self.video is not None and self.video.exists():
            data = self.video.read_bytes()
            if self.video_sha256 and hashlib.sha256(data).hexdigest() != self.video_sha256:
                raise PublishError("rendered video changed for %s; review and approve again" % self.item)
            return data
        if self.placeholder:
            return PLACEHOLDER
        raise PublishError("no rendered video for %s" % self.item)


@dataclass
class Result:
    status: str  # posted | uploaded | drafted
    post_id: str = ""
    url: str = ""
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class Connector(Protocol):
    name: str

    def publish(self, post: Post, commit: Commit) -> Result: ...


def need(cond: Any, message: str) -> None:
    if not cond:
        raise PublishError(message)


def best_effort(read: Callable[[], Any], default: Any = None) -> Any:
    try:
        return read()
    except Exception:
        return default


def wait_for(read: Callable[[], Any], done: Callable[[Any], bool], failed: Callable[[Any], str | None], what: str,
             sleep: Callable[[float], None] = time.sleep, every: float = 5, tries: int = 60) -> Any:
    """Poll until `done`. `failed` returns an error text to stop early. Used before the commit, so giving up is a
    plain failure: nothing has been published yet."""
    last = None
    for _ in range(tries):
        last = read()
        err = failed(last)
        if err:
            raise PublishError("%s failed: %s" % (what, err))
        if done(last):
            return last
        sleep(every)
    raise PublishError("%s still not ready after %d checks" % (what, tries))
