"""Deterministic tests for the news digest. No network: fixtures only."""

import datetime
import json

from scripts.collect_news import (
    DEFAULT_NGRAM_TERMS, _to_seendate, articles_from_mentions,
    collect_once_ngrams, summarize_topic,
)
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


def test_to_seendate_formats():
    assert _to_seendate("20261028224600") == "20261028T224600Z"
    assert _to_seendate("20261028T224600Z") == "20261028T224600Z"
    assert _to_seendate("junk", "fb") == "fb"
    assert _to_seendate(None) == ""


def test_default_ngram_terms_cover_all_topics():
    assert set(DEFAULT_NGRAM_TERMS) == {"mamdani-nyc-mayor", "fed-rates",
                                        "trump-white-house", "us-economy"}
    assert all(len(v) >= 2 for v in DEFAULT_NGRAM_TERMS.values())


def _mention_gz(path):
    import gzip

    rows = [
        {"ngram": "zohran", "type": 1, "lang": "en", "pos": 10,
         "pre": "polls show", "post": "mamdani leads race",
         "url": "http://x.test/1", "date": "20261028224600"},
        {"ngram": "zohran", "type": 1, "lang": "en", "pos": 80,
         "pre": "polls show", "post": "mamdani leads race",
         "url": "http://x.test/1", "date": "20261028224600"},  # dupe url
        {"ngram": "mamdani", "type": 1, "lang": "en", "pos": 30,
         "pre": "rival hits", "post": "in debate",
         "url": "https://y.test/2"},  # no date -> snapshot fallback
    ]
    with gzip.open(str(path), "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_articles_from_mentions_lead_first_deduped(tmp_path):
    gz = tmp_path / "m.json.gz"
    _mention_gz(gz)
    from sources.ngrams import ngram_mentions

    mentions = ngram_mentions(["zohran mamdani", "mamdani"], str(gz))
    arts = articles_from_mentions(mentions, "20261028230100")
    assert [a.url for a in arts] == ["http://x.test/1", "https://y.test/2"]
    assert arts[0].published_at == "20261028T224600Z"  # record date kept
    assert arts[1].published_at == "20261028T230100Z"  # snapshot fallback
    assert arts[1].raw["domain"] == "y.test"
    assert all(a.source == "ngrams" for a in arts)


def test_collect_once_ngrams_offline(tmp_path):
    gz = tmp_path / "ngram_20261028224600.json.gz"
    _mention_gz(gz)
    out = tmp_path / "digest.json"
    rc = collect_once_ngrams(
        {"mayor": ["zohran mamdani", "mamdani"]}, limit=5,
        out=str(out), snapshot=str(gz))
    assert rc == 0
    back = json.loads(out.read_text())
    assert back["source"] == "ngrams"
    topic = back["topics"]["mayor"]
    assert topic["ngram_hits"] >= 2 and topic["snapshot"] == "20261028224600"
    assert topic["count"] == 2 and not back["errors"]
