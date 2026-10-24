import json
import threading

import pytest

from content_engine import cli, config, review
from content_engine import session as S
from content_engine.cli import main
from content_engine.demo import demo_config, run_demo
from content_engine.demo_data import take_words
from content_engine.publish import run as P

from .conftest import approved_session


def test_demo_runs_the_whole_loop_offline(tmp_path):
    out = []
    root = tmp_path / "demo"
    assert run_demo(root, out=out.append) == 0
    text = out[0]
    for expected in ("1. Ideas ranked from 4 sources", "Planning another session now is refused",
                     "take-02  card 2  superseded by take-03", "take-05  no card", "Not recorded: card 4",
                     "Approved by number: 1, 2", "instagram  HELD", "already ran", "(repaired demo#1)",
                     "7. Carousel text for demo#1"):
        assert expected in text, expected
    cfg = demo_config(root)
    st = P.ledger_for(cfg, False).states()
    assert st[("demo#1", "instagram")].state == "held" and st[("demo#1", "threads")].state == "done"
    assert st[("demo#1", "facebook")].state == "done" and ("demo#2", "facebook") not in st
    assert (root / "out" / "demo" / "review" / "index.html").exists()
    deck = json.loads((root / "out" / "demo" / "carousels" / "1.json").read_text())
    assert deck["problems"] == [] and deck["slides"][-1] == "Save this for later."
    s = S.load_all(cfg)[0]
    assert S.stage(s, P.finished_items(st, list(cfg["publish"]["platforms"]))) == "in review"
    assert not (root / "state" / "ledger.jsonl").exists()


def test_demo_reruns_in_its_own_folder_but_never_clears_someone_elses(tmp_path):
    root = tmp_path / "demo"
    assert run_demo(root, out=lambda s: None) == 0
    assert run_demo(root, out=lambda s: None) == 0
    other = tmp_path / "work"
    other.mkdir()
    (other / "notes.txt").write_text("keep me")
    with pytest.raises(RuntimeError, match="not empty"):
        run_demo(other, out=lambda s: None)
    assert (other / "notes.txt").read_text() == "keep me"


def test_init_and_check(tmp_path, capsys):
    conf = tmp_path / "content-engine.toml"
    assert main(["init", "--config", str(conf)]) == 0
    assert conf.exists() and (tmp_path / "ideas.toml").exists()
    config.load(conf)  # the template is valid
    with pytest.raises(ValueError, match="privacy"):
        config.from_dict({"publish": {"youtube": {"privacy": "secret"}}})
    assert main(["check", "--config", str(conf)]) == 0
    out = capsys.readouterr().out
    assert "Config OK" in out and "Ideas: 2" in out and "Publish mode: dry-run" in out
    assert main(["init", "--config", str(conf)]) == 0 and "already exists" in capsys.readouterr().out


def test_cli_walks_one_session_from_note_to_dry_run_post(tmp_path, capsys, monkeypatch):
    conf = tmp_path / "content-engine.toml"
    conf.write_text('[sessions]\ncards = 1\n[publish]\nplatforms = ["facebook", "x"]\n')
    (tmp_path / "ideas.toml").write_text('[[idea]]\nid = "fees"\ntitle = "Late fees"\nquestion = "Should I '
                                         'charge a late fee?"\nkeywords = ["late fee"]\nfit = 3\n')
    c = ["--config", str(conf)]
    assert main(["note", "A client ignores my late fee. Should I still charge it?"] + c) == 0
    assert main(["ideas", "--force"] + c) == 0
    assert main(["plan", "--id", "s1"] + c) == 0
    assert "Should I still charge it?" in capsys.readouterr().out
    assert main(["plan"] + c) == 1 and "planned but not recorded" in capsys.readouterr().err
    cfg = config.load(conf)
    s = S.get(S.load_all(cfg), "s1")
    s.takes_dir.mkdir(parents=True)
    words = take_words(s.cards[0]["question"], "Yes, charge it every time, and put it in the contract up front "
                                               "so nobody is surprised when it shows up on the invoice.")
    (s.takes_dir / "take-01.json").write_text(json.dumps(words))
    assert main(["match"] + c) == 0
    assert main(["edit"] + c) == 0
    assert main(["approve", "s1", "1"] + c) == 0
    assert main(["publish"] + c) == 0
    assert main(["status"] + c) == 0
    out = capsys.readouterr().out
    assert "facebook   posted" in out and "x          posted" in out and "s1             publishing" not in out
    assert "s1             done" in out
    assert main(["reconcile", "s1#1", "x", "--posted", "--dry-run"] + c) == 1


def test_run_waits_for_post_after_and_honours_mode_off(tmp_path, capsys):
    conf = tmp_path / "content-engine.toml"
    conf.write_text('[publish]\nmode = "off"\n')
    assert main(["run", "--config", str(conf)]) == 0
    assert "Ideas: 0 ranked" in capsys.readouterr().out
    assert main(["publish", "--config", str(conf)]) == 0 and "off" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["match", "edit"])
def test_commands_reload_after_a_rejection_saved_during_session_selection(cfg, monkeypatch, command):
    s = approved_session(cfg)
    selected, resume = threading.Event(), threading.Event()
    load = S.Session.load
    codes, errors = [], []
    monkeypatch.setattr(cli, "_cfg", lambda _: cfg)

    def delayed_load(cls, folder):
        loaded = load(folder)
        if threading.current_thread().name == "session-command" and not selected.is_set():
            selected.set()
            assert resume.wait(5)
        return loaded

    def run_command():
        try:
            codes.append(main([command, s.id]))
        except BaseException as e:
            errors.append(e)

    monkeypatch.setattr(S.Session, "load", classmethod(delayed_load))
    thread = threading.Thread(target=run_command, name="session-command")
    thread.start()
    try:
        assert selected.wait(5)
        assert main(["reject", s.id, "1", "--note", "Synthetic correction required"]) == 0
    finally:
        resume.set()
        thread.join(5)
    assert not thread.is_alive() and not errors and codes == [0]
    assert S.review_status(S.Session.load(s.dir), 1) == "rejected"


def test_decision_commands_lock_loading_through_saving(cfg, monkeypatch):
    s = approved_session(cfg)
    deciding, resume = threading.Event(), threading.Event()
    reject = review.reject
    codes, errors = [], []
    monkeypatch.setattr(cli, "_cfg", lambda _: cfg)

    def delayed_reject(*args):
        deciding.set()
        assert resume.wait(5)
        return reject(*args)

    def run_rejection():
        try:
            codes.append(main(["reject", s.id, "1", "--note", "Synthetic correction required"]))
        except BaseException as e:
            errors.append(e)

    monkeypatch.setattr(review, "reject", delayed_reject)
    thread = threading.Thread(target=run_rejection)
    thread.start()
    try:
        assert deciding.wait(5)
        assert main(["approve", s.id, "2"]) == 1
    finally:
        resume.set()
        thread.join(5)
    assert not thread.is_alive() and not errors and codes == [0]
    persisted = S.Session.load(s.dir)
    assert S.review_status(persisted, 1) == "rejected"
    assert S.review_status(persisted, 2) == "pending"
