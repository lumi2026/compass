"""Deterministic contract tests. No network: fixtures only."""

from graph import CommitResult, GraphStore, GraphUpdateAPI, Node, Edge
from processing import ChangeEvent, detect_new_items, investigation_triggers
from sources.base import MarketEvent
from sources.kalshi import (
    Candle,
    KalshiSource,
    MarketContract,
    OrderBookSnapshot,
    best_bid,
)
from squad import Orchestrator, Proposal, Skeptic
from workspace import MAX_VISIBLE_NODES, feedback_to_task, to_cytoscape


class EchoAgent:
    name = "echo"

    def __init__(self, proposals):
        self._proposals = proposals

    def run(self, task, context):
        return self._proposals


def test_source_contract_shape():
    item = MarketEvent(source="kalshi", external_id="E1", title="Test event")
    assert item.source and item.external_id and item.title


def test_processing_detects_only_new_items():
    items = [
        MarketEvent(source="kalshi", external_id="E1", title="one"),
        MarketEvent(source="kalshi", external_id="E2", title="two"),
    ]
    changes = detect_new_items({"kalshi:E1"}, items, kind="new_event")
    assert [c.entity_id for c in changes] == ["kalshi:E2"]
    assert investigation_triggers(changes) is True
    assert investigation_triggers([]) is False


def test_skeptic_vetoes_weak_proposals():
    good = Proposal(agent="scout", action="link", entity_id="kalshi:E1", confidence=0.9)
    weak = Proposal(agent="scout", action="", entity_id="", confidence=0.0)
    approved, verdicts = Skeptic(min_confidence=0.3).review([good, weak])
    assert approved == [good]
    assert [v.approved for v in verdicts] == [True, False]


def test_orchestrator_gates_through_skeptic():
    good = Proposal(agent="scout", action="link", entity_id="kalshi:E1", confidence=0.8)
    bad = Proposal(agent="scout", action="", entity_id="", confidence=0.0)
    orch = Orchestrator(agents=[EchoAgent([good, bad])])
    assert orch.investigate("check E1") == [good]


def test_graph_update_api_idempotent_and_validating():
    api = GraphUpdateAPI(GraphStore())
    node = Node(id="kalshi:E1", kind="Event", title="Test")
    first = api.commit(node)
    second = api.commit(node)
    assert first.committed and second.committed
    assert second.reason.startswith("duplicate")
    bad = api.commit(Node(id="", kind="", title=""))
    assert bad.committed is False
    assert api.commit(Node(id="x", kind="Nope", title="t")).committed is False
    api.commit(Node(id="gdelt:a1", kind="Article", title="a"))
    ok = api.record(Edge(src="kalshi:E1", dst="gdelt:a1", rel="relates-to", at="t"))
    assert ok.committed and len(api.store.edges) == 1
    assert api.record(Edge(src="kalshi:E1", dst="missing",
                           rel="relates-to", at="t")).committed is False
    assert api.record(Edge(src="kalshi:E1", dst="gdelt:a1",
                           rel="vibes", at="t")).committed is False


def test_graph_record_reinforces_reruns():
    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="kalshi:E1", kind="Event", title="E1"))
    api.commit(Node(id="gdelt:a1", kind="Article", title="a"))
    e = Edge(src="gdelt:a1", dst="kalshi:E1", rel="about", at="t1")
    assert api.record(e).reason == "recorded"
    dup = api.record(Edge(src="gdelt:a1", dst="kalshi:E1", rel="about",
                          at="t2"))
    assert dup.committed and dup.reason == "reinforced x2"
    assert len(api.store.edges) == 1
    assert api.store.edges[0].at == "t1"  # first-seen kept
    assert api.store.edges[0].seen_count == 2


def test_graph_translator_role_lanes():
    import json

    from graph import proposals_to_commit

    api = GraphUpdateAPI(GraphStore())
    good = proposals_to_commit(api, "scout", "add-node", "kalshi:E1",
                               json.dumps({"kind": "Event", "title": "E1"}))
    assert good.committed
    assert proposals_to_commit(api, "scout", "add-edge", "x",
                               json.dumps({"src": "a", "dst": "b",
                                           "rel": "relates-to"})).committed is False
    edge = proposals_to_commit(api, "news-agent", "add-edge", "",
                               json.dumps({"src": "kalshi:E1",
                                           "dst": "kalshi:E1",
                                           "rel": "relates-to"}))
    assert edge.committed  # self-loop passes endpoint validation
    assert proposals_to_commit(api, "news-agent", "add-edge", "",
                               "not-json").committed is False


