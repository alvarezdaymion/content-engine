"""The publisher: the commit boundary decides what is safe to retry, and the ledger makes it stick."""

import datetime as dt
import os
import subprocess
import sys

import pytest

from content_engine import pipeline, review
from content_engine import session as S
from content_engine.http import Http, HttpError, TransportError
from content_engine.publish import run as P
from content_engine.publish.ledger import Ledger
from content_engine.publish.platforms import ENV, ORDER
from content_engine.publish.platforms.base import Post, Result
from content_engine.publish.platforms.tiktok import TikTok
from content_engine.publish.platforms.youtube import YouTube
from content_engine.publish.simulate import Simulator
from content_engine.store import Lock, LockedError

from .conftest import NOW, FakeHttp, approved_session, render_session

DAY = "2026-03-02"


class Fake:
    name = "fake"

    def __init__(self, before=None, at_commit=None, after=None, commit=True, twice=False):
        self.before, self.at_commit, self.after, self.commit, self.twice = before, at_commit, after, commit, twice

    def publish(self, post, commit):
        if self.before:
            raise self.before
        if not self.commit:
            return Result("posted")

        def call():
            if self.at_commit:
                raise self.at_commit
            return {"id": "p1"}

        commit(call)
        if self.twice:
            commit(call)
        if self.after:
            raise self.after
        return Result("posted", "p1", "https://example.com/p1")


def run_one(tmp_path, connector, hide=None):
    led = Ledger(tmp_path / "ledger.jsonl")
    post = Post("s#1", "Title", {"full": "f", "short": "s", "x": "x"}, None, placeholder=True)
    row = P.attempt(led, "x", connector, post, DAY, "daily", lambda: NOW, hide)
    return row, [e["event"] for e in led.events()], led.states()[("s#1", "x")]


def test_success_writes_attempt_commit_and_the_outcome(tmp_path):
    row, events, st = run_one(tmp_path, Fake())
    assert events == ["attempt", "commit", "posted"] and row["state"] == "done" and st.url.endswith("/p1")


def test_an_error_before_the_publish_call_is_a_definite_failure(tmp_path):
    row, events, st = run_one(tmp_path, Fake(before=HttpError(503, "upload returned HTTP 503")))
    assert events == ["attempt", "failed"] and row["state"] == "failed" and st.state == "failed"


def test_a_clear_refusal_of_the_publish_call_is_a_failure(tmp_path):
    row, events, st = run_one(tmp_path, Fake(at_commit=HttpError(400, "publish returned HTTP 400: bad caption")))
    assert events == ["attempt", "commit", "failed"] and st.state == "failed"


@pytest.mark.parametrize("err", [TransportError("no response"), HttpError(503, "server error"),
                                 HttpError(408, "timeout"), HttpError(409, "conflict"), ValueError("parse")])
def test_an_unclear_publish_call_is_held(tmp_path, err):
    row, events, st = run_one(tmp_path, Fake(at_commit=err))
    assert events == ["attempt", "commit", "uncertain"] and row["state"] == "held" and st.state == "held"


def test_anything_failing_after_the_publish_call_is_held(tmp_path):
    row, _, st = run_one(tmp_path, Fake(after=RuntimeError("permalink read blew up")))
    assert row["state"] == "held" and st.state == "held"
    row, _, st = run_one(tmp_path / "b", Fake(twice=True))
    assert row["state"] == "held" and "only one publish call" in st.error


def test_a_connector_that_never_publishes_is_a_failure(tmp_path):
    row, events, _ = run_one(tmp_path, Fake(commit=False))
    assert events == ["attempt", "failed"] and "without making the publish call" in row["error"]


def test_a_crash_inside_the_publish_call_leaves_it_held(tmp_path):
    with pytest.raises(KeyboardInterrupt):
        run_one(tmp_path, Fake(at_commit=KeyboardInterrupt()))
    assert Ledger(tmp_path / "ledger.jsonl").states()[("s#1", "x")].state == "held"
    with pytest.raises(KeyboardInterrupt):
        run_one(tmp_path / "b", Fake(before=KeyboardInterrupt()))
    assert Ledger(tmp_path / "b" / "ledger.jsonl").states()[("s#1", "x")].state == "failed"


