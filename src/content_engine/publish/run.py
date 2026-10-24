"""Publishing: the daily run, repair and reconcile.

    daily      posts today's item (plan.py) to each enabled platform on its own; one failure never blocks another
    repair     retries platforms that definitely did not get the post
    reconcile  records what a person found on a held platform

Every attempt goes through `attempt()`, which writes the ledger around the one irreversible call (ledger.py,
platforms/base.py). A `state/PAUSED` file stops all three. Dry runs talk to the simulator and keep their own ledger
in state/dry-run/, so they never touch the live ledger or a real account.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import carousel
from .. import session as session_mod
from ..http import Http, HttpError, Transport
from ..store import Lock, LockedError, now_utc, write_json
from .ledger import Ledger, PlatformState
from .plan import plan_today, repair_candidates
from .platforms import ENV, ORDER, Post, build, missing_env
from .platforms.base import Connector
from .simulate import Simulator

Out = Callable[[str], None]
LABEL = {"facebook": "Facebook", "instagram": "Instagram", "threads": "Threads", "x": "X", "youtube": "YouTube",
         "tiktok": "TikTok"}


def state_dir(cfg: Any, live: bool) -> Path:
    return cfg.state_dir if live else cfg.state_dir / "dry-run"


def ledger_for(cfg: Any, live: bool) -> Ledger:
    return Ledger(state_dir(cfg, live) / "ledger.jsonl")


def paused(cfg: Any) -> bool:
    return (cfg.state_dir / "PAUSED").exists()


def _secrets(platform: str, live: bool) -> list[str]:
    return [os.environ[k] for k in ENV[platform] if live and os.environ.get(k)]


def _error(e: BaseException, hide: list[str]) -> str:
    text = str(e) if isinstance(e, (HttpError, RuntimeError)) else "%s: %s" % (type(e).__name__, e)
    for s in sorted(set(hide), key=len, reverse=True):
        text = text.replace(s, "[redacted]")
    return text[:300]


def attempt(ledger: Ledger, platform: str, connector: Connector, post: Post, day: str, by: str,
            clock: Callable[[], dt.datetime] = now_utc, hide: list[str] | None = None) -> dict[str, Any]:
    """One try of one item on one platform, with the ledger written around the irreversible call."""
    ledger.append(post.item, platform, "attempt", clock(), day, by=by)
    flags: dict[str, Any] = {"committed": False, "refused": None}

    def commit(fn: Callable[[], Any]) -> Any:
        if flags["committed"]:
            raise RuntimeError("a connector may make only one publish call per attempt")
        if post.verify:
            post.verify()
        ledger.append(post.item, platform, "commit", clock(), day)
        flags["committed"] = True
        try:
            return fn()
        except HttpError as e:
            if 400 <= e.status < 500 and e.status not in (408, 409):
                flags["refused"] = e  # a clear refusal: the platform did not take the post
            raise

    row: dict[str, Any] = {"item": post.item, "platform": platform}
    try:
        if post.verify:
            post.verify()
        result = connector.publish(post, commit)
    except Exception as e:
        http = getattr(connector, "http", None)
        hidden = [*(hide or []), *getattr(http, "secrets", [])]
        err = _error(e, hidden)
        if isinstance(http, Http):
            err = http.redact(err)
        definite = not flags["committed"] or flags["refused"] is e
        ledger.append(post.item, platform, "failed" if definite else "uncertain", clock(), day, error=err)
        return {**row, "state": "failed" if definite else "held", "error": err}
    if not flags["committed"]:
        err = "the connector returned without making the publish call"
        ledger.append(post.item, platform, "failed", clock(), day, error=err)
        return {**row, "state": "failed", "error": err}
    ledger.append(post.item, platform, result.status, clock(), day, post_id=result.post_id, url=result.url,
                  note=result.note)
    return {**row, "state": "done", "status": result.status, "url": result.url, "note": result.note}


def make_post(cfg: Any, session: Any, n: int, live: bool) -> Post:
    texts = dict(session.captions[str(n)])
    edit = session.edits[str(n)]
    video = session.dir / edit["artifacts"]["render"]
    name = "%s-%d.mp4" % (session.id, n)
    base = str(cfg["publish"]["video_base_url"]).strip()
    url = base.rstrip("/") + "/" + name if base else ("" if live else "https://media.example.com/" + name)
    valid = session_mod.render_current(session, n)
    expected = session.review[str(n)].get("fingerprint")

    def verify() -> None:
        if (session_mod.review_status(session, n) != "approved"
                or session_mod.fingerprint(session, n) != expected or not session_mod.render_current(session, n)):
            raise RuntimeError("approved media changed; edit, render, review and approve again")

    return Post(session.key(n), texts["title"], texts, video if valid else None, url, placeholder=not live,
                video_sha256=str((edit.get("render_proof") or {}).get("sha256") or ""),
                verify=verify if live else None)


def _http(live: bool, transport: Transport | None, simulate: dict[str, str] | None) -> Http:
    if transport is not None:
        return Http(transport)
    return Http(None if live else Simulator(simulate), timeout=120)


def _line(row: dict[str, Any]) -> str:
    p = row["platform"]
    if row["state"] == "done":
        detail = row.get("url") or ""
        if row.get("note"):
            detail = (detail + " (%s)" % row["note"]).strip()
        return "  %-10s %-9s %s" % (p, row["status"], detail)
    if row["state"] == "held":
        return "  %-10s %-9s %s. Check %s, then: content-engine reconcile %s %s --posted [--url URL] | " \
               "--not-posted" % (p, "HELD", row["error"], LABEL[p], row["item"], p)
    return "  %-10s %-9s %s" % (p, "failed", row["error"])


def _todo(rows: list[dict[str, Any]], texts: dict[str, str]) -> list[str]:
    out = []
    for r in rows:
        if r["state"] == "done" and r.get("status") == "drafted":
            out.append("Finish the TikTok draft in the app and paste this caption:\n%s" % texts["full"])
        elif r["state"] == "done" and r.get("status") == "uploaded":
            out.append("Publish the YouTube upload in YouTube Studio: %s" % r.get("url", ""))
        elif r["state"] == "held":
            out.append("Check %s for %s by hand, then run content-engine reconcile." % (LABEL[r["platform"]],
                                                                                        r["item"]))
        elif r["state"] == "failed":
            out.append("%s failed for %s: `content-engine repair` retries it today." % (LABEL[r["platform"]],
                                                                                        r["item"]))
    return out


def finished_items(states: dict[tuple[str, str], PlatformState], enabled: list[str]) -> set[str]:
    items = {it for it, _ in states}
    return {it for it in items if all(states.get((it, p), PlatformState()).state == "done" for p in enabled)}


def _carousel(cfg: Any, session: Any, n: int) -> str:
    deck = carousel.build(session.captions[str(n)], cfg)
    write_json(cfg.out_dir / session.id / "carousels" / ("%d.json" % n), {"item": session.key(n), **deck})
    return "Carousel text: out/%s/carousels/%d.json (%d slides)" % (session.id, n, len(deck["slides"]))


def daily(cfg: Any, sessions: list[Any], now: dt.datetime, live: bool, *, transport: Transport | None = None,
          simulate: dict[str, str] | None = None, sleep: Any = None, clock: Callable[[], dt.datetime] = now_utc,
          out: Out = print) -> int:
    if paused(cfg):
        out("Publishing is paused (state/PAUSED). Remove the file to resume.")
        return 0
    head = "Publish (%s) · %s" % ("live" if live else "dry run: simulated platforms, separate ledger",
                                  cfg.today(now))
    try:
        with Lock(state_dir(cfg, live) / "publish.lock", float(cfg["publish"]["lock_stale_minutes"])):
            return _daily(cfg, sessions, now, live, transport, simulate, sleep, clock, out, head)
    except LockedError as e:
        out("%s\n  not run: %s" % (head, e))
        return 1


def _daily(cfg: Any, sessions: list[Any], now: dt.datetime, live: bool, transport: Transport | None,
           simulate: dict[str, str] | None, sleep: Any, clock: Callable[[], dt.datetime], out: Out, head: str) -> int:
    ledger = ledger_for(cfg, live)
    states = ledger.states()
    queue, changed = session_mod.queue(sessions)
    by_key = {s.key(n): (s, n) for s, n in queue}
    waiting = sum(1 for s in sessions for n in s.edits if session_mod.review_status(s, n) == "pending")
    enabled = [p for p in ORDER if p in cfg["publish"]["platforms"]]
    day = cfg.today(now)
    today = plan_today(list(by_key), enabled, states, day, waiting)
    lines = [head]
    rows: list[dict[str, Any]] = []
    code = 0
    if today.kind in ("new", "resume") and today.item:
        s, n = by_key[today.item]
        lines.append("Today: %s \"%s\" (%s)" % (today.item, s.card(n)["hook"], today.kind))
        blocked = [p for p in today.platforms if live and missing_env(p)]
        post = make_post(cfg, s, n, live)
        if blocked:
            lines.append("  not run: set %s, or remove %s from [publish] platforms"
                         % (", ".join(v for p in blocked for v in missing_env(p)), ", ".join(blocked)))
            code = 1
        elif live and post.video is None:
            lines.append("  not run: no render verified for the current edit at out/%s/renders/%d.mp4 "
                         "(run `content-engine edit --render`, review and approve again)"
                         % (s.id, n))
            code = 1
        else:
            http = _http(live, transport, simulate)
            for p in today.platforms:
                rows.append(attempt(ledger, p, build(p, http, cfg, live, sleep), post, day, "daily", clock,
                                    _secrets(p, live)))
                lines.append(_line(rows[-1]))
            if any(r["state"] == "done" for r in rows):
                lines.append(_carousel(cfg, s, n))
            todo = _todo(rows, s.captions[str(n)])
            if todo:
                lines.append("To do:")
                lines += ["  %d. %s" % (i, t.replace("\n", "\n       ")) for i, t in enumerate(todo, 1)]
            code = 0 if any(r["state"] == "done" for r in rows) else 1
    elif today.kind == "done":
        lines.append("Today's item %s already ran. Nothing new until tomorrow (one reel a day)." % today.item)
    elif today.kind == "waiting":
        lines.append("Nothing approved to post. %d edited item(s) wait for review." % waiting)
    elif today.kind == "empty":
        lines.append("Nothing left to post. Record the next session.")
    else:
        lines.append("No platforms enabled in [publish] platforms.")
    if changed:
        lines.append("Changed since approval, not posted until approved again: %s" % ", ".join(changed))
    left = today.left if today.kind in ("new", "resume", "done") else 0
    if today.kind in ("new", "resume", "done"):
        low = left <= int(cfg["publish"]["low_queue"])
        lines.append("Queue: %d approved reel(s) after today.%s" % (left, " Record the next session." if low else ""))
    out("\n".join(lines))
    return code


def repair(cfg: Any, sessions: list[Any], now: dt.datetime, live: bool, *, item: str | None = None,
           platform: str | None = None, transport: Transport | None = None, simulate: dict[str, str] | None = None,
           sleep: Any = None, clock: Callable[[], dt.datetime] = now_utc, out: Out = print) -> int:
    if paused(cfg):
        out("Publishing is paused (state/PAUSED). Remove the file to resume.")
        return 0
    head = "Repair (%s) · %s" % ("live" if live else "dry run", cfg.today(now))
    try:
        with Lock(state_dir(cfg, live) / "publish.lock", float(cfg["publish"]["lock_stale_minutes"])):
            ledger = ledger_for(cfg, live)
            enabled = [p for p in ORDER if p in cfg["publish"]["platforms"]]
            day = cfg.today(now)
            cands = repair_candidates(ledger.states(), enabled, day, int(cfg["publish"]["max_attempts"]), item,
                                      platform)
            approved = {s.key(n): (s, n) for s, n in session_mod.queue(sessions)[0]}
            lines, code = [head], 0
            http = _http(live, transport, simulate) if cands["retry"] else None
            for it, p in cands["retry"]:
                if it not in approved:
                    lines.append("  %-10s skipped   %s is no longer approved as it was" % (p, it))
                    continue
                if live and missing_env(p):
                    lines.append("  %-10s skipped   set %s" % (p, ", ".join(missing_env(p))))
                    continue
                s, n = approved[it]
                post = make_post(cfg, s, n, live)
                if live and post.video is None:
                    lines.append("  %-10s skipped   no render verified for the current edit at out/%s/renders/%d.mp4"
                                 % (p, s.id, n))
                    continue
                assert http is not None
                row = attempt(ledger, p, build(p, http, cfg, live, sleep), post, day, "repair", clock,
                              _secrets(p, live))
                lines.append(_line(row) + ("" if row["state"] != "done" else " (repaired %s)" % it))
                if row["state"] == "done":
                    lines.append(_carousel(cfg, s, n))
                code = code or (0 if row["state"] == "done" else 1)
            for it, p in cands["held"]:
                lines.append("  %-10s HELD      %s: not retried. Check %s, then: content-engine reconcile %s %s "
                             "--posted [--url URL] | --not-posted" % (p, it, LABEL[p], it, p))
            for it, p in cands["exhausted"]:
                lines.append("  %-10s gave up   %s: %s attempts used ([publish] max_attempts)"
                             % (p, it, cfg["publish"]["max_attempts"]))
            if len(lines) == 1:
                lines.append("  nothing to repair")
            out("\n".join(lines))
            return code
    except LockedError as e:
        out("%s\n  not run: %s" % (head, e))
        return 1


def reconcile(cfg: Any, item: str, platform: str, posted: bool, now: dt.datetime, live: bool = True,
              url: str = "") -> str:
    """Record a person's check of a held platform: posted (done) or not posted (failed, so repair may fill it)."""
    with Lock(state_dir(cfg, live) / "publish.lock", float(cfg["publish"]["lock_stale_minutes"])):
        ledger = ledger_for(cfg, live)
        st = ledger.states().get((item, platform))
        if st is None or st.state != "held":
            raise ValueError("%s on %s is %s, not held; only held platforms are reconciled"
                             % (item, platform, st.state if st else "not in the ledger"))
        ledger.append(item, platform, "reconciled", now, cfg.today(now), outcome="posted" if posted else "not_posted",
                      url=url)
    return "%s on %s marked %s." % (item, platform, "posted" if posted else
                                    "not posted; `content-engine repair --item %s` can post it" % item)
