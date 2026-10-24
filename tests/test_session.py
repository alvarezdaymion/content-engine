"""The showcase: the recording session is the unit of work, and nothing is planned outside one."""

import json

import pytest

from content_engine import ideas, pipeline, review
from content_engine import session as S
from content_engine.demo_data import take_words
from content_engine.publish import run as P

from .conftest import ANSWER, NOW, approved_session, record, render_session


def test_plan_turns_the_top_ideas_into_cards_inside_a_session(cfg):
    s = S.plan(cfg, [], NOW, record_on="2026-03-09")
    assert s.id == "2026-03-09" and (cfg.sessions_dir / s.id / "session.json").exists()
    assert [c["idea"] for c in s.cards] == ["invoices", "accounts"]
    assert all(c["n"] == i for i, c in enumerate(s.cards, 1))
    loaded = S.load_all(cfg)
    assert [x.id for x in loaded] == ["2026-03-09"] and loaded[0].cards == s.cards


def test_only_one_unrecorded_session_at_a_time(cfg):
    s = S.plan(cfg, [], NOW)
    with pytest.raises(S.SessionRuleError, match="planned but not recorded"):
        S.plan(cfg, [s], NOW)
    with pytest.raises(S.SessionRuleError, match="no unrecorded session"):
        S.plan(cfg, [], NOW, replace=True)


def test_replacing_the_open_session_releases_its_ideas_first(cfg):
    s = S.plan(cfg, [], NOW, n_cards=1)
    assert [c["idea"] for c in s.cards] == ["invoices"]
    s.edit_notes["1"] = {"cut_phrases": ["old note"]}
    again = S.plan(cfg, [s], NOW, n_cards=2, replace=True, record_on="2026-03-12")
    assert again.id == s.id and [c["idea"] for c in again.cards] == ["invoices", "accounts"]
    assert again.record_on == "2026-03-12" and again.edit_notes == {}
    assert len(S.load_all(cfg)) == 1


def test_a_session_holds_its_ideas_out_of_the_ranking(cfg):
    s = S.plan(cfg, [], NOW)
    result = ideas.ranked(cfg, NOW, S.idea_usage([s]))
    assert [i["id"] for i in result["ideas"]] == ["receipts"]
    assert {c["id"] for c in result["cooling"]} == {"invoices", "accounts"}


def test_recording_releases_unrecorded_cards_and_unlocks_the_next_session(cfg):
    s = S.plan(cfg, [], NOW)
    record(s, [1])
    pipeline.match(cfg, s)
    assert s.recorded and s.recorded_cards == [1]
    assert S.idea_usage([s]) == {"invoices": s.planned_at}
    nxt = S.plan(cfg, [s], NOW, record_on="2026-03-16")
    assert [c["idea"] for c in nxt.cards] == ["accounts", "receipts"]
    assert [x.id for x in S.load_all(cfg)] == [s.id, "2026-03-16"]


def test_takes_match_only_their_own_sessions_cards(cfg):
    first = S.plan(cfg, [], NOW, n_cards=1, sid="one")
    record(first, [1])
    pipeline.match(cfg, first)
    second = S.plan(cfg, [first], NOW, n_cards=1, sid="two")
    assert second.cards[0]["idea"] == "accounts"
    stray = take_words(first.card(1)["question"], ANSWER)  # answers session one's card
    second.takes_dir.mkdir(parents=True)
    (second.takes_dir / "take-01.json").write_text(json.dumps(stray))
    matches, _ = pipeline.match(cfg, second)
    assert matches["take-01"].status == "unmatched" and not second.recorded


def test_the_queue_holds_only_approved_unchanged_items(cfg):
    s = approved_session(cfg)
    queue, changed = S.queue([s])
    assert [(x.id, n) for x, n in queue] == [(s.id, 1)] and changed == []
    assert S.review_status(s, 2) == "pending"
    s.captions["1"]["full"] += " Edited after approval."
    queue, changed = S.queue([s])
    assert queue == [] and changed == [s.key(1)] and S.review_status(s, 1) == "changed"
    review.approve(s, [1], NOW)
    assert S.queue([s])[0] == [(s, 1)]