def test_short_writes_are_completed_before_publishing(tmp_path, monkeypatch):
    write = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: write(fd, data[:7]))
    with pytest.raises(KeyboardInterrupt):
        run_one(tmp_path, Fake(at_commit=KeyboardInterrupt()))
    assert Ledger(tmp_path / "ledger.jsonl").states()[("s#1", "x")].state == "held"


def test_a_zero_length_write_never_reaches_the_connector(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "write", lambda fd, data: 0)
    reached = []

    class Connector:
        def publish(self, post, commit):
            reached.append(post.item)
            return commit(lambda: Result("posted"))

    with pytest.raises(OSError):
        run_one(tmp_path, Connector())
    assert reached == []


def test_an_active_lock_never_expires_or_gets_unlinked(tmp_path):
    path = tmp_path / "publish.lock"
    with Lock(path):
        os.utime(path, (0, 0))
        with pytest.raises(LockedError, match="another run"):
            with Lock(path):
                pass
    assert path.exists()
    with Lock(path):
        pass


def test_a_crashed_process_releases_its_lock(tmp_path):
    path = tmp_path / "publish.lock"
    code = ("import os, sys\nfrom pathlib import Path\nfrom content_engine.store import Lock\n"
            "with Lock(Path(sys.argv[1])):\n os._exit(0)\n")
    subprocess.run([sys.executable, "-c", code, str(path)], check=True)
    with Lock(path):
        pass


def test_known_secrets_are_redacted_from_errors(tmp_path):
    row, _, st = run_one(tmp_path, Fake(before=RuntimeError("bad token tok-123456 sent")), hide=["tok-123456"])
    assert "tok-123456" not in row["error"] and "[redacted]" in st.error


@pytest.mark.parametrize("platform", ["youtube", "tiktok"])
def test_refreshed_credentials_never_reach_the_summary_or_ledger(tmp_path, platform):
    access, refresh = "test-new-access-token", "test-new-refresh-token"
    if platform == "youtube":
        fake = FakeHttp({("POST", "/token"): {"access_token": access},
                         ("POST", "/upload/youtube/v3/videos"): (400, {"error": {"message": access}})})
        connector = YouTube(Http(fake), "test-client", "test-secret", "test-refresh")
    else:
        fake = FakeHttp({("POST", "/v2/oauth/token/"): {"access_token": access, "refresh_token": refresh},
                         ("POST", "/v2/post/publish/inbox/video/init/"): {
                             "error": {"code": "bad", "message": access + " " + refresh}}})
        connector = TikTok(Http(fake), "test-client", "test-secret", "test-refresh")
    led = Ledger(tmp_path / "ledger.jsonl")
    post = Post("s#1", "Title", {"full": "f", "short": "s", "x": "x"}, None, placeholder=True)
    row = P.attempt(led, platform, connector, post, DAY, "daily", lambda: NOW)
    assert row["state"] == "failed"
    assert access not in str(row) + led.path.read_text()
    assert refresh not in str(row) + led.path.read_text()


def test_repair_creates_carousel_after_every_initial_platform_failed(cfg):
    s = approved_session(cfg)
    cfg.data["publish"]["platforms"] = ["facebook"]
    daily(cfg, [s], simulate={"facebook": "commit-reject"})
    deck = cfg.out_dir / s.id / "carousels" / "1.json"
    assert not deck.exists()
    P.repair(cfg, [s], NOW, False, out=lambda _: None)
    assert deck.is_file()
    original = deck.read_bytes()
    P.repair(cfg, [s], NOW, False, out=lambda _: None)
    assert deck.read_bytes() == original


def daily(c, sessions, now=NOW, live=False, **kw):
    out = []
    code = P.daily(c, sessions, now, live, out=out.append, sleep=lambda s: None, **kw)
    return code, "\n".join(out)


def test_dry_run_posts_to_all_six_and_holds_the_unclear_one(cfg):
    s = approved_session(cfg)
    code, text = daily(cfg, [s], simulate={"instagram": "commit-timeout"})
    st = P.ledger_for(cfg, False).states()
    assert code == 0 and {p: st[(s.key(1), p)].state for p in ORDER} == {
        "facebook": "done", "instagram": "held", "threads": "done", "x": "done", "youtube": "done", "tiktok": "done"}
    assert st[(s.key(1), "youtube")].status == "uploaded" and st[(s.key(1), "tiktok")].status == "drafted"
    assert "content-engine reconcile %s instagram" % s.key(1) in text
    assert (cfg.out_dir / s.id / "carousels" / "1.json").exists()
    assert not (cfg.state_dir / "ledger.jsonl").exists()  # the live ledger is untouched