def test_connector_derives_shared_subject_links():
    from graph import derive_shared_subject_links

    api = GraphUpdateAPI(GraphStore())
    for n in [Node(id="kalshi:E1", kind="Event", title="E1"),
              Node(id="kalshi:E2", kind="Event", title="E2"),
              Node(id="article:abc", kind="Article", title="A"),
              Node(id="subject:spacex", kind="Subject", title="spacex")]:
        api.commit(n)
    for e in [Edge(src="article:abc", dst="subject:spacex", rel="mentions", at="t"),
              Edge(src="article:abc", dst="kalshi:E1", rel="about", at="t"),
              Edge(src="article:abc", dst="kalshi:E2", rel="about", at="t")]:
        assert api.record(e).committed
    links = derive_shared_subject_links(api.store, "t")
    assert {(e.src, e.dst) for e in links} == {("kalshi:E1", "kalshi:E2"),
                                               ("kalshi:E2", "kalshi:E1")}
    assert all(e.rel == "shares-subject-with" for e in links)


def test_graph_store_json_roundtrip(tmp_path):
    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="kalshi:E1", kind="Event", title="E1"))
    back = GraphStore.from_json(api.store.to_json())
    assert back.nodes["kalshi:E1"].title == "E1"


def _linked_pair():
    api = GraphUpdateAPI(GraphStore())
    for n in [Node(id="kalshi:E1", kind="Event", title="E1"),
              Node(id="kalshi:E2", kind="Event", title="E2"),
              Node(id="article:a", kind="Article", title="A"),
              Node(id="subject:s", kind="Subject", title="spacex")]:
        api.commit(n)
    for e in [Edge(src="article:a", dst="subject:s", rel="mentions", at="t"),
              Edge(src="article:a", dst="kalshi:E1", rel="about", at="t"),
              Edge(src="article:a", dst="kalshi:E2", rel="about", at="t")]:
        api.record(e)
    return api


def test_followup_tasks_dispatch():
    from graph import followup_tasks

    tasks = followup_tasks(_linked_pair().store, "kalshi:E2")
    assert tasks == [{"event_ticker": "E1", "terms": ["spacex"]}]


def test_refresh_derived_links_idempotent():
    from graph import refresh_derived_links

    api = _linked_pair()
    assert refresh_derived_links(api) == 2
    assert refresh_derived_links(api) == 0
    assert len(api.store.edges) == 3 + 2


def test_api_save_load_roundtrip(tmp_path):
    out = str(tmp_path / "g.json")
    api = _linked_pair()
    api.save(out)
    back = GraphUpdateAPI.load(out)
    assert len(back.store.nodes) == len(api.store.nodes)
    assert GraphUpdateAPI.load(str(tmp_path / "nope.json")).store.nodes == {}


def test_kalshi_market_normalize():
    src = KalshiSource()
    m = src.normalize_market(
        {"ticker": "T1", "event_ticker": "E1", "title": "Yes?",
         "yes_bid": 55, "yes_ask": 60, "last_price": 57}
    )
    assert isinstance(m, MarketContract)
    assert (m.ticker, m.event_ticker, m.yes_bid) == ("T1", "E1", 55)


def test_kalshi_market_normalize_dollars_strings():
    src = KalshiSource()
    m = src.normalize_market(
        {"ticker": "T1", "event_ticker": "E1", "title": "Yes?",
         "yes_bid_dollars": "0.5500", "yes_ask_dollars": "0.6000",
         "last_price_dollars": "0.5700", "volume_24h_fp": "263.00"}
    )
    assert (m.yes_bid, m.yes_ask, m.last_price, m.volume_24h) == (55, 60, 57, 263)


def test_kalshi_orderbook_normalize_and_best_bid():
    src = KalshiSource()
    snap = src.normalize_orderbook(
        "T1", {"yes": [[55, 10], [54, 5]], "no": [[44, 7]]}
    )
    assert isinstance(snap, OrderBookSnapshot)
    assert best_bid(snap.yes_bids).price_cents == 55
    assert best_bid(snap.no_bids).contracts == 7
    assert best_bid([]) is None


def test_kalshi_candle_normalize():
    src = KalshiSource()
    c = src.normalize_candle(
        "T1",
        {"end_period_ts": 123, "price": {"open": 50, "high": 60, "low": 48, "close": 57},
         "volume": 11},
    )
    assert isinstance(c, Candle)
    assert (c.end_ts, c.open, c.close, c.volume) == (123, 50, 57, 11)


def test_workspace_contracts():
    nodes = [Node(id=f"n{i}", kind="Event", title=f"t{i}") for i in range(3)]
    payload = to_cytoscape(nodes, [])
    assert len(payload["elements"]) == 3
    assert MAX_VISIBLE_NODES == 200
    task = feedback_to_task("challenge", "kalshi:E1", "why?")
    assert task.kind == "challenge"
    try:
        feedback_to_task("nope", "x")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown feedback kind")
