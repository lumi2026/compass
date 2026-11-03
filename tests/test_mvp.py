"""MVP offline test: two events sharing a subject link up; viewer renders."""

from graph import GraphStore, GraphUpdateAPI, proposals_to_commit
from squad import Skeptic
from squad.connector import Connector
from squad.investigator import Investigator
from squad.news_agent import NewsAgent
from squad.pipeline import build_investigator_context
from squad.scout import Scout
from workspace import to_cytoscape
from workspace.viewer import render_page


class FakeMarkets:
    def __init__(self, ticker, title):
        self.ticker = ticker
        self.title = title


def _commit_all(api, props):
    for p in Skeptic().review(props)[0]:
        r = proposals_to_commit(api, p.agent, p.action, p.entity_id, p.detail)
        assert r.committed, r.reason


def test_mvp_two_events_share_subject_link():
    api = GraphUpdateAPI(GraphStore())
    tasks = [
        {"event_ticker": "KXEVA-1", "terms": ["spacex"]},
        {"event_ticker": "KXEVB-2", "terms": ["spacex", "nato"]},
    ]
    kept_sets = [
        [{"url": "http://mvp.test/a1", "title": "A1",
          "subjects": ["spacex"], "snapshot": "ts", "seendate": "ts"}],
        [{"url": "http://mvp.test/b1", "title": "B1",
          "subjects": ["spacex"], "snapshot": "ts", "seendate": "ts"}],
    ]
    scout = Scout(markets_fn=lambda e: [FakeMarkets(f"{e}-M", f"{e} title")])
    for task, kept in zip(tasks, kept_sets):
        markets = scout.markets_fn(task["event_ticker"])
        _commit_all(api, scout.run(task, {"markets": markets}))
        _commit_all(api, NewsAgent().graph_proposals(task, kept))
        _commit_all(api, Investigator().run(
            task, build_investigator_context(kept=kept, markets=markets)))
    (intent,) = Connector().run(tasks[0])
    r = proposals_to_commit(api, intent.agent, intent.action,
                            intent.entity_id, intent.detail)
    assert r.committed and "derived 4" in r.reason
    assert {(e.src, e.dst, e.rel) for e in api.store.edges
            if e.rel in ("shares-subject-with", "has-market")} == {
        ("kalshi:KXEVA-1", "kalshi:KXEVB-2", "shares-subject-with"),
        ("kalshi:KXEVB-2", "kalshi:KXEVA-1", "shares-subject-with"),
        ("kalshi:KXEVA-1", "market:KXEVA-1-M", "has-market"),
        ("kalshi:KXEVB-2", "market:KXEVB-2-M", "has-market")}

    payload = to_cytoscape(list(api.store.nodes.values()), api.store.edges)
    assert len(payload["elements"]) > 0
    page = render_page(payload, title="MVP test")
    assert "3d-force-graph" in page
    assert "kalshi:KXEVA-1" in page and "kalshi:KXEVB-2" in page


def test_viewer_empty_graph():
    page = render_page({"elements": []}, title="empty")
    assert 'id="counts"' in page and "3d-force-graph" in page


def test_capped_payload_keeps_edges_coherent():
    from graph import Edge, Node
    from workspace import to_cytoscape

    nodes = [Node(id=f"n{i}", kind="Event", title=f"t{i}") for i in range(5)]
    edges = [Edge(src="n0", dst="n4", rel="mentions", at="t"),
             Edge(src="n4", dst="n3", rel="mentions", at="t")]
    payload = to_cytoscape(nodes, edges, limit=3)
    ids = [e["data"].get("id", "") for e in payload["elements"]]
    assert ids == ["n0", "n1", "n2"]
    assert payload["elements"][3:] == []  # n3/n4 cut -> their edges cut too
    full = to_cytoscape(nodes, edges, limit=None)
    assert len(full["elements"]) == 7


def _linked_pair():
    return [
        {"data": {"id": "kalshi:X",
                  "label": "Will Kevin Warsh be out as chairman of the "
                           "Board of Governors?",
                  "kind": "Event"}},
        {"data": {"id": "m:Y", "label": "why", "kind": "Market"}},
        {"data": {"source": "kalshi:X", "target": "m:Y",
                  "label": "relates-to"}},
    ]


def test_viewer_interactive_labels():
    from workspace.viewer import render_page, _short
    els = _linked_pair()
    page = render_page({"elements": els})
    long_title = els[0]["data"]["label"]
    assert _short(long_title) in page  # truncated sprite label
    assert long_title in page  # full title present for hover/click pop-out
    assert 'id="zin"' in page and 'id="zout"' in page and 'id="zreset"' in page
    assert 'id="info"' in page and 'id="allLabels"' in page
    assert "3d-force-graph" in page
    assert "three@0.186.1" in page and "3d-force-graph@1.80.1" in page


def test_viewer_embeds_sized_colored_nodes():
    import json
    import re

    from workspace.viewer import render_page
    page = render_page({"elements": _linked_pair()})
    m = re.search(r"const graphData = (\{.*?\});\n", page, re.S)
    data = json.loads(m.group(1))
    assert {n["id"] for n in data["nodes"]} == {"kalshi:X", "m:Y"}
    assert all(n["val"] > 0 and n["color"].startswith("#")
               for n in data["nodes"])
    assert data["nodes"][0]["neighbors"] == ["m:Y"]
    assert data["links"] == [{"source": "kalshi:X", "target": "m:Y",
                              "rel": "relates-to", "color": "#999999",
                              "w": 1}]


def test_viewer_drops_unlinked_nodes():
    from workspace.viewer import render_page
    page = render_page({"elements": [
        {"data": {"id": "a:1", "label": "linked one", "kind": "Article"}},
        {"data": {"id": "a:2", "label": "linked two", "kind": "Article"}},
        {"data": {"id": "a:3", "label": "lonely node", "kind": "Article"}},
        {"data": {"source": "a:1", "target": "a:2", "label": "relates-to"}},
    ]})
    assert "linked one" in page and "linked two" in page
    assert "lonely node" not in page
