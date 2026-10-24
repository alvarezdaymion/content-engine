import datetime as dt
import json

import pytest

from content_engine import config, pipeline, review
from content_engine import session as S
from content_engine.demo_data import take_words
from content_engine.ideas.evidence import EvidenceStore

NOW = dt.datetime(2026, 3, 2, 15, 0, tzinfo=dt.UTC)


def ago(hours: float) -> str:
    return (NOW - dt.timedelta(hours=hours)).isoformat()


class FakeHttp:
    """A transport that answers from a route table keyed by (method, path) and records every call."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        import urllib.parse
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        parts = urllib.parse.urlsplit(url)
        key = (method, parts.path)
        if key not in self.routes:
            key = (method, parts.hostname)
        if key not in self.routes:
            return 404, {}, json.dumps({"error": {"message": "no route for %s %s" % (method, parts.path)}}).encode()
        resp = self.routes[key]
        if callable(resp):
            resp = resp(url, headers, body)
        if isinstance(resp, Exception):
            raise resp
        if isinstance(resp, tuple):
            status, data = resp[0], resp[1]
            hdrs = resp[2] if len(resp) > 2 else {}
        else:
            status, data, hdrs = 200, resp, {}
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        return status, hdrs, raw


@pytest.fixture
def make_cfg(tmp_path):
    def make(**data):
        return config.from_dict(data, tmp_path)
    return make


IDEAS = """
[[idea]]
id = "invoices"
title = "Getting paid on time"
question = "How do I get clients to pay my invoices on time?"
keywords = ["invoice"]
fit = 3

[[idea]]
id = "accounts"
title = "Separate accounts"
question = "Do I really need a separate bank account for my business?"
keywords = ["separate account"]
fit = 3

[[idea]]
id = "receipts"
title = "Receipts"
question = "What is the easiest way to keep track of receipts?"
keywords = ["receipt"]
fit = 2
"""
ANSWER = ("Here is the short version. Send it the same day the work ships, put the due date in writing, "
          "and follow up on a fixed schedule so it never feels personal.")


@pytest.fixture
def cfg(make_cfg):
    """A workspace with the three ideas above, some evidence for each, and two cards per session."""
    c = make_cfg(sessions={"cards": 2})
    c.path("ideas").write_text(IDEAS)
    store = EvidenceStore(c.state_dir / "evidence.json")
    store.merge([{"id": "n%d" % i, "kind": kind, "text": text, "published": ago(h)} for i, (kind, text, h) in
                 enumerate([("question", "Clients pay my invoice late, what now?", 2),
                            ("question", "Late invoice again?", 5),
                            ("own_comment", "Do I need a separate account?", 3),
                            ("news", "Receipt apps add scanning", 10)])], NOW)
    store.save()
    return c


def record(s, card_numbers, name_prefix="take"):
    """Write one synthetic take transcript per card: the question, then ANSWER."""
    for i, n in enumerate(card_numbers, 1):
        card = s.card(n)
        words = take_words(card["question"], ANSWER)
        (s.takes_dir).mkdir(parents=True, exist_ok=True)
        (s.takes_dir / ("%s-%02d.json" % (name_prefix, i))).write_text(json.dumps(words))


def approved_session(cfg):
    """A session with cards 1 and 2 recorded and edited, and card 1 approved."""
    s = S.plan(cfg, [], NOW)
    record(s, [1, 2])
    pipeline.match(cfg, s)
    pipeline.edit(cfg, s)
    assert review.approve(s, [1], NOW) == []
    s.save()
    return s


def render_session(cfg, s, monkeypatch):
    for name in s.takes:
        (s.takes_dir / (name + ".mp4")).write_bytes(b"synthetic source video")

    def render(video, segments, output, ass_path):
        assert video.is_file() and ass_path.is_file()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"synthetic rendered video")
        return "rendered with captions burned in"

    monkeypatch.setattr(pipeline, "render", render)
    pipeline.edit(cfg, s, render_video=True)
    assert review.approve(s, [1], NOW) == []
    s.save()
