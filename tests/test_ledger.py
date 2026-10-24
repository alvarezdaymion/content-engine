"""The publish ledger: at most once per platform, across crashes and unclear answers."""

import os
import stat

import pytest

from content_engine.publish.ledger import Ledger, fold
from content_engine.publish.plan import plan_today, repair_candidates

from .conftest import NOW


def ev(event, platform="x", item="s#1", day="2026-03-02", **kw):
    return {"event": event, "platform": platform, "item": item, "day": day, "ts": "2026-03-02T12:00:00+00:00", **kw}


def test_done_is_final_and_later_events_are_ignored():
    st = fold([ev("attempt"), ev("commit"), ev("posted", url="u"), ev("attempt"), ev("failed", error="e")])
    assert st[("s#1", "x")].state == "done" and st[("s#1", "x")].url == "u"


def test_a_crash_before_the_publish_call_is_a_safe_failure():
    st = fold([ev("attempt")])[("s#1", "x")]
    assert st.state == "failed" and "nothing was sent" in st.error and st.attempts == 1


def test_a_crash_during_the_publish_call_is_held():
    st = fold([ev("attempt"), ev("commit")])[("s#1", "x")]
    assert st.state == "held" and "may have posted" in st.error


def test_an_orphaned_commit_is_held_and_counts_as_started():
    st = fold([ev("commit")])
    assert st[("s#1", "x")].state == "held"
    assert plan_today(["s#1", "s#2"], ["x"], st, "2026-03-02").kind == "done"
    assert repair_candidates(st, ["x"], "2026-03-02", 3)["retry"] == []


def test_uncertain_is_held_until_reconciled():
    events = [ev("attempt"), ev("commit"), ev("uncertain", error="timeout")]
    assert fold(events)[("s#1", "x")].state == "held"
    posted = fold(events + [ev("reconciled", outcome="posted", url="https://example.com/p")])[("s#1", "x")]
    assert posted.state == "done" and posted.url == "https://example.com/p"
    not_posted = fold(events + [ev("reconciled", outcome="not_posted")])[("s#1", "x")]
    assert not_posted.state == "failed"


def test_reconcile_resolves_an_interrupted_commit_too():
    st = fold([ev("attempt"), ev("commit"), ev("reconciled", outcome="not_posted")])[("s#1", "x")]
    assert st.state == "failed"


def test_reconciled_lines_only_move_held_platforms():
    st = fold([ev("attempt"), ev("failed", error="401"), ev("reconciled", outcome="posted")])[("s#1", "x")]
    assert st.state == "failed"


def test_retries_are_counted_and_an_old_open_attempt_settles_first():
    st = fold([ev("attempt"), ev("attempt"), ev("commit"), ev("posted")])[("s#1", "x")]
    assert st.state == "done" and st.attempts == 2