def test_re_running_edit_keeps_approvals_when_nothing_changed(cfg):
    s = approved_session(cfg)
    pipeline.edit(cfg, s)
    assert S.review_status(s, 1) == "approved"
    s.edit_notes["1"] = {"cut_phrases": ["put the due date in writing"]}
    pipeline.edit(cfg, s)
    assert S.review_status(s, 1) == "changed"


@pytest.mark.parametrize("key,value", [("disclosure", "Synthetic voice disclosure updated"),
                                       ("caption_words", 1), ("caption_chars", 8)])
def test_presentation_changes_require_approval_again(cfg, key, value):
    s = approved_session(cfg)
    cfg.data["edit"][key] = value
    pipeline.edit(cfg, s)
    assert S.review_status(s, 1) == "changed"


def test_unchanged_edit_keeps_verified_render_and_approval(cfg, monkeypatch):
    s = approved_session(cfg)
    render_session(cfg, s, monkeypatch)
    assert S.render_current(s, 1)
    loaded = S.Session.load(s.dir)
    assert S.render_current(loaded, 1)
    pipeline.edit(cfg, s)
    assert S.render_current(s, 1) and S.review_status(s, 1) == "approved"


def test_hosted_url_is_bound_to_the_render_and_approval(cfg, monkeypatch):
    s = approved_session(cfg)
    cfg.data["publish"]["video_base_url"] = "https://media.example.com/reels"
    render_session(cfg, s, monkeypatch)
    digest = s.edits["1"]["render_proof"]["sha256"]
    expected = "https://media.example.com/reels/%s-1-%s.mp4" % (s.id, digest)
    assert s.edits["1"]["video_url"] == expected
    assert P.make_post(cfg, s, 1, True).video_url == expected
    cfg.data["publish"]["video_base_url"] = "https://media.example.org/reels"
    pipeline.edit(cfg, s)
    assert S.render_current(s, 1) and S.review_status(s, 1) == "changed"
    assert s.edits["1"]["video_url"] != expected


def test_missing_retake_transcript_never_resurrects_an_older_take(cfg):
    s = approved_session(cfg)
    record(s, [1], name_prefix="take-03")
    pipeline.edit(cfg, s)
    assert s.edits["1"]["take"] == "take-03-01"
    assert review.approve(s, [1], NOW) == []
    (s.takes_dir / "take-03-01.json").unlink()
    pipeline.edit(cfg, s)
    assert s.edits["1"]["take"] == "take-03-01"
    assert s.takes["take-01"]["status"] == "superseded"
    assert S.review_status(s, 1) == "approved"
    record(s, [1], name_prefix="take-04")
    pipeline.edit(cfg, s)
    assert s.edits["1"]["take"] == "take-04-01"


def test_restored_superseded_transcript_keeps_its_history(cfg):
    s = approved_session(cfg)
    record(s, [1], name_prefix="take-03")
    pipeline.edit(cfg, s)
    assert review.approve(s, [1], NOW) == []
    s.save()
    older = s.takes_dir / "take-01.json"
    contents = older.read_bytes()
    older.unlink()
    pipeline.edit(cfg, s)
    s = S.Session.load(s.dir)
    assert s.takes["take-01"]["status"] == "superseded"
    assert s.takes["take-01"]["missing"]
    (s.takes_dir / "take-03-01.json").unlink()
    older.write_bytes(contents)
    pipeline.edit(cfg, s)
    assert s.edits["1"]["take"] == "take-03-01"
    assert S.review_status(s, 1) == "approved"
    record(s, [1], name_prefix="take-04")
    pipeline.edit(cfg, s)
    assert s.edits["1"]["take"] == "take-04-01"


def test_a_new_take_preserves_the_missing_takes_superseded_identity(cfg):
    s = approved_session(cfg)
    older = s.takes_dir / "take-01.json"
    contents = older.read_bytes()
    older.unlink()
    record(s, [1], name_prefix="retake")
    pipeline.edit(cfg, s)
    assert s.edits["1"]["take"] == "retake-01"
    assert s.takes["take-01"]["status"] == "superseded"
    assert review.approve(s, [1], NOW) == []
    (s.takes_dir / "retake-01.json").unlink()
    older.write_bytes(contents)
    pipeline.edit(cfg, s)
    assert s.edits["1"]["take"] == "retake-01"
    assert S.review_status(s, 1) == "approved"


