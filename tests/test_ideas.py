import json

import pytest

from content_engine import ideas
from content_engine.ideas.evidence import EvidenceStore
from content_engine.ideas.filing import Idea, file_all, file_item, parse_ideas, unfiled_terms
from content_engine.ideas.sources import run_due
from content_engine.ideas.sources.base import Collected
from content_engine.ideas.sources.csv_file import CsvSource
from content_engine.ideas.sources.news import NewsSource
from content_engine.ideas.sources.youtube import YouTubeSource
from content_engine.textutil import scrub

from .conftest import NOW, FakeHttp, ago

TOPICS = [Idea("invoices", "Getting paid", "How do I get paid on time?", ["late invoice", "invoice"]),
          Idea("accounts", "Separate accounts", "Do I need a separate account?", ["separate account", "mixing"])]


def test_filing_prefers_the_idea_covering_more_words_and_folds_plurals():
    assert file_item("Chasing LATE invoices again", TOPICS) == "invoices"
    assert file_item("Is mixing money with a separate account bad?", TOPICS) == "accounts"
    assert file_item("Payroll software for two people", TOPICS) is None
    by_idea, unfiled = file_all([{"text": "late invoice"}, {"text": "payroll"}], TOPICS)
    assert len(by_idea["invoices"]) == 1 and by_idea["accounts"] == [] and len(unfiled) == 1


def test_unfiled_terms_skip_stopwords_and_old_items():
    items = [{"text": "Payroll software prices", "published": ago(1)},
             {"text": "Which payroll software do you use?", "published": ago(2)},
             {"text": "Payroll is old news", "published": ago(30 * 24)}]
    assert unfiled_terms(items, NOW) == [("payroll", 2), ("software", 2)]


def test_ideas_file_rules():
    with pytest.raises(ValueError, match="unique id"):
        parse_ideas({"idea": [{"id": "a", "title": "t", "question": "q", "keywords": ["k"]}] * 2})
    with pytest.raises(ValueError, match="keywords"):
        parse_ideas({"idea": [{"id": "a", "title": "t", "question": "q"}]})
    with pytest.raises(ValueError, match="fit"):
        parse_ideas({"idea": [{"id": "a", "title": "t", "question": "q", "keywords": ["k"], "fit": 5}]})


def test_scrub_removes_contact_details_and_html():
    text = scrub("Mail me at someone@example.com or call (206) 555-0142 <b>today</b> &amp; now")
    assert text == "Mail me at [email] or call [phone] today & now"


