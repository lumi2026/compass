"""Service tool modules: shapes, soft fails, registry. No network."""

import tools.graph as G
import tools.kalshi as K
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
        return {"swept": 3, "revision": 8}
    monkeypatch.setattr(K, "_post", post)
    out = K.kalshi_tools("http://x")["kalshi_trigger_sweep"]["func"]()
    assert out == {"ok": True, "swept": 3, "revision": 8}
    assert seen == [("/publish/sweep", {})]


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


def test_as_structured_keeps_names():
    from langchain_core.tools import StructuredTool

    reg = K.kalshi_tools("http://x")
    reg.update(G.graph_tools("http://y"))
    tools = as_structured(reg)
    assert len(tools) == 10
    assert all(isinstance(t, StructuredTool) for t in tools)
    by_name = {t.name: t.description for t in tools}
    assert by_name["kalshi_discover_events"].startswith("List open")
    assert "gated" in by_name["graph_post_proposals"]
