"""Each connector against a fake transport: the exact calls, which one is the commit, and no secrets in errors."""

import json
import urllib.parse

import pytest

from content_engine.http import Http, HttpError, TransportError
from content_engine.publish.platforms import build, missing_env
from content_engine.publish.platforms.base import Post, PublishError
from content_engine.publish.platforms.facebook import Facebook
from content_engine.publish.platforms.instagram import Instagram
from content_engine.publish.platforms.oauth1 import header
from content_engine.publish.platforms.threads import Threads
from content_engine.publish.platforms.tiktok import TikTok, chunk_plan
from content_engine.publish.platforms.x import X
from content_engine.publish.platforms.youtube import YouTube

from .conftest import FakeHttp

TOKEN = "test-token-not-real"
TEXTS = {"full": "Full caption", "short": "Short caption", "x": "X caption"}


def post(tmp_path, url="https://media.example.com/s-1.mp4"):
    video = tmp_path / "1.mp4"
    video.write_bytes(b"\x01" * 100)
    return Post("s#1", "A title", TEXTS, video, url)


class Commit:
    """Records which call was the commit."""

    def __init__(self):
        self.calls = 0

    def __call__(self, fn):
        self.calls += 1
        return fn()


def form(call):
    return dict(urllib.parse.parse_qsl(call["body"].decode()))


def test_facebook_start_upload_then_finish_as_the_commit(tmp_path):
    fake = FakeHttp({("POST", "/v23.0/42/video_reels"): lambda u, h, b: {"video_id": "77"} if b"start" in b
                     else {"success": True},
                     ("POST", "/video-upload/v23.0/77"): {"success": True}})
    commit = Commit()
    r = Facebook(Http(fake), "42", TOKEN).publish(post(tmp_path), commit)
    assert (r.status, r.post_id, r.url) == ("posted", "77", "https://www.facebook.com/reel/77")
    assert [form(c).get("upload_phase") for c in fake.calls if "video_reels" in c["url"]] == ["start", "finish"]
    assert form(fake.calls[-1])["description"] == "Full caption" and commit.calls == 1
    assert fake.calls[1]["headers"]["file_size"] == "100" and fake.calls[1]["body"] == b"\x01" * 100


def test_facebook_refuses_an_unexpected_upload_host(tmp_path):
    fake = FakeHttp({("POST", "/v23.0/42/video_reels"): {"video_id": "77", "upload_url": "https://evil.example/x"}})
    with pytest.raises(PublishError, match="upload host"):
        Facebook(Http(fake), "42", TOKEN).publish(post(tmp_path), Commit())
    assert len(fake.calls) == 1


def meta_routes(version, user, create, publish, status_field, status="FINISHED", permalink=True):
    def node(u, h, b):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query))
        if q.get("fields") == "permalink":
            return {"permalink": "https://example.com/p/1"} if permalink else (500, {"error": "down"})
        return {status_field: status}
    return {("HEAD", "media.example.com"): {}, ("POST", "%s/%s%s" % (version, user, create)): {"id": "c1"},
            ("GET", version + "/c1"): node, ("GET", version + "/m1"): node,
            ("POST", "%s/%s%s" % (version, user, publish)): {"id": "m1"}}


def test_instagram_checks_the_url_waits_for_the_container_and_publishes(tmp_path):
    fake = FakeHttp(meta_routes("/v23.0", "9", "/media", "/media_publish", "status_code"))
    commit = Commit()
    r = Instagram(Http(fake), "9", TOKEN, sleep=lambda s: None).publish(post(tmp_path), commit)
    assert (r.status, r.post_id, r.url) == ("posted", "m1", "https://example.com/p/1") and commit.calls == 1
    assert fake.calls[0]["method"] == "HEAD"
    created = form(fake.calls[1])
    assert created["media_type"] == "REELS" and created["caption"] == "Full caption"
    assert created["video_url"] == "https://media.example.com/s-1.mp4"


def test_instagram_permalink_failure_after_publishing_is_still_posted(tmp_path):
    routes = meta_routes("/v23.0", "9", "/media", "/media_publish", "status_code", permalink=False)
    r = Instagram(Http(FakeHttp(routes)), "9", TOKEN, sleep=lambda s: None).publish(post(tmp_path), Commit())
    assert r.status == "posted" and r.url == "" and "permalink not read" in r.note


