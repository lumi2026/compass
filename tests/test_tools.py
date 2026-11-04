"""Service tool modules: shapes, soft fails, registry. No network."""

import tools.graph as G
import tools.kalshi as K
import tools.news as N
from tools import as_structured


def _boom(*a, **k):
    raise RuntimeError("down")


def test_kalshi_discover_shape_and_soft_fail(monkeypatch):
    monkeypatch.setattr(
        K, "_get",
        lambda b, p, params=None, timeout=30: {"events": [
            {"event_ticker": "E1", "title": "T1"}, {"nope": 1}]})
    reg = K.kalshi_tools("http://x")
    assert reg["kalshi_discover_events"]["func"]() == {
        "ok": True, "events": [{"event_ticker": "E1", "title": "T1"}]}
    monkeypatch.setattr(K, "_get", _boom)
    out = reg["kalshi_discover_events"]["func"]()
    assert out["ok"] is False and "error" in out


def test_kalshi_sweep_posts_path(monkeypatch):
    seen = []

    def post(base, path, payload, timeout=30):
        seen.append((path, payload))
        return {"published": True, "tickers": 2, "proposals": 4,
                "revision": 8}
    monkeypatch.setattr(K, "_post", post)
    out = K.kalshi_tools("http://x")["kalshi_trigger_sweep"]["func"]()
    assert out == {"ok": True, "published": True, "tickers": 2,
                   "proposals": 4, "revision": 8, "reason": ""}
    assert seen == [("/publish/sweep", {})]


def test_kalshi_sweep_reports_disabled_publish(monkeypatch):
    monkeypatch.setattr(
        K, "_post",
        lambda b, p, payload, timeout=30: {
            "published": False, "reason": "no graph_url"})
    out = K.kalshi_tools("http://x")["kalshi_trigger_sweep"]["func"]()
    assert out == {"ok": False, "published": False, "tickers": 0,
                   "proposals": 0, "revision": None,
                   "reason": "no graph_url"}


def _graph_elements():
    return [
        {"data": {"id": "kalshi:A", "kind": "Event", "label": "A"}},
        {"data": {"id": "article:1", "kind": "Article", "label": "s"}},
        {"data": {"id": "hypothesis:h1", "kind": "Hypothesis",
                  "label": "A x B"}},
        {"data": {"id": "hypothesis:h2", "kind": "Hypothesis",
                  "label": "lone"}},
        {"data": {"id": "audit:a1", "kind": "Audit", "label": "f",
                  "props": {"target": "hypothesis:h1"}}},
        {"data": {"source": "hypothesis:h1", "target": "kalshi:A",
                  "label": "supports"}},
        {"data": {"source": "hypothesis:h1", "target": "article:1",
                  "label": "supports"}},
        {"data": {"source": "audit:a1", "target": "hypothesis:h1",
                  "label": "challenges"}},
    ]


def test_graph_list_hypotheses(monkeypatch):
    monkeypatch.setattr(
        G, "_get",
        lambda b, p, timeout=30: {"elements": _graph_elements()})
    out = G.graph_tools("http://x")["graph_list_hypotheses"]["func"]()
    assert out["ok"] is True
    (h1, h2) = out["hypotheses"]
    assert (h1["id"], h1["challenged"], h1["articles"]) == \
        ("hypothesis:h1", True, 1)
    assert (h2["id"], h2["events"], h2["challenged"]) == \
        ("hypothesis:h2", [], False)


def test_graph_post_proposals_validates_and_counts(monkeypatch):
    seen = []

    def post(base, path, payload, timeout=30):
        seen.append((path, payload))
        return {"revision": 4, "verdicts": [{"committed": True},
                                            {"committed": False}]}
    monkeypatch.setattr(G, "_post", post)
    func = G.graph_tools("http://x")["graph_post_proposals"]["func"]
    assert func("nope")["ok"] is False
    assert func('{"a": 1}')["ok"] is False  # not a list; item shape is
    out = func('[{"a": 1}]')
    assert out == {"ok": True, "revision": 4, "committed": 1,
                   "rejected": 1}
    assert seen[0][0] == "/proposals"


def test_news_run_cycle_delegates_to_run_cycle(monkeypatch):
    import scripts.news_loop as nl

    seen = {}

    def fake_run_cycle(graph_url, kalshi_url, cycle=0, event_slice=(),
                       max_pairs=10, mode_pin=None, model="auto",
                       event_limit=30, history_snaps=1, **kw):
        seen.update(graph_url=graph_url, kalshi_url=kalshi_url,
                    event_slice=event_slice, mode_pin=mode_pin, model=model,
                    event_limit=event_limit, history_snaps=history_snaps,
                    max_pairs=max_pairs)
        return {"ok": True, "mode": "co-mention", "pairs": 0}

    monkeypatch.setattr(nl, "run_cycle", fake_run_cycle)
    func = N.news_tools("http://g", "http://k",
                        "m")["news_run_cycle"]["func"]
    out = func(event_limit=5)
    assert out["ok"] is True
    assert seen == {"graph_url": "http://g", "kalshi_url": "http://k",
                    "event_slice": (), "mode_pin": None, "model": "m",
                    "event_limit": 5, "history_snaps": 1, "max_pairs": 10}
    assert func(mode_pin="nope")["ok"] is False


def test_news_run_batch_fans_out_in_order(monkeypatch):
    import json

    import scripts.news_loop as nl

    calls = []

    def fake_run_cycle(graph_url, kalshi_url, cycle=0, event_slice=(),
                       max_pairs=10, mode_pin=None, model="auto",
                       event_limit=30, history_snaps=1, **kw):
        calls.append((cycle, event_slice, mode_pin))
        return {"ok": True, "cycle": cycle,
                "events": list(event_slice)}

    monkeypatch.setattr(nl, "run_cycle", fake_run_cycle)
    reg = N.news_tools("http://g", "http://k", "m")
    batch = reg["news_run_batch"]["func"]
    jobs = json.dumps([
        {"event_filter": "E1,E2"},
        {"event_filter": ["E3"], "mode_pin": "burst"},
    ])
    out = batch(jobs, max_workers=2)
    assert out["ok"] is True
    assert [r["events"] for r in out["results"]] == [["E1", "E2"], ["E3"]]
    assert [c[0] for c in calls] == [0, 1]
    assert batch("nope")["ok"] is False
    assert batch("{}")["ok"] is False
    out = batch(cycles=[{"event_filter": "E9"}])
    assert out["ok"] is True
    assert [r["events"] for r in out["results"]] == [["E9"]]
    out = batch([{"event_filter": "E7"}])
    assert out["ok"] is True
    assert [r["events"] for r in out["results"]] == [["E7"]]


def test_as_structured_keeps_names():
    from langchain_core.tools import StructuredTool

    reg = K.kalshi_tools("http://x")
    reg.update(G.graph_tools("http://y"))
    reg.update(N.news_tools("http://g", "http://k"))
    tools = as_structured(reg)
    assert len(tools) == 13
    assert all(isinstance(t, StructuredTool) for t in tools)
    by_name = {t.name: t.description for t in tools}
    assert by_name["kalshi_discover_events"].startswith("List open")
    assert "gated" in by_name["graph_post_proposals"]
    assert "Execute the returned" in by_name["news_propose_step"]