def test_one_item_a_day_and_held_platforms_are_never_retried(cfg):
    s = approved_session(cfg)
    daily(cfg, [s], simulate={"instagram": "commit-timeout"})
    before = len(P.ledger_for(cfg, False).events())
    code, text = daily(cfg, [s])
    assert "already ran" in text and len(P.ledger_for(cfg, False).events()) == before
    out = []
    P.repair(cfg, [s], NOW, False, out=out.append)
    assert "HELD" in out[0] and len(P.ledger_for(cfg, False).events()) == before


def test_repair_fills_a_definite_failure_and_reconcile_unlocks_a_held_one(cfg):
    s = approved_session(cfg)
    daily(cfg, [s], simulate={"threads": "prepare-error-once", "x": "commit-timeout"})
    out = []
    assert P.repair(cfg, [s], NOW, False, out=out.append, sleep=lambda x: None) == 0
    st = P.ledger_for(cfg, False).states()
    assert st[(s.key(1), "threads")].state == "done" and st[(s.key(1), "threads")].attempts == 2
    assert st[(s.key(1), "x")].state == "held"
    with pytest.raises(ValueError, match="not held"):
        P.reconcile(cfg, s.key(1), "facebook", True, NOW, live=False)
    assert "not posted" in P.reconcile(cfg, s.key(1), "x", False, NOW, live=False)
    P.repair(cfg, [s], NOW, False, item=s.key(1), out=out.append)
    assert P.ledger_for(cfg, False).states()[(s.key(1), "x")].state == "done"


def test_the_next_day_posts_the_next_approved_item(cfg):
    s = approved_session(cfg)
    review.approve(s, [2], NOW)
    daily(cfg, [s])
    code, text = daily(cfg, [s], now=NOW + dt.timedelta(days=1))
    assert "Today: %s" % s.key(2) in text and "Record the next session" in text


def test_todays_item_edited_after_its_run_ends_the_day_quietly(cfg, monkeypatch):
    s = approved_session(cfg)
    review.approve(s, [2], NOW)
    monkeypatch.setitem(cfg.data["publish"], "platforms", ["facebook"])
    daily(cfg, [s])
    s.captions["1"]["full"] += " changed"
    monkeypatch.setitem(cfg.data["publish"], "platforms", ["facebook", "x"])
    code, text = daily(cfg, [s])
    assert "already ran" in text and (s.key(1), "x") not in P.ledger_for(cfg, False).states()


def test_items_changed_after_approval_are_not_posted(cfg):
    s = approved_session(cfg)
    s.captions["1"]["x"] = "Edited after approval"
    code, text = daily(cfg, [s])
    assert "Nothing approved to post" in text and "Changed since approval" in text
    assert P.ledger_for(cfg, False).events() == []


def test_pause_and_lock_stop_publishing(cfg):
    s = approved_session(cfg)
    (cfg.state_dir / "PAUSED").write_text("")
    assert "paused" in daily(cfg, [s])[1]
    out = []
    P.repair(cfg, [s], NOW, False, out=out.append)
    assert "paused" in out[0]
    (cfg.state_dir / "PAUSED").unlink()
    with Lock(P.state_dir(cfg, False) / "publish.lock"):
        code, text = daily(cfg, [s])
    assert code == 1 and "another run holds" in text and P.ledger_for(cfg, False).events() == []


def test_an_unowned_lock_file_is_reused_without_deletion(tmp_path):
    lock = tmp_path / "publish.lock"
    lock.write_text("123")
    old = NOW.timestamp() - 4 * 3600
    os.utime(lock, (old, old))
    with Lock(lock, stale_minutes=120):
        assert lock.exists()
    assert lock.exists()


