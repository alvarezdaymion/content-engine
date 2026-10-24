"""The session stages after planning: match the takes, then edit every matched card.

    match   transcripts in sessions/<id>/takes/ -> session.takes (rule 4: only this session's cards)
    edit    per matched card: trims, caption cues, graphics, the .ass track, post texts, an optional render,
            then the numbered review page at out/<id>/review/index.html. Post texts are regenerated from the
            cut on every edit, unless you changed them by hand in session.json (then --rewrite-captions does it).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from . import review
from .captions import build_cues, post_texts, validate_texts
from .matching import Match, match_takes
from .overlay import ass_document, place
from .render import render
from .session import Session, file_hash, hosted_url, presentation_fingerprint, render_current
from .store import write_text
from .transcript import find_takes, load_words
from .trims import plan_edit


def _hash(texts: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(texts, sort_keys=True).encode()).hexdigest()[:16]


def match(cfg: Any, session: Session) -> tuple[dict[str, Match], list[str]]:
    """Match every take that has a transcript. Returns (matches, takes still waiting for a transcript).

    A take matched earlier whose transcript has since been deleted keeps its match (marked "missing") unless a
    newer take now covers its card, so clearing old files never un-records a session or drops its edits."""
    with session.lock():
        return _match(cfg, session)


def _match(cfg: Any, session: Session) -> tuple[dict[str, Match], list[str]]:
    session.check_current()
    found = find_takes(session.takes_dir)
    words = {name: load_words(t["transcript"]) for name, t in found.items() if t["transcript"]}
    m = cfg["matching"]
    results = match_takes(session.cards, words, float(m["threshold"]), float(m["margin"]), int(m["window_words"]),
                          previous=session.takes)
    takes = {name: {**r.to_json(), "video": found[name]["video"].name if found[name]["video"] else None}
             for name, r in results.items()}
    for name, old in session.takes.items():
        if name not in takes and old.get("status") == "superseded":
            takes[name] = {**old, "missing": True}
    for name, old in session.takes.items():
        if name in takes or old.get("status") != "matched":
            continue
        replacements = [other for other, t in takes.items()
                        if t["status"] == "matched" and t.get("card") == old.get("card")]
        newer = [other for other in replacements if other not in session.takes
                 or (session.takes[other].get("status") != "superseded" and other > name)]
        if newer:
            takes[name] = {**old, "status": "superseded", "by": newer[-1], "missing": True}
            continue
        for other in replacements:
            results[other].status, results[other].by = "superseded", name
            takes[other].update(status="superseded", by=name)
        takes[name] = {**old, "missing": True}
    session.takes = dict(sorted(takes.items()))
    session._save()
    return results, [name for name, t in found.items() if not t["transcript"]]


def edit(cfg: Any, session: Session, render_video: bool = False,
         rewrite_captions: bool = False) -> list[dict[str, Any]]:
    """Edit every matched card and write the review page. Returns one row per card for the summary.
    Takes are matched again first, so a transcript fixed by hand is picked up."""
    with session.lock():
        return _edit(cfg, session, render_video, rewrite_captions)


def _edit(cfg: Any, session: Session, render_video: bool, rewrite_captions: bool) -> list[dict[str, Any]]:
    _match(cfg, session)
    out = cfg.out_dir / session.id
    ec = cfg["edit"]
    rows = []
    edited = set()
    for name, take in session.takes.items():
        if take.get("status") != "matched":
            continue
        if take.get("missing"):  # no transcript to rebuild from: keep the edit exactly as it was
            edited.add(str(take["card"]))
            continue
        n, card = take["card"], session.card(take["card"])
        words = load_words(session.takes_dir / ("%s.json" % name))
        notes = session.edit_notes.get(str(n), {})
        e = plan_edit(name, words, take["q_first"], take["q_last"], notes, ec)
        cues = build_cues(e, int(ec["caption_words"]), int(ec["caption_chars"]))
        graphics, g_problems = place(notes.get("graphics", []), e, words, take["q_last"])
        ass_path = write_text(out / "captions" / ("%d.ass" % n),
                              ass_document(cues, graphics, card["hook"], ec["disclosure"], e.segments[0].d,
                                           e.duration))
        generated = post_texts(card, e, cfg)
        previous = session.captions.get(str(n))
        last_auto = (session.edits.get(str(n)) or {}).get("texts_hash")
        by_hand = previous is not None and _hash(previous) != last_auto and not rewrite_captions
        texts = previous if by_hand and previous else generated
        previous_edit = session.edits.get(str(n)) or {}
        render_path = out / "renders" / ("%d.mp4" % n)
        status = "not rendered (run `content-engine edit --render`)"
        session.edits[str(n)] = {**e.summary(), "graphics": graphics, "cues": len(cues),
                                 "cue_preview": [c.text for c in cues[:8]], "render": status,
                                 "artifacts": {"subtitles": os.path.relpath(ass_path, session.dir),
                                               "render": os.path.relpath(render_path, session.dir)},
                                 "render_proof": previous_edit.get("render_proof"),
                                 "video": take.get("video"), "texts_hash": last_auto if by_hand else _hash(generated),
                                 "problems": e.problems + g_problems + validate_texts(texts,
                                                                                      cfg["cards"]["banned_words"])}
        session.captions[str(n)] = texts
        edit = session.edits[str(n)]
        if not render_current(session, n):
            edit.pop("render_proof", None)
        else:
            status = str(previous_edit.get("render", status))
        if render_video:
            edit.pop("render_proof", None)
            before = presentation_fingerprint(session, n)
            video = session.takes_dir / take["video"] if take.get("video") else None
            status = render(video, e.segments, render_path, ass_path)
            digest = file_hash(render_path)
            if (status.startswith("rendered") and digest is not None and before is not None
                    and before == presentation_fingerprint(session, n)):
                edit["render_proof"] = {"fingerprint": before, "sha256": digest}
        edit["render"] = status
        edit["video_url"] = hosted_url(str(cfg["publish"]["video_base_url"]).strip(), session.id, n,
                                       str((edit.get("render_proof") or {}).get("sha256") or ""))
        edited.add(str(n))
        rows.append({"card": n, "take": name, "raw": e.duration_raw, "cut": e.duration, "removed": e.removed,
                     "cues": len(cues), "graphics": len(graphics), "problems": session.edits[str(n)]["problems"],
                     "render": status})
    for stale in set(session.edits) - edited:  # a card whose take no longer matches loses its edit
        del session.edits[stale]
    session._save()
    review.write_page(cfg, session)
    return rows
