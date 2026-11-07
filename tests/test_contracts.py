"""Deterministic contract tests. No network: fixtures only."""

from graph import (CommitResult, GraphStore, GraphUpdateAPI, Node, Edge,
                   MAX_VISIBLE_NODES, to_elements)
from squad.pipeline import ChangeEvent, detect_new_items, investigation_triggers
from sources.base import MarketEvent
from sources.kalshi import (
    Candle,
    KalshiSource,
    MarketContract,
    OrderBookSnapshot,
    best_bid,
)
from squad import Orchestrator, Proposal, Skeptic, feedback_to_task


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
    ok = api.record(Edge(src="kalshi:E1", dst="gdelt:a1", rel="about", at="t"))
    assert ok.committed and len(api.store.edges) == 1
    assert api.record(Edge(src="kalshi:E1", dst="missing",
                           rel="about", at="t")).committed is False
    assert api.record(Edge(src="kalshi:E1", dst="gdelt:a1",
                           rel="vibes", at="t")).committed is False


def test_graph_commit_refreshes_props_keeps_history_key():
    api = GraphUpdateAPI(GraphStore())
    assert api.commit(Node(id="kalshi:E", kind="Event", title="E")).reason == "created"
    created_at = api.store.nodes["kalshi:E"].created_at
    assert api.commit(Node(id="kalshi:E", kind="Event", title="E")).reason == \
        "duplicate: kept existing"
    r = api.commit(Node(id="kalshi:E", kind="Event", title="E",
                        props={"markets": {"T": {"price_cents": 55,
                                                 "odds_at": "t1"}}}))
    assert r.reason == "refreshed"
    n = api.store.nodes["kalshi:E"]
    assert n.props == {"markets": {"T": {"price_cents": 55, "odds_at": "t1"}}}
    assert n.created_at == created_at and n.recorded_at
    r2 = api.commit(Node(id="kalshi:E", kind="Event", title="E",
                         props={"markets": {"T": {"volume_24h": 9}}}))
    assert r2.reason == "refreshed"
    assert api.store.nodes["kalshi:E"].props == {
        "markets": {"T": {"price_cents": 55, "odds_at": "t1",
                          "volume_24h": 9}}}  # nested merge
    assert api.commit(Node(id="kalshi:E", kind="Event", title="E",
                           props="nope")).committed is False
    back = GraphStore.from_json(api.store.to_json())
    assert back.nodes["kalshi:E"].props["markets"]["T"]["price_cents"] == 55


def test_folded_props_merge_subjects_and_markets():
    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="article:a", kind="Article", title="A",
                    props={"subjects": ["spacex", "mars"]}))
    api.commit(Node(id="article:a", kind="Article", title="A",
                    props={"subjects": ["mars", "nato"]}))
    assert api.store.nodes["article:a"].props["subjects"] == [
        "spacex", "mars", "nato"]
    api.commit(Node(id="kalshi:E", kind="Event", title="E",
                    props={"markets": {"A": {"price_cents": 1}}}))
    api.commit(Node(id="kalshi:E", kind="Event", title="E",
                    props={"markets": {"B": {"price_cents": 2}}}))
    assert api.store.nodes["kalshi:E"].props["markets"] == {
        "A": {"price_cents": 1}, "B": {"price_cents": 2}}


def test_edge_props_persist_and_ratchet_on_relay():
    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="kalshi:A", kind="Event", title="A"))
    api.commit(Node(id="kalshi:B", kind="Event", title="B"))
    api.commit(Node(id="hypothesis:h", kind="Hypothesis", title="H"))
    r = api.record(Edge(src="hypothesis:h", dst="kalshi:A", rel="asserts",
                        at="t", props={"weight": 2, "confidence": 0.6}))
    assert r.committed
    r = api.record(Edge(src="hypothesis:h", dst="kalshi:A", rel="asserts",
                        at="t2", props={"weight": 5, "confidence": 0.5}))
    assert r.committed and "reinforced x2" in r.reason
    [e] = api.store.edges
    assert e.at == "t"  # first-seen timestamp kept
    assert e.props == {"weight": 5, "confidence": 0.6}  # strength ratchets
    assert e.seen_count == 2