def test_file_is_append_only_fsynced_private_and_survives_a_torn_line(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append("s#1", "x", "attempt", NOW, "2026-03-02", by="daily")
    led.append("s#1", "x", "commit", NOW, "2026-03-02")
    with open(led.path, "a") as f:
        f.write('{"event": "posted", "item": "s#1", "plat')  # the process died mid-write
    assert stat.S_IMODE(os.stat(led.path).st_mode) == 0o600
    assert led.states()[("s#1", "x")].state == "held"
    assert [e["event"] for e in led.events()] == ["attempt", "commit"]


@pytest.mark.parametrize("tail", [b'{"event":', b'{"event": "posted", "item": "s#1", "platform": "x"}'])
def test_appending_after_an_unterminated_line_preserves_event_boundaries(tmp_path, tail):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.path.write_bytes(tail)
    if tail.endswith(b"}"):
        with pytest.raises(RuntimeError, match="corrupt"):
            led.append("s#2", "x", "attempt", NOW, "2026-03-02")
        return
    led.append("s#1", "x", "attempt", NOW, "2026-03-02")
    led.append("s#1", "x", "commit", NOW, "2026-03-02")
    assert led.states()[("s#1", "x")].state == "held"


def test_a_complete_record_without_a_newline_is_preserved(tmp_path):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append("s#1", "x", "attempt", NOW, "2026-03-02")
    led.append("s#1", "x", "commit", NOW, "2026-03-02")
    led.path.write_bytes(led.path.read_bytes().rstrip(b"\n"))
    led.append("s#2", "x", "attempt", NOW, "2026-03-02")
    assert led.states()[("s#1", "x")].state == "held"
    assert led.states()[("s#2", "x")].state == "failed"


@pytest.mark.parametrize("bad", [b'broken\n', b'[]\n', b'{"event":"unknown"}\n'])
def test_complete_corruption_stops_reading_and_appending(tmp_path, bad):
    led = Ledger(tmp_path / "ledger.jsonl")
    led.append("s#1", "x", "attempt", NOW, "2026-03-02")
    led.path.write_bytes(led.path.read_bytes() + bad)
    with pytest.raises(RuntimeError, match="corrupt"):
        led.states()
    with pytest.raises(RuntimeError, match="corrupt"):
        led.append("s#1", "x", "commit", NOW, "2026-03-02")


ENABLED = ["facebook", "x"]


def states(*events):
    return fold(list(events))


def test_plan_starts_the_next_approved_item():
    p = plan_today(["s#1", "s#2"], ENABLED, {}, "2026-03-02")
    assert (p.kind, p.item, p.platforms, p.left) == ("new", "s#1", ENABLED, 1)


def test_plan_posts_one_item_a_day_and_resumes_only_unattempted_platforms():
    st = states(ev("attempt", "facebook"), ev("commit", "facebook"), ev("posted", "facebook"))
    p = plan_today(["s#1", "s#2"], ENABLED, st, "2026-03-02")
    assert (p.kind, p.item, p.platforms) == ("resume", "s#1", ["x"])
    st = states(ev("attempt", "facebook"), ev("posted", "facebook"), ev("attempt", "x"), ev("failed", "x"))
    assert plan_today(["s#1", "s#2"], ENABLED, st, "2026-03-02").kind == "done"


def test_plan_moves_on_the_next_day_but_finishes_a_crashed_item_first():
    st = states(ev("attempt", "facebook"), ev("posted", "facebook"), ev("attempt", "x"), ev("failed", "x"))
    assert plan_today(["s#1", "s#2"], ENABLED, st, "2026-03-03").item == "s#2"
    crashed = states(ev("attempt", "facebook"), ev("posted", "facebook"))
    p = plan_today(["s#1", "s#2"], ENABLED, crashed, "2026-03-03")
    assert (p.kind, p.item, p.platforms) == ("resume", "s#1", ["x"])


def test_a_resumed_item_is_todays_item_so_no_second_item_starts_that_day():
    st = states(ev("attempt", "facebook", day="2026-03-01"), ev("posted", "facebook", day="2026-03-01"),
                ev("attempt", "x", day="2026-03-02"), ev("posted", "x", day="2026-03-02"))
    p = plan_today(["s#1", "s#2"], ENABLED, st, "2026-03-02")
    assert (p.kind, p.item) == ("done", "s#1")
    repaired = states(ev("attempt", "facebook", item="s#0", day="2026-03-01"),
                      ev("failed", "facebook", item="s#0", day="2026-03-01"),
                      ev("attempt", "facebook", item="s#0", day="2026-03-02", by="repair"))
    assert plan_today(["s#1"], ["facebook"], repaired, "2026-03-02").item == "s#1"


def test_plan_never_resumes_an_item_that_is_no_longer_approved():
    crashed = states(ev("attempt", "facebook"), ev("posted", "facebook"))
    assert plan_today(["s#2"], ENABLED, crashed, "2026-03-03").item == "s#2"


def test_plan_reports_waiting_empty_and_off():
    assert plan_today([], ENABLED, {}, "d", waiting=2).kind == "waiting"
    assert plan_today([], ENABLED, {}, "d").kind == "empty"
    assert plan_today(["s#1"], [], {}, "d").kind == "off"


def test_repair_retries_only_definite_failures_from_today_within_the_limit():
    st = states(ev("attempt", "facebook"), ev("failed", "facebook", error="503"),
                ev("attempt", "x"), ev("commit", "x"), ev("uncertain", "x"),
                ev("attempt", "facebook", item="s#0", day="2026-03-01"), ev("failed", "facebook", item="s#0",
                                                                            day="2026-03-01"))
    c = repair_candidates(st, ENABLED, "2026-03-02", 3)
    assert c["retry"] == [("s#1", "facebook")] and c["held"] == [("s#1", "x")]
    assert repair_candidates(st, ENABLED, "2026-03-02", 3, item="s#0")["retry"] == [("s#0", "facebook")]
    assert repair_candidates(st, ENABLED, "2026-03-02", 1)["exhausted"] == [("s#1", "facebook")]
    assert repair_candidates(st, ENABLED, "2026-03-02", 1, item="s#0")["exhausted"] == [("s#0", "facebook")]
    assert repair_candidates(st, ["x"], "2026-03-02", 3)["retry"] == []
