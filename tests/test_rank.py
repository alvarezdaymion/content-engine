import math

import pytest

from content_engine.ideas.filing import Idea
from content_engine.ideas.rank import lift, points, rank, recency, score_idea

from .conftest import NOW, ago


def idea(iid="a", fit=3):
    return Idea(iid, "Idea " + iid, "Question %s?" % iid, ["kw"], fit=fit)


def test_recency_halves_every_half_life_and_unknown_dates_count_half():
    assert recency(ago(0), NOW, 7) == pytest.approx(1.0)
    assert recency(ago(7 * 24), NOW, 7) == pytest.approx(0.5)
    assert recency(ago(14 * 24), NOW, 7) == pytest.approx(0.25)
    assert recency(None, NOW, 7) == 0.5


def test_lift_counts_from_1x_and_caps_at_32x():
    assert lift(None) == lift(0.5) == lift(1.0) == 1.0
    assert lift(4) == pytest.approx(3.0)
    assert lift(32) == lift(500) == pytest.approx(6.0)


def test_creator_post_uses_its_multiple_or_falls_back_to_views():
    assert points({"kind": "creator", "published": ago(0), "multiple": 8}, NOW) == ("creators", pytest.approx(4.0))
    part, p = points({"kind": "creator", "published": ago(0), "views": 90_000}, NOW)
    assert part == "creators" and p == pytest.approx(0.5 * (1 + math.log10(10)))


def test_long_running_ad_in_several_versions():
    _, p = points({"kind": "ad", "published": ago(0), "days_live": 56, "versions": 4}, NOW)
    assert p == pytest.approx((1 + 2) * (1 + 0.3 * 2))
    _, fresh = points({"kind": "ad", "published": ago(0), "days_live": 3, "versions": 1}, NOW)
    assert fresh == pytest.approx(1.0)


def test_market_and_own_weights():
    assert points({"kind": "news", "published": ago(3 * 24)}, NOW) == ("market", pytest.approx(1.0))
    assert points({"kind": "thread", "published": ago(0), "engagement": 99}, NOW)[1] == pytest.approx(3.0)
    assert points({"kind": "own_post", "published": ago(0), "multiple": 2}, NOW) == ("ours", pytest.approx(4.0))
    assert points({"kind": "own_comment", "published": ago(30 * 24)}, NOW)[1] == pytest.approx(1.0)
    assert points({"kind": "question", "published": ago(60 * 24)}, NOW) == ("ours", pytest.approx(1.5))
    assert points({"kind": "unknown"}, NOW)[1] == 0


def test_several_creators_multiply_the_creators_part_up_to_2x():
    posts = [{"kind": "creator", "source": "yt", "author": "c%d" % i, "published": ago(0), "multiple": 2}
             for i in range(8)]
    one = score_idea(3, posts[:1], NOW)["parts"]["creators"]
    three = score_idea(3, posts[:3], NOW)["parts"]["creators"]
    eight = score_idea(3, posts, NOW)["parts"]["creators"]
    assert one == pytest.approx(2.0)
    assert three == pytest.approx(3 * 2.0 * 1.4)
    assert eight == pytest.approx(8 * 2.0 * 2.0)


def test_score_saturates_below_100_and_scales_with_fit():
    many = [{"kind": "question", "published": ago(0)} for _ in range(50)]
    assert 99 < score_idea(3, many, NOW)["score"] <= 100
    one = [{"kind": "question", "published": ago(0)}]
    assert score_idea(3, one, NOW)["score"] == pytest.approx(100 * (1 - math.exp(-3 / 25)), abs=0.05)
    assert score_idea(0, one, NOW)["score"] == pytest.approx(score_idea(3, one, NOW)["score"] * 0.3, abs=0.1)


def test_trending_needs_most_points_recent_and_a_hot_post_or_three_items():
    hot = [{"kind": "creator", "published": ago(5), "multiple": 4, "author": "a", "source": "yt"}]
    assert score_idea(3, hot, NOW)["trending"]
    lukewarm = [{"kind": "creator", "published": ago(5), "multiple": 1.5, "author": "a", "source": "yt"}]
    assert not score_idea(3, lukewarm, NOW)["trending"]
    three = [{"kind": "news", "published": ago(h)} for h in (1, 2, 3)]
    assert score_idea(3, three, NOW)["trending"]
    old = [{"kind": "own_post", "published": ago(20 * 24), "multiple": 30}] + three
    assert not score_idea(3, old, NOW)["trending"]


def test_rank_orders_by_score_then_id_and_skips_ideas_without_evidence():
    ideas = [idea("b"), idea("a"), idea("c")]
    items = {"a": [{"kind": "news", "published": ago(0)}], "b": [{"kind": "news", "published": ago(0)}],
             "c": []}
    ranked, cooling = rank(ideas, items, NOW)
    assert [r["id"] for r in ranked] == ["a", "b"] and [r["rank"] for r in ranked] == [1, 2]
    assert cooling == []


def test_an_idea_a_session_took_cools_down_then_counts_only_newer_evidence():
    ideas = [idea("a")]
    items = {"a": [{"kind": "question", "published": ago(30 * 24)}, {"kind": "news", "published": ago(2)}]}
    ranked, cooling = rank(ideas, items, NOW, usage={"a": ago(5 * 24)}, cooldown_days=21)
    assert ranked == [] and cooling[0]["id"] == "a"
    ranked, _ = rank(ideas, items, NOW, usage={"a": ago(25 * 24)}, cooldown_days=21)
    assert ranked[0]["counts"] == {"news": 1}