def test_edge_props_must_be_an_object():
    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="kalshi:A", kind="Event", title="A"))
    api.commit(Node(id="hypothesis:h", kind="Hypothesis", title="H"))
    r = api.record(Edge(src="hypothesis:h", dst="kalshi:A", rel="asserts",
                        at="t", props=["weight"]))
    assert r.committed is False and "props must be an object" in r.reason


def test_gate_refuses_legacy_rels_with_useful_hints():
    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="kalshi:E", kind="Event", title="E"))
    api.commit(Node(id="hypothesis:h", kind="Hypothesis", title="H"))
    api.commit(Node(id="article:a", kind="Article", title="A"))
    r = api.record(Edge(src="hypothesis:h", dst="kalshi:E", rel="supports",
                        at="t"))
    assert r.committed is False and "cites" in r.reason
    assert api.record(Edge(src="hypothesis:h", dst="article:a",
                           rel="cites", at="t")).committed
    assert api.record(Edge(src="hypothesis:h", dst="kalshi:E",
                           rel="asserts", at="t")).committed
    r = api.record(Edge(src="kalshi:E", dst="kalshi:E", rel="relates-to",
                        at="t"))
    assert r.committed is False and "experienced" in r.reason


def test_gate_refuses_folded_nodes_and_mentions():
    api = GraphUpdateAPI(GraphStore())
    r = api.commit(Node(id="subject:spacex", kind="Subject", title="spacex"))
    assert r.committed is False and "folded kind" in r.reason
    r = api.commit(Node(id="market:T", kind="Market", title="T"))
    assert r.committed is False and "folded kind" in r.reason
    api.commit(Node(id="article:a", kind="Article", title="A",
                    props={"subjects": ["spacex"]}))
    r = api.record(Edge(src="article:a", dst="subject:spacex",
                        rel="mentions", at="t"))
    assert r.committed is False and "folded rel" in r.reason


def test_proposals_to_commit_passes_edge_props():
    import json

    from graph import proposals_to_commit

    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="kalshi:A", kind="Event", title="A"))
    api.commit(Node(id="article:1", kind="Article", title="one"))
    api.commit(Node(id="hypothesis:h", kind="Hypothesis", title="H"))
    r = proposals_to_commit(
        api, "investigator", "add-edge", "hypothesis:h", json.dumps(
            {"src": "hypothesis:h", "dst": "article:1", "rel": "cites",
             "props": {"weight": 1, "confidence": 0.6}}))
    assert r.committed
    [e] = api.store.edges
    assert e.rel == "cites" and e.props == {"weight": 1, "confidence": 0.6}


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
                                           "rel": "about"})).committed is False
    edge = proposals_to_commit(api, "news-agent", "add-edge", "",
                               json.dumps({"src": "kalshi:E1",
                                           "dst": "kalshi:E1",
                                           "rel": "experienced"}))
    assert edge.committed  # self-loop passes endpoint validation
    assert proposals_to_commit(api, "news-agent", "add-edge", "",
                               "not-json").committed is False


def test_graph_translator_carries_props():
    import json as _json

    from graph import proposals_to_commit as _commit

    api = GraphUpdateAPI(GraphStore())
    good = _commit(api, "scout", "add-node", "kalshi:E1",
                   _json.dumps({"kind": "Event", "title": "E1",
                                "props": {"markets": {"T": {"price_cents": 55}}}}))
    assert good.committed
    assert api.store.nodes["kalshi:E1"].props == {
        "markets": {"T": {"price_cents": 55}}}
    bad = _commit(api, "scout", "add-node", "kalshi:E2",
                  _json.dumps({"kind": "Event", "title": "E2",
                               "props": [1]}))
    assert bad.committed is False


def test_elements_carry_props():
    nodes = [Node(id="market:T", kind="Market", title="T",
                  props={"price_cents": 55})]
    payload = to_elements(nodes, [])
    assert payload["elements"][0]["data"]["props"] == {"price_cents": 55}


