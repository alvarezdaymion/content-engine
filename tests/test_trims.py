import pytest

from content_engine.config import DEFAULTS
from content_engine.trims import find_phrase, map_time, plan_edit

EDIT = dict(DEFAULTS["edit"])


def W(*spec):
    """Words from (text, start, end) triples."""
    return [(t, float(a), float(b)) for t, a, b in spec]


# question: 0..2; answer from index 3
WORDS = W(("Do", 1.0, 1.2), ("I", 1.25, 1.35), ("need?", 1.4, 1.8),
          ("Yes,", 2.6, 2.9), ("um,", 3.0, 3.2), ("you", 3.3, 3.5), ("do.", 3.55, 3.8),
          ("Pro-", 5.0, 5.2), ("Probably", 5.25, 5.7), ("today.", 5.75, 6.2),
          ("Let", 6.3, 6.4), ("me", 6.45, 6.5), ("say", 6.55, 6.7), ("that", 6.75, 6.9), ("again.", 6.95, 7.3),
          ("Open", 7.4, 7.7), ("one", 7.75, 7.9), ("now.", 7.95, 8.3))


def edit(notes=None, **cfg):
    return plan_edit("take", WORDS, 0, 2, notes, {**EDIT, **cfg})


def test_the_question_is_one_piece_and_long_pauses_become_cuts():
    e = edit()
    assert e.segments[0].kind == "question" and e.segments[0].a == pytest.approx(0.85)
    assert e.segments[0].b == pytest.approx(1.92)
    pauses = [r for r in e.removed if r["kind"] == "pause"]
    assert [(p["a"], p["b"]) for p in pauses] == [(3.8, 5.0)]
    assert [round(s.a, 2) for s in e.segments[1:]] == [2.48, 4.88]


def test_segments_are_padded_never_overlap_and_land_back_to_back():
    e = edit()
    for prev, nxt in zip(e.segments, e.segments[1:], strict=False):
        assert prev.b <= nxt.a and nxt.o == pytest.approx(prev.o + prev.d)
    assert e.duration == pytest.approx(sum(s.d for s in e.segments))
    assert e.duration < e.duration_raw


def test_a_cut_phrase_goes_even_without_a_pause_around_it():
    e = edit({"cut_phrases": ["let me say that again"]})
    phrase = [r for r in e.removed if r["kind"] == "phrase"]
    assert phrase and phrase[0]["text"] == "Let me say that again."
    kept = [w for w, *_ in e.words]
    assert "Let" not in kept and "Open" in kept
    assert map_time(e.segments, 6.6) is None and map_time(e.segments, 7.5) is not None
    seg_after = next(s for s in e.segments if s.a <= 7.5 <= s.b)
    assert seg_after.a >= 7.3  # the pad never reaches back into the removed word


def test_manual_ranges_fillers_and_missing_phrases():
    e = edit({"cut": [[5.0, 5.22]], "cut_phrases": ["not said"]})
    assert any(r["kind"] == "range" and r["text"] == "Pro-" for r in e.removed)
    assert "cut phrase not found: 'not said'" in e.problems
    assert "um," in [w for w, *_ in edit().words]  # fillers stay in the audio by default
    assert "um," not in [w for w, *_ in edit(cut_fillers=True).words]


def test_find_phrase_ignores_fillers_and_punctuation():
    assert find_phrase(WORDS, "yes you do", 3) == (3, 6)
    assert find_phrase(WORDS, "do i need", 3) is None  # only after the question
    assert find_phrase(WORDS, "") is None


def test_map_time_maps_kept_moments_and_drops_cut_ones():
    e = edit()
    assert map_time(e.segments, 1.0) == pytest.approx(0.15)
    assert map_time(e.segments, 4.4) is None
    q = e.segments[0].d
    assert map_time(e.segments, 2.6) == pytest.approx(q + 0.12)


def test_a_short_answer_is_flagged():
    words = W(("Why?", 0.5, 0.9), ("Because.", 1.5, 2.0))
    e = plan_edit("t", words, 0, 0, None, EDIT)
    assert any("only 1 words" in p for p in e.problems)
    nothing = plan_edit("t", words[:1], 0, 0, None, EDIT)
    assert len(nothing.segments) == 1 and any("only 0 words" in p for p in nothing.problems)