def test_instagram_processing_error_stops_before_the_commit(tmp_path):
    routes = meta_routes("/v23.0", "9", "/media", "/media_publish", "status_code", status="ERROR")
    commit = Commit()
    with pytest.raises(PublishError, match="processing failed"):
        Instagram(Http(FakeHttp(routes)), "9", TOKEN, sleep=lambda s: None).publish(post(tmp_path), commit)
    assert commit.calls == 0
    with pytest.raises(PublishError, match="video_base_url"):
        Instagram(Http(FakeHttp({})), "9", TOKEN).publish(post(tmp_path, url=""), commit)


def test_threads_uses_the_short_text(tmp_path):
    routes = meta_routes("/v1.0", "5", "/threads", "/threads_publish", "status")
    fake = FakeHttp(routes)
    r = Threads(Http(fake), "5", TOKEN, sleep=lambda s: None).publish(post(tmp_path), Commit())
    assert r.post_id == "m1" and form(fake.calls[1])["text"] == "Short caption"
    assert form(fake.calls[1])["media_type"] == "VIDEO"


def test_x_uploads_in_segments_then_posts_the_x_text_signed(tmp_path):
    fake = FakeHttp({
        ("POST", "/2/media/upload/initialize"): {"data": {"id": "m9"}},
        ("POST", "/2/media/upload/m9/append"): (204, b""),
        ("POST", "/2/media/upload/m9/finalize"): {"data": {"processing_info": {"state": "pending",
                                                                               "check_after_secs": 1}}},
        ("GET", "/2/media/upload"): {"data": {"processing_info": {"state": "succeeded"}}},
        ("POST", "/2/tweets"): {"data": {"id": "t1"}},
    })
    commit = Commit()
    r = X(Http(fake), "ck", "cs", "at", "as", sleep=lambda s: None).publish(post(tmp_path), commit)
    assert (r.post_id, r.url) == ("t1", "https://x.com/i/status/t1") and commit.calls == 1
    assert json.loads(fake.calls[-1]["body"]) == {"text": "X caption", "media": {"media_ids": ["m9"]}}
    assert all(c["headers"]["Authorization"].startswith("OAuth ") for c in fake.calls)
    assert b'name="segment_index"' in fake.calls[1]["body"]
    assert "command=STATUS" in fake.calls[3]["url"]


def test_oauth1_signature_with_synthetic_credentials():
    h = header("GET", "https://photos.example.com/photos", {"file": "photo.jpg", "size": "original"},
               "test-consumer", "test-consumer-secret", "test-access", "test-access-secret",
               nonce="test-nonce", timestamp="1700000000")
    found = dict(part.split("=", 1) for part in h[len("OAuth "):].split(", "))
    expected = "hNMDw6C/9vpMr4vByOu5gRl/GGM="
    assert urllib.parse.unquote(found["oauth_signature"].strip('"')) == expected


def test_youtube_refreshes_uploads_resumably_and_reports_private_as_uploaded(tmp_path):
    loc = "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=u1"
    fake = FakeHttp({("POST", "/token"): {"access_token": "a1"},
                     ("POST", "/upload/youtube/v3/videos"): (200, {}, {"Location": loc}),
                     ("PUT", "/upload/youtube/v3/videos"): {"id": "v1", "status": {"privacyStatus": "private"}}})
    commit = Commit()
    r = YouTube(Http(fake), "cid", "secret", "refresh", {"privacy": "private"}).publish(post(tmp_path), commit)
    assert (r.status, r.url) == ("uploaded", "https://www.youtube.com/shorts/v1") and commit.calls == 1
    meta = json.loads(fake.calls[1]["body"])
    assert meta["snippet"]["title"] == "A title" and meta["status"]["containsSyntheticMedia"] is True
    assert meta["status"]["privacyStatus"] == "private" and fake.calls[2]["headers"]["Authorization"] == "Bearer a1"


def test_youtube_without_an_upload_location_fails_before_the_commit(tmp_path):
    fake = FakeHttp({("POST", "/token"): {"access_token": "a1"}, ("POST", "/upload/youtube/v3/videos"): {}})
    commit = Commit()
    with pytest.raises(PublishError, match="upload location"):
        YouTube(Http(fake), "cid", "secret", "refresh").publish(post(tmp_path), commit)
    assert commit.calls == 0