def test_connector_derives_shared_subject_links():
    from graph import derive_shared_subject_links

    api = GraphUpdateAPI(GraphStore())
    for n in [Node(id="kalshi:E1", kind="Event", title="E1"),
              Node(id="kalshi:E2", kind="Event", title="E2"),
              Node(id="article:abc", kind="Article", title="A",
                   props={"subjects": ["spacex"]})]:
        api.commit(n)
    for e in [Edge(src="article:abc", dst="kalshi:E1", rel="about", at="t"),
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
              Node(id="article:a", kind="Article", title="A",
                   props={"subjects": ["spacex"]})]:
        api.commit(n)
    for e in [Edge(src="article:a", dst="kalshi:E1", rel="about", at="t"),
              Edge(src="article:a", dst="kalshi:E2", rel="about", at="t")]:
        api.record(e)
    return api


def test_followup_tasks_dispatch():
    from graph import followup_tasks

    tasks = followup_tasks(_linked_pair().store, "kalshi:E2")
    assert tasks == [{"event_ticker": "E1", "terms": ["spacex"]}]


def test_derived_mesh_materializes_without_persisting():
    from graph import materialize_derived_links

    api = _linked_pair()
    derived = materialize_derived_links(api.store, "t")
    mesh = [e for e in derived if e.rel == "shares-subject-with"]
    # Undirected mesh: one canonical direction, never stored.
    assert {(e.src, e.dst) for e in mesh} == {
        ("kalshi:E1", "kalshi:E2")}
    assert len(api.store.edges) == 2
    assert all(e.rel != "shares-subject-with" for e in api.store.edges)


def test_gate_refuses_derived_edge_writes():
    api = _linked_pair()
    r = api.record(Edge(src="kalshi:E1", dst="kalshi:E2",
                        rel="shares-subject-with", at="t"))
    assert r.committed is False
    assert "read-time only" in r.reason
    assert all(e.rel != "shares-subject-with" for e in api.store.edges)


def test_derive_market_links_props_prefix_orphan():
    from graph import derive_market_links

    api = GraphUpdateAPI(GraphStore(), allow_folded_writes=True)
    for n in [Node(id="kalshi:E1", kind="Event", title="E1"),
              Node(id="market:E1-A", kind="Market", title="A",
                   props={"event_ticker": "E1"}),
              Node(id="market:E1-B", kind="Market", title="B"),
              Node(id="market:ZZZ", kind="Market", title="Z")]:
        api.commit(n)
    links = derive_market_links(api.store, "t")
    assert {(e.src, e.dst) for e in links} == {
        ("kalshi:E1", "market:E1-A"), ("kalshi:E1", "market:E1-B")}
    assert all(e.rel == "has-market" for e in links)


def test_derive_market_links_exact_ticker_match():
    from graph import derive_market_links

    api = GraphUpdateAPI(GraphStore(), allow_folded_writes=True)
    for n in [Node(id="kalshi:E1", kind="Event", title="E1"),
              Node(id="market:E1", kind="Market", title="E1")]:
        api.commit(n)
    links = derive_market_links(api.store, "t")
    assert [(e.src, e.dst) for e in links] == [("kalshi:E1", "market:E1")]


def test_market_links_materialize_without_persisting():
    from graph import materialize_derived_links, materialize_derived_nodes

    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="kalshi:E1", kind="Event", title="E1",
                    props={"markets": {"E1-A": {"title": "A"}}}))
    derived = materialize_derived_links(api.store, "t")
    [e] = [e for e in derived if e.rel == "has-market"]
    assert (e.src, e.dst) == ("kalshi:E1", "market:E1-A")
    assert all(e.rel != "has-market" for e in api.store.edges)
    [node] = materialize_derived_nodes(api.store)
    assert (node.id, node.kind, node.title) == ("market:E1-A", "Market", "A")


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


def test_display_contracts():
    nodes = [Node(id=f"n{i}", kind="Event", title=f"t{i}") for i in range(3)]
    payload = to_elements(nodes, [])
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
