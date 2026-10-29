"""Deterministic tests for the news agent tools. No network: fixtures only."""

import gzip
import json

from sources import ngrams as ng
from squad import news_agent as na
from squad import tools as t


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
    m = ng.ngram_mentions(["spacex", "mars"], str(gz))
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
    m = ng.ngram_mentions(["rate cut", "kevin warsh"], str(gz))
    assert m["rate cut"]["urls"] == ["http://e.test/rate"]  # not the tax cut
    assert m["kevin warsh"]["urls"] == ["http://e.test/warsh"]  # type 2 out
    assert ng.match_record({"ngram": "x", "type": 1}, ["a", "b", "c"]) is False
    assert ng.match_record({"ngram": "b", "type": 1, "pre": "a",
                            "post": "c"}, ["a", "b", "c"]) is True


def test_summarize_picks_term_sentences():
    text = ("The sky is blue today. SpaceX launched another Starlink batch "
            "from Florida. Markets were calm and quiet overall. "
            "Analysts say SpaceX dominates launch cadence.")
    s = t.summarize(text, ["spacex"], max_sentences=2)
    assert "SpaceX" in s and "sky is blue" not in s


def test_snapshot_candidates_format():
    cands = ng.snapshot_candidates(1793000000.0)
    assert len(cands) > 3
    assert all(len(c) == 14 and c.endswith(("0100", "1600", "3100", "4600"))
               for c in cands)


def test_snapshot_candidates_skip_unlanded_mark():
    import datetime

    now = datetime.datetime(2026, 10, 28, 0, 5,
                            tzinfo=datetime.timezone.utc).timestamp()
    assert ng.snapshot_candidates(now, delay_min=0)[0] == "20261028000100"
    assert ng.snapshot_candidates(now)[0] == "20261027234600"  # 10m back


def test_agent_proposal_shape(monkeypatch, tmp_path):
    gz = tmp_path / "s.json.gz"
    _fixture_gz(gz)
    monkeypatch.setattr(na, "kalshi_markets", lambda *a, **k: [])
    monkeypatch.setattr(na, "resolve_model", lambda spec, role=None: None)
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


def _clear_model_env(monkeypatch):
    for k in ("MODEL_PROVIDER", "COMPASS_PROVIDER", "MODEL_API_KEY",
              "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "COMPASS_MODEL",
              "MODEL_NAME", "MODEL_BASE_URL", "MODEL_FOR_NEWS_AGENT",
              "MODEL_FOR_GRAPH_AGENT"):
        monkeypatch.delenv(k, raising=False)


def test_role_model_defaults_and_overrides(monkeypatch):
    from squad.models import resolve_model

    _clear_model_env(monkeypatch)
    monkeypatch.setenv("MODEL_API_KEY", "k")
    assert resolve_model("auto",
                         role="news-agent").model_name == \
        "muse-spark-1.3-contributor"
    assert resolve_model("auto",
                         role="graph-agent").model_name == \
        "muse-spark-1.3-contributor"
    assert resolve_model("auto", role="unknown-role").model_name == \
        "muse-spark-1.3-contributor"  # env default
    monkeypatch.setenv("MODEL_FOR_NEWS_AGENT", "custom-fast")
    assert resolve_model("auto", role="news-agent").model_name == \
        "custom-fast"
    monkeypatch.delenv("MODEL_FOR_NEWS_AGENT")
    monkeypatch.setenv("COMPASS_MODEL", "global-pick")
    assert resolve_model("auto", role="news-agent").model_name == \
        "global-pick"  # global env beats role table


def test_factory_openai_preset(monkeypatch):
    from squad.models import make_chat_model

    _clear_model_env(monkeypatch)
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    m = make_chat_model()
    assert m.openai_api_base == "https://api.openai.com/v1"
    assert m.model_name == "gpt-4o-mini"


def test_factory_deepseek_generic_key_and_model_override(monkeypatch):
    from squad.models import make_chat_model

    _clear_model_env(monkeypatch)
    monkeypatch.setenv("MODEL_PROVIDER", "deepseek")
    monkeypatch.setenv("MODEL_API_KEY", "ds-test")
    monkeypatch.setenv("COMPASS_MODEL", "deepseek-reasoner")
    m = make_chat_model()
    assert m.openai_api_base == "https://api.deepseek.com/v1"
    assert m.model_name == "deepseek-reasoner"


def test_factory_meta_default_and_unknown_rejected(monkeypatch):
    import pytest

    from squad.models import make_chat_model

    _clear_model_env(monkeypatch)
    monkeypatch.setenv("MODEL_API_KEY", "meta-test")
    m = make_chat_model()
    assert m.openai_api_base == "https://api.meta.ai/v1"
    assert m.model_name == "muse-spark-1.3-contributor"
    monkeypatch.setenv("MODEL_PROVIDER", "Muse")
    with pytest.raises(ValueError):
        make_chat_model()


def test_factory_custom_needs_base_url(monkeypatch):
    import pytest

    from squad.models import make_chat_model

    _clear_model_env(monkeypatch)
    monkeypatch.setenv("MODEL_PROVIDER", "custom")
    monkeypatch.setenv("MODEL_API_KEY", "k")
    with pytest.raises(ValueError):
        make_chat_model()
    monkeypatch.setenv("MODEL_BASE_URL", "https://my-host/v1")
    m = make_chat_model()
    assert m.openai_api_base == "https://my-host/v1"


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


def test_llm_relevant_no_filters_out():
    keep, reason = t.llm_relevant("some body", ["spacex"],
                                   _FakeModel("NO - passing mention only"))
    assert keep is False and "passing mention" in reason