def test_youtube_source_measures_each_video_against_its_channel_normal():
    fake = FakeHttp({
        ("GET", "/youtube/v3/search"): {"items": [{"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}}]},
        ("GET", "/youtube/v3/videos"): {"items": [
            {"id": "v1", "snippet": {"title": "Late invoice fix", "channelId": "ch1", "channelTitle": "Channel A",
                                     "publishedAt": ago(5)}, "statistics": {"viewCount": "40000", "likeCount": "900",
                                                                              "commentCount": "100"}},
            {"id": "v2", "snippet": {"title": "Other", "channelId": "ch2", "publishedAt": ago(9)},
             "statistics": {"viewCount": "500"}}]},
        ("GET", "/youtube/v3/channels"): {"items": [{"id": "ch1", "statistics": {"viewCount": "1000000",
                                                                                 "videoCount": "100"}}]},
    })
    src = YouTubeSource("yt", ["late invoices"], api_key="test-key-not-real", transport=fake)
    got = src.collect(NOW)
    v1, v2 = got.items
    assert v1["kind"] == "creator" and v1["multiple"] == 4.0 and v1["engagement"] == 1000
    assert v1["url"] == "https://www.youtube.com/watch?v=v1" and v2["multiple"] is None
    search = fake.calls[0]["url"]
    assert "order=viewCount" in search and "publishedAfter=2026-02-23T15%3A00%3A00Z" in search
    assert all(c["method"] == "GET" for c in fake.calls)


def test_youtube_errors_never_include_the_key(monkeypatch):
    src = YouTubeSource("yt", ["q"], api_key="test-key-not-real", transport=FakeHttp({}))
    with pytest.raises(Exception) as e:
        src.collect(NOW)
    assert "test-key-not-real" not in str(e.value)
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="YOUTUBE_API_KEY"):
        YouTubeSource("yt", ["q"])


def test_reflected_source_credentials_are_scrubbed_before_persistence(make_cfg):
    cfg = make_cfg(sources=[{"name": "yt", "type": "youtube", "queries": ["invoice"]}])
    key = "test-source-key"
    message = "Bad key " + key + " at https://example.com/search?key=" + key
    fake = FakeHttp({("GET", "/youtube/v3/search"): (400, {"error": {"message": message}})})
    src = YouTubeSource("yt", ["invoice"], api_key=key, transport=fake)
    result = ideas.refresh(cfg, NOW, built={"yt": src})
    saved = (cfg.state_dir / "sources.json").read_text() + (cfg.out_dir / "ideas.json").read_text()
    assert key not in str(result) + saved
    assert "?key=" not in str(result) + saved


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Late payments rise for small firms</title><link>https://news.example.org/a</link><guid>a</guid>
<pubDate>Sun, 01 Mar 2026 10:00:00 GMT</pubDate><source url="https://news.example.org">Example News</source></item>
<item><title>Old story</title><link>https://news.example.org/b</link>
<pubDate>Mon, 02 Feb 2026 10:00:00 GMT</pubDate></item>
<item><title>Write to editor@example.org</title><link>https://news.example.org/c</link>
<pubDate>Mon, 02 Mar 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_news_source_keeps_recent_stories_and_scrubs_them():
    fake = FakeHttp({("GET", "/rss/search"): (200, RSS.encode())})
    got = NewsSource("news", ["late payments"], transport=fake).collect(NOW)
    assert [i["text"] for i in got.items] == ["Late payments rise for small firms", "Write to [email]"]
    assert got.items[0]["author"] == "Example News" and got.items[0]["kind"] == "news"
    assert "q=late+payments+when%3A3d" in fake.calls[0]["url"] and "ceid=US%3Aen" in fake.calls[0]["url"]


def test_csv_source_computes_own_post_multiples_from_the_median(tmp_path):
    f = tmp_path / "mine.csv"
    f.write_text("date,text,views,url\n2026-03-01,Post A,1000,https://example.com/a\n"
                 "2026-02-27,Post B,2000,https://example.com/b\n2026-02-25,Post C,4000,\n2026-02-20,,10,\n")
    got = CsvSource("mine", f, "own_post").collect(NOW)
    assert [i["multiple"] for i in got.items] == [0.5, 1.0, 2.0]
    assert got.items[2]["url"] is None and "1 skipped" in got.note
    ads = tmp_path / "ads.csv"
    ads.write_text("kind,date,text,days_live,versions\nad,2026-03-01,Get paid faster,40,3\nbogus,2026-03-01,x,1,1\n")
    got = CsvSource("ads", ads).collect(NOW)
    assert len(got.items) == 1 and got.items[0]["days_live"] == 40 and got.items[0]["versions"] == 3


def test_csv_ids_are_scoped_to_each_source_and_stable_on_refresh(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.json")
    for name, kind, text in [("posts", "own_post", "Invoice post"), ("comments", "own_comment", "Invoice question")]:
        path = tmp_path / (name + ".csv")
        path.write_text("id,date,text\n1,2026-03-02," + text + "\n")
        src = CsvSource(name, path, kind)
        assert store.merge(src.collect(NOW).items, NOW) == 1
        assert store.merge(src.collect(NOW).items, NOW) == 0
    assert len(store.items) == 2
    assert {(i["kind"], i["text"]) for i in store.items.values()} == {
        ("own_post", "Invoice post"), ("own_comment", "Invoice question")}


class Fixed:
    def __init__(self, name, items=None, error=None):
        self.name, self.items, self.error, self.every_hours = name, items or [], error, 6

    def collect(self, now):
        if self.error:
            raise RuntimeError(self.error)
        return Collected(self.items)


def test_run_due_isolates_failures_and_respects_intervals(make_cfg):
    cfg = make_cfg(sources=[{"name": "good", "type": "demo"}, {"name": "bad", "type": "demo"}])
    store = EvidenceStore(cfg.state_dir / "evidence.json")
    item = {"id": "x:1", "kind": "news", "text": "late invoice news", "published": ago(1), "views": 1}
    built = {"good": Fixed("good", [item]), "bad": Fixed("bad", error="feed down")}
    rows = run_due(cfg, store, NOW, built=built)
    assert rows[0]["ran"] and rows[0]["new"] == 1 and rows[1]["error"] == "RuntimeError: feed down"
    later = run_due(cfg, store, NOW, built=built)
    assert not later[0]["ran"] and later[1]["ran"]  # the failed source is still due
    item2 = dict(item, views=50)
    run_due(cfg, store, NOW, force=True, built={"good": Fixed("good", [item2]), "bad": Fixed("bad")})
    assert store.items["x:1"]["views"] == 50 and len(store.items) == 1


def test_refresh_writes_ideas_json_and_notes_are_questions(make_cfg):
    cfg = make_cfg()
    cfg.path("ideas").write_text('[[idea]]\nid = "invoices"\ntitle = "Getting paid"\nquestion = "How?"\n'
                                 'keywords = ["invoice"]\nfit = 3\n')
    ideas.add_note(cfg, "My client pays every invoice late. What do I say?", NOW)
    result = ideas.refresh(cfg, NOW)
    assert result["ideas"][0]["id"] == "invoices" and result["ideas"][0]["counts"] == {"question": 1}
    snap = json.loads((cfg.out_dir / "ideas.json").read_text())
    assert snap["totals"]["filed"] == 1 and snap["ideas"][0]["evidence"][0]["kind"] == "question"
    with pytest.raises(ValueError):
        ideas.add_note(cfg, "   ", NOW)


@pytest.mark.parametrize("name,kind,count", [("own-posts.csv", "own_post", 3), ("comments.csv", "own_comment", 2),
                                             ("competitor-ads.csv", "ad", 2)])
def test_the_example_csv_templates_import_cleanly(name, kind, count):
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "examples" / "csv" / name
    got = CsvSource(name, path, kind).collect(NOW)
    assert len(got.items) == count and "skipped" not in got.note
    assert all(i["kind"] == kind and i["url"].startswith("https://example.com/") for i in got.items)