def test_live_mode_needs_credentials_and_a_render_before_anything_is_sent(cfg, monkeypatch):
    s = approved_session(cfg)
    for p in ORDER:
        for v in ENV[p]:
            monkeypatch.delenv(v, raising=False)
    code, text = daily(cfg, [s], live=True)
    assert code == 1 and "not run: set FACEBOOK_PAGE_ID" in text
    for p in ORDER:
        for v in ENV[p]:
            monkeypatch.setenv(v, "test-%s-value" % v.lower())
    code, text = daily(cfg, [s], live=True)
    assert code == 1 and "no render" in text and P.ledger_for(cfg, True).events() == []
    render_session(cfg, s, monkeypatch)
    monkeypatch.setitem(cfg.data["publish"], "video_base_url", "https://media.example.com/reels")
    sim = Simulator()
    code, text = daily(cfg, [s], live=True, transport=sim)
    st = P.ledger_for(cfg, True).states()
    assert code == 0 and all(st[(s.key(1), p)].state == "done" for p in ORDER)
    assert ("HEAD", "https://media.example.com/reels/%s-1.mp4" % s.id) in sim.calls
    assert P.ledger_for(cfg, False).events() == []


@pytest.mark.parametrize("rebuild", ["skipped", "failed", "missing", "changed"])
def test_live_publish_and_repair_reject_unverified_renders(cfg, monkeypatch, rebuild):
    s = approved_session(cfg)
    render_session(cfg, s, monkeypatch)
    monkeypatch.setitem(cfg.data["publish"], "platforms", ["facebook"])
    for v in ENV["facebook"]:
        monkeypatch.setenv(v, "test-value-" + v.lower())
    rendered = cfg.out_dir / s.id / "renders" / "1.mp4"
    s.edit_notes["1"] = {"cut_phrases": ["put the due date in writing"]}
    if rebuild == "failed":
        monkeypatch.setattr(pipeline, "render", lambda *args: "render failed: synthetic failure")
    pipeline.edit(cfg, s, render_video=rebuild == "failed")
    if rebuild == "missing":
        rendered.unlink()
    elif rebuild == "changed":
        rendered.write_bytes(b"synthetic replacement")
    assert review.approve(s, [1], NOW) == []
    sim = Simulator()
    code, _ = daily(cfg, [s], live=True, transport=sim)
    assert code == 1 and sim.calls == [] and P.ledger_for(cfg, True).events() == []
    led = P.ledger_for(cfg, True)
    led.append(s.key(1), "facebook", "attempt", NOW, DAY)
    led.append(s.key(1), "facebook", "failed", NOW, DAY)
    P.repair(cfg, [s], NOW, True, transport=sim, out=lambda _: None)
    assert sim.calls == [] and led.states()[(s.key(1), "facebook")].attempts == 1


def test_a_render_changed_during_preparation_never_reaches_commit(cfg, monkeypatch):
    s = approved_session(cfg)
    render_session(cfg, s, monkeypatch)
    cfg.data["publish"]["platforms"] = ["facebook"]
    for key in ENV["facebook"]:
        monkeypatch.setenv(key, "test-" + key.lower())
    sim = Simulator()
    writes = []

    def transport(method, url, headers, body, timeout):
        if "rupload.facebook.com" in url:
            (cfg.out_dir / s.id / "renders" / "1.mp4").write_bytes(b"changed synthetic render")
        if body and b"upload_phase=finish" in body:
            writes.append(body)
        return sim(method, url, headers, body, timeout)

    daily(cfg, [s], live=True, transport=transport)
    assert writes == []
    assert P.ledger_for(cfg, True).states()[(s.key(1), "facebook")].state == "failed"


def test_live_instagram_without_a_video_url_fails_before_publishing(cfg, monkeypatch):
    s = approved_session(cfg)
    render_session(cfg, s, monkeypatch)
    monkeypatch.setitem(cfg.data["publish"], "platforms", ["instagram"])
    for v in ENV["instagram"]:
        monkeypatch.setenv(v, "test-value-%s" % v.lower())
    code, text = daily(cfg, [s], live=True, transport=Simulator())
    st = P.ledger_for(cfg, True).states()[(s.key(1), "instagram")]
    assert st.state == "failed" and "video_base_url" in st.error


def test_finished_items_need_every_enabled_platform_done(cfg):
    s = approved_session(cfg)
    daily(cfg, [s], simulate={"tiktok": "commit-reject"})
    st = P.ledger_for(cfg, False).states()
    assert st[(s.key(1), "tiktok")].state == "failed"
    assert P.finished_items(st, list(ORDER)) == set()
    assert P.finished_items(st, ["facebook", "x"]) == {s.key(1)}
    assert S.stage(s, P.finished_items(st, ["facebook"])) == "in review"  # card 2 is still pending
