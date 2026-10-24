"""Synthetic data for `content-engine demo`: a made-up bookkeeping studio that answers small-business questions.

Every name, channel, link and number here is invented. Links use example.com and example.org.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from .store import iso

BUSINESS = "Demo Bookkeeping Studio (synthetic data)"

IDEAS: list[dict[str, Any]] = [
    {"id": "late-invoices", "title": "Getting paid on time", "fit": 3,
     "question": "How do I get clients to pay my invoices on time?",
     "context": "Hypothetical: a client is 45 days late and keeps saying the payment is coming.",
     "keywords": ["late invoice", "unpaid invoice", "late payment", "pay on time", "net 30", "invoice"],
     "points": ["Send the invoice the day the work ships", "Due date and late fee in writing",
                "Follow up on day 1, 7 and 14"]},
    {"id": "separate-account", "title": "Business money vs personal money", "fit": 3,
     "question": "Do I really need a separate bank account for my business?",
     "context": "Hypothetical: a freelance designer runs every payment through one personal checking account.",
     "keywords": ["separate account", "business account", "business bank account", "business checking",
                  "personal account", "personal and business", "mixing"],
     "points": ["One account makes the books a ten-minute job", "Pay yourself by transfer",
                "Open it before the next client pays you"]},
    {"id": "hire-bookkeeper", "title": "When to hire a bookkeeper", "fit": 3,
     "question": "When should a small business hire a bookkeeper?",
     "context": "Hypothetical: an owner spends every Sunday night on the books and is three months behind.",
     "keywords": ["hire a bookkeeper", "bookkeeper", "outsource bookkeeping", "do my own books"],
     "points": ["Count the hours you spend on it", "Behind for two months is the signal",
                "Start with monthly, not weekly"]},
    {"id": "receipts", "title": "Receipts without the shoebox", "fit": 2,
     "question": "What is the easiest way to keep track of receipts?",
     "context": "Hypothetical: a contractor keeps paper receipts in the truck and loses half of them.",
     "keywords": ["receipt", "shoebox", "expense tracking", "track expenses"],
     "points": ["Photo at the counter, every time", "One folder per month", "Match them once a week"]},
    {"id": "pay-yourself", "title": "Paying yourself", "fit": 3,
     "question": "How much should I pay myself from my business?",
     "keywords": ["pay yourself", "pay myself", "owner draw", "owner salary"],
     "points": ["Pick a fixed amount and a fixed day", "Keep three months of costs in the account first"]},
    {"id": "month-end", "title": "A 30-minute month-end routine", "fit": 2,
     "question": "What should I do at the end of every month to keep my books clean?",
     "keywords": ["month end", "reconcile", "reconciliation", "close the books"],
     "points": ["Reconcile every account", "Chase anything unpaid", "Look at one number: cash left"]},
    {"id": "cash-vs-accrual", "title": "Cash or accrual", "fit": 1,
     "question": "Should my small business use cash or accrual accounting?",
     "keywords": ["cash basis", "accrual", "cash or accrual"], "points": ["Cash is simpler to start"]},
]

# (source group, kind, text, hours ago, extra fields)
EVIDENCE: list[tuple[str, str, str, float, dict[str, Any]]] = [
    ("creators", "creator", "I stopped chasing late invoices with one email template", 6,
     {"author": "channel-a", "views": 180_000, "baseline": 30_000}),
    ("creators", "creator", "Why clients pay invoices late, and the simple fix", 30,
     {"author": "channel-b", "views": 95_000, "baseline": 25_000}),
    ("creators", "creator", "Mixing personal and business money cost me a year of cleanup", 48,
     {"author": "channel-c", "views": 220_000, "baseline": 40_000}),
    ("creators", "creator", "Do you need a business bank account as a freelancer?", 72,
     {"author": "channel-e", "views": 60_000, "baseline": 20_000}),
    ("creators", "creator", "The receipt app I actually use", 96,
     {"author": "channel-d", "views": 40_000, "baseline": 35_000}),
    ("creators", "creator", "Bookkeeper or DIY: when it makes sense to hire one", 120, {"author": "channel-f",
                                                                                         "views": 52_000}),
    ("creators", "creator", "How I pay myself from my company every month", 240,
     {"author": "channel-a", "views": 70_000, "baseline": 30_000}),
    ("creators", "thread", "Client is 60 days late on an invoice, what do I do?", 20, {"engagement": 140}),
    ("creators", "thread", "Is it fine to run my side business through my personal account?", 50,
     {"engagement": 85}),
    ("ads", "ad", "Stop chasing unpaid invoices. Automatic reminders that get you paid.", 24,
     {"author": "Competitor A", "days_live": 60, "versions": 4}),
    ("ads", "ad", "Your books, done monthly. Hire a bookkeeper for less than you think.", 24,
     {"author": "Competitor B", "days_live": 35, "versions": 2}),
    ("ads", "ad", "Snap a photo of every receipt and you are done.", 48,
     {"author": "Competitor C", "days_live": 21, "versions": 1}),
    ("news", "news", "Survey: small firms wait longer to get paid as late payments rise", 26,
     {"author": "Example Business News"}),
    ("news", "news", "Banks add free business checking for sole owners", 70, {"author": "Example Daily"}),
    ("news", "news", "New reporting thresholds for contractors start next year", 50, {"author": "Example Daily"}),
    ("news", "news", "Payroll software prices climb for small teams", 30, {"author": "Example Business News"}),
    ("own", "own_post", "3 lines that get late invoices paid", 216, {"views": 12_000, "multiple": 2.4}),
    ("own", "own_post", "My month-end checklist in 60 seconds", 288, {"views": 4_000, "multiple": 0.8}),
    ("own", "own_post", "Payroll basics for your first hire", 480, {"views": 8_000, "multiple": 1.6}),
    ("own", "own_comment", "Do you need a separate account if you are a sole owner?", 40, {}),
    ("own", "own_comment", "What app do you use for receipts?", 70, {}),
    ("own", "own_comment", "How much should I pay myself in year one?", 120, {}),
    ("own", "own_comment", "Which payroll software do you use for two people?", 22, {}),
]

# Questions from calls and messages, added with `content-engine note`.
NOTES = [
    ("A client keeps paying my invoices late. How do I bring it up without losing them?", 18),
    ("Should I hire a bookkeeper or keep doing my own books?", 90),
]

# What the creator says on camera. [pause N] is silence; "um" and cut-off words are kept as spoken.
ANSWERS = {
    "late-invoices": "Most late payments are a process problem, not a client problem. So the first thing, um, "
                     "let me say that again. The first thing is to send the invoice the same day the work ships. "
                     "[pause 1.3] Put the due date and the late fee in writing before you start. Then follow up "
                     "on day 1, 7 and 14 with the same friendly script. When it's a system, it never feels "
                     "personal, and clients pay faster.",
    "separate-account": "Yes, and it's the cheapest fix in bookkeeping. When business and personal money mix, "
                        "every statement turns into a guessing game. [pause 1.1] Open a free business account, "
                        "send every client payment there, and pay yourself by transfer. Then your books are "
                        "basically, uh, a ten-minute job instead of a weekend.",
    "hire-bookkeeper": "Count the hours first. If you spend more than three hours a week on the books, or "
                       "you're two months behind, that's the signal. [pause 1.2] A bookkeeper costs less than "
                       "your hourly rate on those hours. Start with a monthly close, not weekly. You can always "
                       "add more later.",
    "receipts": "Take the photo at the counter, every single time. Not later, at the counter. [pause 1.0] Every "
                "photo goes into one folder per month. Then once a week you match them to the bank feed. It "
                "takes five minutes and nothing gets lost in the truck.",
    "pay-yourself": "Pick a fixed amount and a fixed day. Before that, keep three months of costs in the "
                    "account. [pause 1.1] Then pay yourself the same amount on the same day, every month. If "
                    "there's extra at the end of the quarter, take a bonus.",
    "month-end": "Thirty minutes, same day every month. Reconcile every account first. Then chase anything "
                 "unpaid. [pause 1.0] Last, look at one number: how much cash is left. That's the whole routine.",
    "cash-vs-accrual": "For most small businesses starting out, cash is simpler. You record money when it "
                       "moves. [pause 1.0] Accrual shows the real picture once you send a lot of invoices.",
}

# Edit notes for the demo's first card: a false start to cut and one graphic.
OVERRIDES = {
    "late-invoices": {"cut_phrases": ["so the first thing let me say that again"],
                      "graphics": [{"type": "stat", "at": "day 1 7 and 14", "value": "1 · 7 · 14",
                                    "label": "follow-up days", "seconds": 3}]},
    "separate-account": {"graphics": [{"type": "callout", "at": "pay yourself by transfer",
                                       "text": "Pay yourself by transfer", "seconds": 2.5}]},
}

PICKUP = "Quick pickup for the end. If this helped, send me your next question."


def ideas_toml() -> str:
    out = ["# Ideas for the demo business. Evidence is filed under an idea when its text contains a keyword.", ""]
    for idea in IDEAS:
        out.append("[[idea]]")
        for key in ("id", "title", "question", "context", "keywords", "points", "fit"):
            if key in idea:
                out.append("%s = %s" % (key, json.dumps(idea[key], ensure_ascii=False)))
        out.append("")
    return "\n".join(out)


def evidence(group: str, now: dt.datetime) -> list[dict[str, Any]]:
    items = []
    for n, (g, kind, text, hours, extra) in enumerate(EVIDENCE, 1):
        if g != group:
            continue
        item = {"id": "demo:%s:%02d" % (g, n), "source": g, "kind": kind, "text": text,
                "url": "https://%s/demo/%02d" % ("news.example.org" if g == "news" else "example.com", n),
                "published": iso(now - dt.timedelta(hours=hours)), **extra}
        if item.get("views") and item.get("baseline") and "multiple" not in item:
            item["multiple"] = round(item["views"] / item["baseline"], 2)
        items.append(item)
    return items


def speak(text: str, start: float) -> tuple[list[list[Any]], float]:
    """Word timestamps for a script at a steady pace. Returns ([[word, start, end], ...], end time)."""
    words, t, tokens = [], start, text.split()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "[pause":
            t += float(tokens[i + 1].rstrip("]"))
            i += 2
            continue
        dur = 0.14 + 0.05 * len(tok.strip(".,?!"))
        words.append([tok, round(t, 2), round(t + dur, 2)])
        t += dur + 0.06
        if tok.endswith((".", "?", "!")):
            t += 0.3
        elif tok.endswith(","):
            t += 0.12
        i += 1
    return words, t


def take_words(question: str, answer: str, *, skip_question_words: int = 0,
               misheard: dict[str, str] | None = None) -> list[list[Any]]:
    """A take as a transcript: the AI voice reads the question, a beat, then the answer."""
    q = question.split()[skip_question_words:]
    if misheard:
        q = [misheard.get(w, w) for w in q]
    q_words, t = speak(" ".join(q), 0.6)
    a_words, _ = speak(answer, t + 0.9)
    return q_words + a_words