def tiktok_routes(refresh="r1"):
    return {("POST", "/v2/oauth/token/"): {"access_token": "a1", "refresh_token": refresh},
            ("POST", "/v2/post/publish/inbox/video/init/"): {"data": {"publish_id": "p1", "upload_url":
                                                                      "https://open-upload.tiktokapis.com/v/?u=1"},
                                                             "error": {"code": "ok"}},
            ("PUT", "/v/"): (201, b""),
            ("POST", "/v2/post/publish/status/fetch/"): {"data": {"status": "SEND_TO_USER_INBOX"},
                                                         "error": {"code": "ok"}}}


def test_tiktok_uploads_a_draft_with_the_last_chunk_as_the_commit(tmp_path):
    fake = FakeHttp(tiktok_routes())
    commit = Commit()
    r = TikTok(Http(fake), "ck", "cs", "r1").publish(post(tmp_path), commit)
    assert (r.status, r.post_id) == ("drafted", "p1") and commit.calls == 1
    init = json.loads(fake.calls[1]["body"])["source_info"]
    assert init == {"source": "FILE_UPLOAD", "video_size": 100, "chunk_size": 100, "total_chunk_count": 1}
    assert fake.calls[2]["headers"]["Content-Range"] == "bytes 0-99/100"


def test_tiktok_keeps_a_rotated_refresh_token_until_the_env_one_changes(tmp_path):
    token_file = tmp_path / "state" / "tiktok-token.json"
    TikTok(Http(FakeHttp(tiktok_routes(refresh="r2"))), "ck", "cs", "r1", token_file).publish(post(tmp_path),
                                                                                               Commit())
    assert json.loads(token_file.read_text())["refresh_token"] == "r2"
    fake = FakeHttp(tiktok_routes(refresh="r2"))
    TikTok(Http(fake), "ck", "cs", "r1", token_file).publish(post(tmp_path), Commit())
    assert form(fake.calls[0])["refresh_token"] == "r2"
    fake = FakeHttp(tiktok_routes(refresh="r9"))
    TikTok(Http(fake), "ck", "cs", "r9", token_file).publish(post(tmp_path), Commit())
    assert form(fake.calls[0])["refresh_token"] == "r9"


def test_tiktok_chunk_plan():
    assert chunk_plan(5_000_000) == (5_000_000, 1)
    assert chunk_plan(64_000_000) == (64_000_000, 1)
    assert chunk_plan(125_000_000) == (10_000_000, 12)


def test_tiktok_error_codes_stop_before_upload(tmp_path):
    routes = tiktok_routes()
    routes[("POST", "/v2/post/publish/inbox/video/init/")] = {"error": {"code": "spam_risk_too_many_pending_share"}}
    commit = Commit()
    with pytest.raises(PublishError, match="spam_risk"):
        TikTok(Http(FakeHttp(routes)), "ck", "cs", "r1").publish(post(tmp_path), commit)
    assert commit.calls == 0


def test_errors_carry_no_token_or_query_string():
    fake = FakeHttp({("POST", "/v23.0/42/video_reels"): (400, {"error": {"message": "Invalid parameter"}})})
    with pytest.raises(HttpError) as e:
        Http(fake).json("POST", "https://graph.facebook.com/v23.0/42/video_reels?access_token=" + TOKEN)
    assert TOKEN not in str(e.value) and "Invalid parameter" in str(e.value) and e.value.status == 400

    def boom(*a):
        raise TransportError("POST https://graph.facebook.com/v23.0/42/video_reels: no response (timed out)")
    with pytest.raises(TransportError):
        Http(boom).json("POST", "https://graph.facebook.com/v23.0/42/video_reels")


def test_build_needs_env_in_live_mode_and_never_reads_it_in_dry_runs(make_cfg, monkeypatch):
    monkeypatch.setenv("FACEBOOK_PAGE_ID", "1234567")
    monkeypatch.delenv("FACEBOOK_PAGE_TOKEN", raising=False)
    c = make_cfg()
    assert missing_env("facebook") == ["FACEBOOK_PAGE_TOKEN"]
    with pytest.raises(PublishError, match="FACEBOOK_PAGE_TOKEN"):
        build("facebook", Http(FakeHttp()), c, live=True)
    dry = build("facebook", Http(FakeHttp()), c, live=False)
    assert dry.page == "dry-run-facebook_page_id"
