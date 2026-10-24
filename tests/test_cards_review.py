import pytest

from content_engine import carousel, review
from content_engine import session as S
from content_engine.cards import sheet_html, spoken, validate, write_card
from content_engine.ideas.filing import Idea

from .conftest import NOW, ago, approved_session, render_session

TOPIC = Idea("invoices", "Getting paid on time", "How do I get paid on time?", ["invoice"],
             context="Hypothetical: a client is 45 days late.", points=["Invoice the same day"], fit=3)


def entry(*evidence, score=50.0):
    counts: dict[str, int] = {}
    for e in evidence:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1
    return {"score": score, "trending": False, "counts": counts, "evidence": list(evidence)}


def test_a_recent_note_replaces_the_ideas_question_and_others_show_as_asked():
    e = entry({"kind": "question", "text": "Old question?", "published": ago(48)},
              {"kind": "question", "text": "My client is late again. What do I say?", "published": ago(2)},
              {"kind": "own_comment", "text": "Do you charge late fees?", "published": ago(5)},
              {"kind": "creator", "text": "Viral invoice tip", "multiple": 6.0, "published": ago(5)})
    card = write_card(1, TOPIC, e)
    assert card["question"] == "My client is late again. What do I say?"
    assert card["asked"] == ["Old question?", "Do you charge late fees?"]
    assert card["hook"] == TOPIC.title and card["idea"] == "invoices" and card["points"] == TOPIC.points
    assert "2 questions" in card["why"] and "best creator post 6.0x" in card["why"]
    assert spoken(card) == card["question"] + " " + TOPIC.context
    assert write_card(2, TOPIC, None)["question"] == TOPIC.question


def test_card_rules(make_cfg):
    c = make_cfg(cards={"banned_words": ["guarantee"]})
    good = write_card(1, TOPIC, None)
    assert validate([good], c) == []
    bad = dict(good, n=2, hook="H" * 61, question="Tell me everything we guarantee", points=["p"] * 7)
    problems = validate([bad], c)
    assert any("hook is 61" in p for p in problems) and any("question mark" in p for p in problems)
    assert any("talking points" in p for p in problems) and any("guarantee" in p for p in problems)


def test_recording_sheet_escapes_text_and_links_audio(tmp_path):
    s = S.Session("s1", "2026-03-09", ago(1), [dict(write_card(1, TOPIC, None), hook="<b>Hook</b>")],
                  dir=tmp_path)
    page = sheet_html(s, {1: "../sessions/s1/audio/1.mp3"})
    assert "&lt;b&gt;Hook&lt;/b&gt;" in page and 'src="../sessions/s1/audio/1.mp3"' in page
    assert "<b>Hook</b>" not in page


def test_approve_by_number_and_reject_with_a_note(cfg):
    s = approved_session(cfg)
    assert review.approve(s, [9], NOW) == ["card 9 has no edit yet (run `content-engine edit`)"]
    assert review.reject(s, [2], "Re-record the ending", NOW) == []
    assert S.review_status(s, 2) == "rejected"
    page = review.page_html(s, cfg.out_dir / s.id / "review", cfg.out_dir / s.id / "renders")
    assert "Re-record the ending" in page and "Approved (1)" in page
    assert page.index('id="i2"') < page.index("Approved (1)") < page.index('id="i1"')
    assert "content-engine approve %s 2" % s.id in page


def test_approval_refuses_post_texts_that_break_a_limit_or_a_banned_word(cfg):
    s = approved_session(cfg)
    s.captions["2"]["x"] = "x" * 281
    assert review.approve(s, [2], NOW) == ["card 2 not approved: x text is 281 characters (limit 280)"]
    s.captions["2"]["x"] = "Short enough"
    assert "banned word 'short'" in review.approve(s, [2], NOW, banned=["short"])[0]
    assert review.approve(s, [2], NOW) == [] and S.review_status(s, 2) == "approved"


def test_review_page_links_the_raw_take_and_render_relatively(cfg, monkeypatch):
    s = approved_session(cfg)
    render_session(cfg, s, monkeypatch)
    path = review.write_page(cfg, s)
    html = path.read_text()
    assert 'src="../../../sessions/%s/takes/take-02.mp4"' % s.id in html and 'src="../renders/2.mp4"' in html


CFG = {"max_words": 12, "max_slides": 6, "closing": "Save this for later."}
TEXTS = {"full": "How do I get paid on time?\n\nSend the invoice the same day. Put the due date in writing. "
                 "Follow up on day 1, 7 and 14 with the same friendly script.\n\nSend me your question.\n\n"
                 "The question in this video is read by an AI voice."}


def test_carousel_slides_are_word_for_word_from_the_approved_caption():
    slides = carousel.slides(TEXTS, CFG, skip=("Send me your question.",
                                               "The question in this video is read by an AI voice."))
    assert slides[0] == "How do I get paid on time?" and slides[-1] == "Save this for later."
    assert slides[1:-1] == ["Send the invoice the same day. Put the due date in writing.",
                            "Follow up on day 1, 7 and 14 with the same friendly script."]
    assert carousel.check(slides, TEXTS["full"], CFG["closing"]) == []
    assert carousel.check(slides[:1] + ["Send it whenever."], TEXTS["full"]) == [
        "slide 2 is not word for word from the approved caption"]
    assert len(carousel.slides(TEXTS, dict(CFG, max_words=3, max_slides=3))) == 3


def test_carousel_build_skips_the_call_to_action_and_footer(make_cfg):
    c = make_cfg(captions={"cta": "Send me your question."})
    deck = carousel.build(TEXTS, c)
    assert deck["problems"] == [] and "Send me your question." not in deck["slides"]
    assert len(deck["caption_fingerprint"]) == 16


@pytest.mark.parametrize("status", ["pending", "rejected"])
def test_only_approved_items_reach_the_queue(cfg, status):
    s = approved_session(cfg)
    if status == "rejected":
        review.reject(s, [2], "", NOW)
    assert [n for _, n in S.queue([s])[0]] == [1]