@pytest.mark.parametrize("artifact", ["subtitles", "source", "render"])
@pytest.mark.parametrize("remove", [False, True])
def test_changed_or_missing_media_leaves_the_queue(cfg, artifact, remove):
    s = approved_session(cfg)
    source = s.takes_dir / "take-01.mp4"
    source.write_bytes(b"synthetic raw video")
    pipeline.edit(cfg, s)
    output = cfg.out_dir / s.id
    rendered = output / "renders" / "1.mp4"
    rendered.parent.mkdir(parents=True)
    rendered.write_bytes(b"synthetic cut video")
    assert review.approve(s, [1], NOW) == []
    path = {"source": source, "render": rendered, "subtitles": output / "captions" / "1.ass"}[artifact]
    if remove:
        path.unlink()
    else:
        path.write_bytes(b"changed synthetic bytes")
    assert S.review_status(s, 1) == "changed"
    assert S.queue([s])[0] == []


def test_post_texts_follow_the_cut_unless_edited_by_hand(cfg):
    s = approved_session(cfg)
    assert "put the due date in writing" in s.captions["2"]["full"]
    s.edit_notes["2"] = {"cut_phrases": ["put the due date in writing"]}
    pipeline.edit(cfg, s)
    assert "put the due date in writing" not in s.captions["2"]["full"]
    s.captions["2"]["full"] = "My own words."
    pipeline.edit(cfg, s)
    assert s.captions["2"]["full"] == "My own words."
    pipeline.edit(cfg, s, rewrite_captions=True)
    assert s.captions["2"]["full"] != "My own words."


def test_edit_picks_up_a_transcript_fixed_by_hand(cfg):
    s = approved_session(cfg)
    path = s.takes_dir / "take-02.json"
    words = json.loads(path.read_text())
    words = [w for w in words if w[0] != "Here"]  # drop a word: every later index shifts
    words = [[("quick" if w[0] == "short" else w[0]), w[1], w[2]] for w in words]
    path.write_text(json.dumps(words))
    pipeline.edit(cfg, s)
    assert "is the quick version." in s.captions["2"]["full"]
    assert S.review_status(s, 1) == "approved"


def test_deleting_old_takes_never_unrecords_a_session_or_drops_approvals(cfg):
    s = approved_session(cfg)
    for f in s.takes_dir.iterdir():
        f.unlink()
    pipeline.edit(cfg, s)
    assert s.recorded and S.review_status(s, 1) == "approved" and s.takes["take-01"]["missing"]
    with pytest.raises(S.SessionRuleError, match="no unrecorded session"):
        S.plan(cfg, [s], NOW, replace=True)
    record(s, [1], name_prefix="retake")  # a new take for card 1 replaces the missing one
    pipeline.match(cfg, s)
    assert s.takes["take-01"]["status"] == "superseded" and s.takes["retake-01"]["card"] == 1


def test_stage_follows_the_session_from_plan_to_done(cfg):
    s = S.plan(cfg, [], NOW)
    assert S.stage(s) == "planned"
    record(s, [1, 2])
    pipeline.match(cfg, s)
    assert S.stage(s) == "recorded"
    pipeline.edit(cfg, s)
    assert S.stage(s) == "in review"
    review.approve(s, [1], NOW)
    review.reject(s, [2], "re-record", NOW)
    assert S.stage(s) == "publishing"
    assert S.stage(s, finished={s.key(1)}) == "done"


def test_get_picks_the_latest_or_a_named_session(cfg):
    with pytest.raises(S.SessionRuleError, match="no sessions"):
        S.get([])
    s = S.plan(cfg, [], NOW)
    assert S.get([s]) is s and S.get([s], s.id) is s
    with pytest.raises(S.SessionRuleError, match="no session"):
        S.get([s], "missing")