def test_llm_relevant_fail_open_on_unparseable():
    keep, _ = t.llm_relevant("some body", ["spacex"],
                              _FakeModel("LLM summary here."))
    assert keep is True


class _ScriptedModel:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = 0

    def invoke(self, messages):
        content = self.contents[min(self.calls, len(self.contents) - 1)]
        self.calls += 1
        return _FakeMsg(content)


def test_llm_relevant_prompt_carries_question():
    fake = _FakeModel("YES - on point")
    t.llm_relevant("some body", ["spacex"], fake,
                    question="Will SpaceX land on Mars?")
    system_text = str(fake.seen[0].content)
    assert "Will SpaceX land on Mars?" in system_text
    assert "Key subjects: spacex" in system_text
    assert "need not answer the question directly" in system_text


def test_resolve_model_specs(monkeypatch):
    from squad.models import resolve_model

    _clear_model_env(monkeypatch)
    monkeypatch.setenv("MODEL_API_KEY", "k")
    assert resolve_model(None) is None
    assert resolve_model("auto").model_name == "muse-spark-1.3-contributor"
    assert resolve_model("muse-spark-1.3").model_name == "muse-spark-1.3"
    assert resolve_model({"provider": "deepseek"}) \
        .openai_api_base == "https://api.deepseek.com/v1"
    fake = _FakeModel()
    assert resolve_model(fake) is fake


def test_task_model_overrides_agent_model(monkeypatch, tmp_path):
    gz = tmp_path / "s.json.gz"
    _fixture_gz(gz)
    monkeypatch.setattr(na, "kalshi_markets", lambda *a, **k: [])
    monkeypatch.setattr(na, "fetch_article",
                        lambda url, **k: "SpaceX news body here. " * 10)
    ag = na.NewsAgent(snapshot_gz=str(gz), max_articles=1, model=None)
    (p,) = ag.run({"event_ticker": "X", "terms": ["spacex"],
                   "model": _FakeModel("LLM summary here.")})
    assert "[llm] LLM summary here." in p.detail


def test_graph_proposals_link_events_through_subject():
    from graph import (
        GraphStore, GraphUpdateAPI, Node, derive_shared_subject_links,
        proposals_to_commit,
    )
    from squad import Skeptic

    ag = na.NewsAgent(snapshot_gz=None, max_articles=1, model=None)
    tasks = [
        {"event_ticker": "KXELONMARS-99", "terms": ["spacex", "mars"]},
        {"event_ticker": "KXNATO-99", "terms": ["spacex", "nato"]},
    ]
    kept = [[{"url": "http://x.test/mars", "title": "Starship slips"}],
            [{"url": "http://x.test/nato", "title": "NATO cites SpaceX"}]]
    api = GraphUpdateAPI(GraphStore())
    for task, arts in zip(tasks, kept):
        api.commit(Node(id=f"kalshi:{task['event_ticker']}", kind="Event",
                        title=task["event_ticker"]))
        for p in Skeptic().review(ag.graph_proposals(task, arts))[0]:
            r = proposals_to_commit(api, p.agent, p.action, p.entity_id,
                                    p.detail)
            assert r.committed, r.reason
    links = derive_shared_subject_links(api.store, "t")
    assert ("kalshi:KXELONMARS-99", "kalshi:KXNATO-99") in {
        (e.src, e.dst) for e in links}


def test_select_snapshots_strides_and_keeps_newest():
    from sources.ngrams import select_snapshots

    cands = [f"20261028{h:02d}4600" for h in range(6)]  # 00..05
    assert select_snapshots(cands, every_nth=2) == [
        "20261028004600", "20261028024600", "20261028044600",
        "20261028054600"]
    assert select_snapshots(cands, every_nth=1) == sorted(cands)
    assert select_snapshots([], every_nth=4) == []


def test_dig_dedupes_urls_across_snapshots(monkeypatch, tmp_path):
    import gzip
    import json

    gz = tmp_path / "s.json.gz"
    rows = [
        {"ngram": "zohran", "type": 1, "lang": "en", "pos": 10,
         "pre": "polls show", "post": "mamdani leads",
         "url": "http://x.test/1", "date": "20261028014600"},
        {"ngram": "zohran", "type": 1, "lang": "en", "pos": 80,
         "pre": "polls show", "post": "mamdani leads",
         "url": "http://x.test/1", "date": "20261028014600"},
    ]
    with gzip.open(str(gz), "wt") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    monkeypatch.setattr(na, "backfill_snapshots",
                        lambda *a, **k: [("20261028014600", str(gz)),
                                         ("20261028014600", str(gz))])
    ag = na.NewsAgent(snapshot_gz=None, max_articles=3, model=None)
    kept = ag.dig({"event_ticker": "X", "terms": ["zohran mamdani"]},
                  max_urls=5)
    assert [k["url"] for k in kept] == ["http://x.test/1"]
    assert kept[0]["snapshot"] == "20261028014600"
    assert kept[0]["subjects"] == ["zohran mamdani"]


def test_agent_drops_irrelevant_article(monkeypatch, tmp_path):
    gz = tmp_path / "s.json.gz"
    _fixture_gz(gz)
    monkeypatch.setattr(na, "kalshi_markets", lambda *a, **k: [])
    monkeypatch.setattr(na, "fetch_article",
                        lambda url, **k: "SpaceX news body here. " * 10)
    ag = na.NewsAgent(snapshot_gz=str(gz), max_articles=1,
                      model=_ScriptedModel(["NO - off topic"]))
    (p,) = ag.run({"event_ticker": "X", "terms": ["spacex"]})
    assert "relevant_filter=dropped 1 of 1" in p.detail
    assert "no articles fetchable" in p.detail


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
