import pytest

from content_engine import config
from content_engine.captions import LIMITS, build_cues, display_word, post_texts, validate_texts
from content_engine.overlay import ass_document, ass_time, place
from content_engine.trims import plan_edit

from .test_trims import EDIT, WORDS


def edit(notes=None):
    return plan_edit("take", WORDS, 0, 2, notes, EDIT)


def test_cues_are_short_split_by_speaker_and_skip_fillers_and_fragments():
    cues = build_cues(edit())
    texts = [c.text for c in cues]
    assert texts[0] == "Do I need?" and cues[0].who == "ai"
    assert all(len(c.words) <= 3 and len(c.text) <= 16 for c in cues)
    joined = " ".join(texts)
    assert "um" not in joined and "Pro-" not in joined and "Probably" in joined
    assert all(c.who == "creator" for c in cues[1:])


def test_cue_times_are_reel_times_in_order():
    e = edit()
    cues = build_cues(e)
    for a, b in zip(cues, cues[1:], strict=False):
        assert a.start < a.end <= b.start + 1e-9
    assert cues[-1].end <= e.duration + 0.7
    assert cues[0].words[0][1] == pytest.approx(0.15)


def test_display_words_drop_trailing_punctuation_and_fix_restarts():
    assert display_word("today.") == "today"
    assert display_word("need?") == "need?"
    assert display_word("Ob-obviously,") == "Obviously"
    assert display_word("ener-energy") == "energy"
    assert display_word("well-known") == "well-known"


def cfg(**captions):
    return config.from_dict({"captions": captions})


def test_post_texts_fit_every_limit_and_keep_the_disclosure():
    card = {"hook": "Do you need a separate account?", "question": "Do I need a separate account?"}
    texts = post_texts(card, edit(), cfg())
    assert texts["title"] == card["hook"] and texts["full"].startswith(card["question"])
    assert all(len(texts[k]) <= LIMITS[k] for k in LIMITS)
    assert texts["full"].endswith("read by an AI voice.") and texts["x"].endswith("read by an AI voice.")
    assert "Yes, you do." in texts["full"] and "um" not in texts["full"]
    assert validate_texts(texts, []) == []


def test_long_answers_drop_whole_sentences_to_fit():
    long = edit()
    long.words = [(w, a, b, who) for w, a, b, who in long.words] + [
        ("This is a much longer sentence that keeps going for quite a while.", 9, 10, "creator")] * 12
    card = {"hook": "Hook", "question": "Q" * 200 + "?"}
    texts = post_texts(card, long, cfg())
    assert len(texts["x"]) <= 280 and len(texts["short"]) <= 500
    assert validate_texts({**texts, "x": "x" * 300}, [])[0].startswith("x text is 300")
    assert "longer" not in texts["full"]  # the body is the answer's first three sentences
    assert validate_texts(texts, ["probably"]) == ["uses the banned word 'probably'"]


def test_graphics_anchor_to_said_phrases_and_drop_cut_ones():
    e = edit({"cut_phrases": ["let me say that again"]})
    specs = [{"type": "stat", "at": "open one now", "value": "1", "label": "account"},
             {"type": "callout", "at": "say that again", "text": "gone"},
             {"type": "callout", "at": "never said", "text": "x"},
             {"type": "spinner", "at": "yes"}]
    placed, problems = place(specs, e, WORDS, 2)
    assert [g["type"] for g in placed] == ["stat"] and placed[0]["value"] == "1"
    assert len(problems) == 3 and "not in the cut" in problems[0] and "never said" in problems[1]


def test_ass_track_has_header_disclosure_active_words_and_graphics():
    e = edit()
    cues = build_cues(e)
    placed, _ = place([{"type": "callout", "at": "open one now", "text": "Open {one} now"}], e, WORDS, 2)
    doc = ass_document(cues, placed, "Hook {x}", "Question read by an AI voice", e.segments[0].d, e.duration)
    events = [ln for ln in doc.splitlines() if ln.startswith("Dialogue:")]
    assert events[0].endswith("Header,,0,0,0,,Hook (x)")
    assert ",Disclosure," in events[1] and ass_time(e.segments[0].d) in events[1]
    word_events = [ln for ln in events if ",Caption" in ln]
    assert len(word_events) == sum(len(c.words) for c in cues)
    assert "{\\c&H000AD6FF&}Do{\\r} I need?" in word_events[0]
    assert any(",Callout," in ln and "Open (one) now" in ln for ln in events)
    assert ass_time(3725.5) == "1:02:05.50"
