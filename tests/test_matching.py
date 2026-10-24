from content_engine.demo_data import speak, take_words
from content_engine.matching import align, match_take, match_takes

CARDS = [
    {"n": 1, "question": "How do I get clients to pay my invoices on time?", "context": ""},
    {"n": 2, "question": "Do I really need a separate bank account for my business?",
     "context": "Hypothetical: a designer runs every payment through one personal account."},
    {"n": 3, "question": "When should a small business hire a bookkeeper?", "context": ""},
]
ANSWER = "Yes. Open one this week and send every client payment there, then pay yourself by transfer each month."


def tw(text, answer=ANSWER, **kw):
    return [tuple(w) for w in take_words(text, answer, **kw)]


def test_a_clean_take_matches_with_the_question_span():
    words = tw(CARDS[0]["question"])
    m = match_take("t1", words, CARDS)
    assert (m.status, m.card, m.score) == ("matched", 1, 1.0)
    assert words[m.q_first][0] == "How" and words[m.q_last][0] == "time?"
    assert m.q_start == words[0][1] and m.q_end == words[m.q_last][2]


def test_the_ai_read_context_line_belongs_to_the_question():
    text = CARDS[1]["question"] + " " + CARDS[1]["context"]
    words = tw(text)
    m = match_take("t2", words, CARDS)
    assert m.card == 2 and words[m.q_last][0] == "account." and words[m.q_last + 1][0] == "Yes."


def test_a_late_start_and_a_misheard_word_still_match():
    words = tw(CARDS[2]["question"], skip_question_words=2, misheard={"bookkeeper?": "book keeper?"})
    m = match_take("t3", words, CARDS)
    assert m.status == "matched" and m.card == 3 and 0.6 <= m.score < 1


def test_a_pickup_without_a_question_stays_unmatched():
    words = [tuple(w) for w in speak("Quick pickup for the end. Thanks for watching.", 0.2)[0]]
    m = match_take("p", words, CARDS)
    assert m.status == "unmatched" and m.card is None and "no card" in m.reason


def test_two_cards_with_the_same_question_are_not_guessed():
    cards = [{"n": 1, "question": "How do I pay myself?"}, {"n": 2, "question": "How do I pay myself?"}]
    m = match_take("t", tw("How do I pay myself?"), cards)
    assert m.status == "unmatched" and "ambiguous: cards 1 and 2" in m.reason
    close = [{"n": 1, "question": "How do I pay myself?"}, {"n": 2, "question": "How do I pay myself fairly?"}]
    assert match_take("t", tw("How do I pay myself?"), close).card == 1


def test_question_words_repeated_in_the_answer_do_not_stretch_the_match():
    q = CARDS[0]["question"]
    answer = "Good question. " + ("Filler words go here before the repeat. " * 6) + q
    words = tw(q, answer)
    m = match_take("t", words, CARDS)
    assert words[m.q_last][0] == "time?" and m.q_last < 12


def test_the_last_take_of_a_card_wins_and_earlier_ones_are_superseded():
    takes = {"take-01": tw(CARDS[0]["question"], "Hmm, let me start over."),
             "take-02": tw(CARDS[0]["question"]),
             "take-03": tw(CARDS[2]["question"])}
    result = match_takes(CARDS, takes)
    assert result["take-01"].status == "superseded" and result["take-01"].by == "take-02"
    assert result["take-02"].status == "matched" and result["take-03"].card == 3


def test_a_restored_superseded_take_does_not_override_selection_history():
    previous = {"take-01": {"status": "superseded", "card": 1, "by": "retake-01"},
                "retake-01": {"status": "matched", "card": 1}}
    result = match_takes(CARDS, {name: tw(CARDS[0]["question"]) for name in previous}, previous=previous)
    assert result["retake-01"].status == "matched"
    assert result["take-01"].status == "superseded" and result["take-01"].by == "retake-01"
    missing = match_takes(CARDS, {"take-01": tw(CARDS[0]["question"])}, previous=previous)
    assert missing["take-01"].status == "superseded" and missing["take-01"].by == "retake-01"


def test_a_new_take_can_replace_a_known_selection_regardless_of_filename_order():
    previous = {"take-01": {"status": "matched", "card": 1}}
    result = match_takes(CARDS, {"take-01": tw(CARDS[0]["question"]), "new-01": tw(CARDS[0]["question"])},
                         previous=previous)
    assert result["new-01"].status == "matched"
    assert result["take-01"].status == "superseded" and result["take-01"].by == "new-01"


def test_align_scores_the_share_heard_in_order():
    assert align(["a", "b", "c", "d"], ["x", "a", "b", "d"])[0] == 0.75
    assert align([], ["a"]) == (0.0, -1, -1)
    assert align(["a"], ["b"]) == (0.0, -1, -1)
