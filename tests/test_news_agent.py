"""Deterministic tests for the news agent tools. No network: fixtures only."""

import gzip
import json

from squad import news_agent as na


def _fixture_gz(path):
    rows = [
        {"ngram": "SpaceX", "type": 1, "lang": "en", "pos": 10, "pre": "a",
         "post": "b", "url": "http://e.test/1"},
        {"ngram": "SpaceX,", "type": 1, "lang": "en", "pos": 80, "pre": "c",
         "post": "d", "url": "http://e.test/2"},
        {"ngram": "SpaceX", "type": 1, "lang": "fr", "pos": 10, "pre": "e",
         "post": "f", "url": "http://e.test/3"},
        {"ngram": "Mars", "type": 1, "lang": "en", "pos": 30, "pre": "g",
         "post": "h", "url": "http://e.test/1"},
    ]
    with gzip.open(str(path), "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_ngram_mentions_counts_and_filters(tmp_path):
    gz = tmp_path / "s.json.gz"
    _fixture_gz(gz)
    m = na.ngram_mentions(["spacex", "mars"], str(gz))
    assert m["spacex"]["count"] == 2  # punctuation variant + casefolded
    assert m["spacex"]["urls"] == ["http://e.test/1", "http://e.test/2"]
    assert m["mars"]["count"] == 1  # fr record excluded by lang


def _phrase_gz(path):
    rows = [
        {"ngram": "rate", "type": 1, "lang": "en",
         "pre": "Fed signals another", "post": "cut deepens rally",
         "url": "http://e.test/rate"},
        {"ngram": "rate", "type": 1, "lang": "en",
         "pre": "budget", "post": "hike surprises all",
         "url": "http://e.test/hike"},
        {"ngram": "Kevin", "type": 1, "lang": "en",
         "pre": "nominee", "post": "warsh testifies Thursday",
         "url": "http://e.test/warsh"},
        {"ngram": "Kevin", "type": 2, "lang": "en",
         "pre": "nominee", "post": "warsh testifies Thursday",
         "url": "http://e.test/char"},
    ]
    with gzip.open(str(path), "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_multi_gram_matching(tmp_path):
    gz = tmp_path / "p.json.gz"
    _phrase_gz(gz)
    m = na.ngram_mentions(["rate cut", "kevin warsh"], str(gz))
    assert m["rate cut"]["urls"] == ["http://e.test/rate"]  # not the tax cut
    assert m["kevin warsh"]["urls"] == ["http://e.test/warsh"]  # type 2 out
    assert na.match_record({"ngram": "x", "type": 1}, ["a", "b", "c"]) is False
    assert na.match_record({"ngram": "b", "type": 1, "pre": "a",
                            "post": "c"}, ["a", "b", "c"]) is True


def test_summarize_picks_term_sentences():
    text = ("The sky is blue today. SpaceX launched another Starlink batch "
            "from Florida. Markets were calm and quiet overall. "
            "Analysts say SpaceX dominates launch cadence.")
    s = na.summarize(text, ["spacex"], max_sentences=2)
    assert "SpaceX" in s and "sky is blue" not in s


def test_snapshot_candidates_format():
    cands = na.snapshot_candidates(1793000000.0)
    assert len(cands) > 3
    assert all(len(c) == 14 and c.endswith(("0100", "1600", "3100", "4600"))
               for c in cands)


def test_agent_proposal_shape(monkeypatch, tmp_path):
    gz = tmp_path / "s.json.gz"
    _fixture_gz(gz)
    monkeypatch.setattr(na, "kalshi_markets", lambda *a, **k: [])
    monkeypatch.setattr(na, "make_chat_model", lambda: None)
    monkeypatch.setattr(na, "fetch_article",
                        lambda url, **k: "SpaceX news body here. " * 10)
    ag = na.NewsAgent(snapshot_gz=str(gz), max_articles=1)
    (p,) = ag.run({"event_ticker": "X", "terms": ["spacex"]})
    assert p.action == "news_summary" and p.entity_id == "kalshi:X"
    assert "ngram_hits_15min=2" in p.detail and p.confidence >= 0.3
    assert "engine=extractive" in p.detail


def test_factory_none_without_key(monkeypatch):
    from squad.models import make_chat_model

    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert make_chat_model() is None


class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeModel:
    def __init__(self, content="LLM summary here.", fail=False):
        self.content, self.fail = content, fail
        self.seen = []

    def invoke(self, messages):
        self.seen = messages
        if self.fail:
            raise RuntimeError("model down")
        return _FakeMsg(self.content)


def test_agent_uses_llm_when_available(monkeypatch, tmp_path):
    gz = tmp_path / "s.json.gz"
    _fixture_gz(gz)
    monkeypatch.setattr(na, "kalshi_markets", lambda *a, **k: [])
    monkeypatch.setattr(na, "fetch_article",
                        lambda url, **k: "SpaceX news body here. " * 10)
    ag = na.NewsAgent(snapshot_gz=str(gz), max_articles=1,
                      model=_FakeModel())
    (p,) = ag.run({"event_ticker": "X", "terms": ["spacex"]})
    assert "[llm] LLM summary here." in p.detail
    assert "engine=llm" in p.detail


def test_agent_falls_back_when_llm_fails(monkeypatch, tmp_path):
    gz = tmp_path / "s.json.gz"
    _fixture_gz(gz)
    monkeypatch.setattr(na, "kalshi_markets", lambda *a, **k: [])
    monkeypatch.setattr(na, "fetch_article",
                        lambda url, **k: "SpaceX news body here. " * 10)
    monkeypatch.setattr(na, "fetch_article",
                        lambda url, **k: "SpaceX launched another Starlink "
                        "batch from Florida this morning. " * 6)
    ag = na.NewsAgent(snapshot_gz=str(gz), max_articles=1,
                      model=_FakeModel(fail=True))
    (p,) = ag.run({"event_ticker": "X", "terms": ["spacex"]})
    assert "[extractive]" in p.detail and "engine=extractive" in p.detail
