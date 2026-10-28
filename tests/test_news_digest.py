"""Deterministic tests for the news digest. No network: fixtures only."""

import datetime
import json

from scripts.collect_news import summarize_topic
from sources.base import NewsArticle


def _art(title, url="http://x.test/a", seendate="20261027T051500Z",
         domain="x.test"):
    return NewsArticle(source="gdelt", external_id="e", title=title, url=url,
                       published_at=seendate, raw={"domain": domain})


def test_summarize_counts_domains_keywords():
    now = datetime.datetime(2026, 10, 28, tzinfo=datetime.timezone.utc)
    arts = [_art("Trump White House repainting repairs budget", domain="fox.test"),
            _art("Trump White House ballroom plans stall", domain="fox.test"),
            _art("HSBC quarterly results climb outlook", domain="rtt.test")]
    s = summarize_topic("t", "q", arts, now=now)
    assert s["count"] == 3
    assert s["top_domains"][0] == ("fox.test", 2)
    assert "trump" in s["keywords"] and "white" in s["keywords"]
    assert s["newest_seendate"] == "20261027T051500Z"
    assert len(s["headlines"]) == 3


def test_summarize_empty_and_bad_dates():
    s = summarize_topic("t", "q", [], now=datetime.datetime(
        2026, 10, 28, tzinfo=datetime.timezone.utc))
    assert s["count"] == 0 and s["lag_minutes"] is None
    assert s["keywords"] == [] and s["top_domains"] == []
    s2 = summarize_topic("t", "q", [_art("x", seendate="not-a-date")])
    assert s2["lag_minutes"] is None


def test_digest_json_roundtrip(tmp_path):
    arts = [_art("Mamdani mayor race polls tighten")]
    s = summarize_topic("m", "q", arts)
    out = tmp_path / "digest.json"
    out.write_text(json.dumps({"topics": {"m": s}, "errors": {}}))
    back = json.loads(out.read_text())
    assert back["topics"]["m"]["count"] == 1
