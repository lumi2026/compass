"""Focused squad-split tests. No network: fixtures only."""

import json

from graph import GraphStore, GraphUpdateAPI, Node, Edge, proposals_to_commit
from squad import Orchestrator, Skeptic
from squad.connector import Connector
from squad.investigator import Investigator
from squad.pipeline import build_investigator_context
from squad.scout import Scout


class FakeMarket:
    def __init__(self, ticker, title, last_price=None, volume_24h=None):
        self.ticker = ticker
        self.title = title
        self.last_price = last_price
        self.volume_24h = volume_24h


def _commit_all(api, proposals, agent_action=None):
    out = []
    for p in proposals:
        r = proposals_to_commit(api, p.agent, p.action, p.entity_id, p.detail)
        assert r.committed, f"{p} refused: {r.reason}"
        out.append(r)
    return out


def test_scout_offline_context_markets():
    sc = Scout()
    markets = [FakeMarket("E1-A", "A wins"), FakeMarket("E1-B", "B wins")]
    props = sc.run({"event_ticker": "E1"}, {"markets": markets})
    assert props[0].entity_id == "kalshi:E1"
    assert {p.entity_id for p in props} == {"kalshi:E1", "market:E1-A", "market:E1-B"}
    api = GraphUpdateAPI(GraphStore())
    _commit_all(api, props)
    assert "kalshi:E1" in api.store.nodes


def test_scout_market_nodes_carry_odds():
    sc = Scout()
    markets = [FakeMarket("E1-A", "A wins", last_price=62, volume_24h=900)]
    (market_prop,) = [p for p in sc.run({"event_ticker": "E1"},
                                        {"markets": markets})
                      if p.entity_id == "market:E1-A"]
    detail = json.loads(market_prop.detail)
    assert detail["props"]["price_cents"] == 62
    assert detail["props"]["volume_24h"] == 900
    assert detail["props"]["odds_at"]
    api = GraphUpdateAPI(GraphStore())
    assert proposals_to_commit(
        api, market_prop.agent, market_prop.action,
        market_prop.entity_id, market_prop.detail).committed
    assert api.store.nodes["market:E1-A"].props["price_cents"] == 62


def test_scout_lane_only_add_node():
    api = GraphUpdateAPI(GraphStore())
    bad = proposals_to_commit(api, "scout", "add-edge", "x",
                              json.dumps({"src": "a", "dst": "b", "rel": "relates-to"}))
    assert bad.committed is False


def test_investigator_hypothesis_offline():
    inv = Investigator()
    kept = [{"url": "https://t.test/a1", "title": "A1", "subjects": ["spacex"]},
            {"url": "https://t.test/a2", "title": "A2", "subjects": ["spacex"]}]
    ctx = build_investigator_context(kept=kept, markets=[FakeMarket("E1-A", "Q?")])
    props = inv.run({"event_ticker": "E1", "terms": ["spacex"]}, ctx)
    assert props[0].action == "add-node"
    assert props[0].entity_id.startswith("hypothesis:e1-")
    assert any(p.action == "add-edge" for p in props[1:])
    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="kalshi:E1", kind="Event", title="E1"))
    _commit_all(api, [p for p in props if p.action == "add-node"])
    for p in props:
        if p.action == "add-edge":
            d = json.loads(p.detail)
            if d["dst"].startswith("article:"):
                api.commit(Node(id=d["dst"], kind="Article", title="a"))
            if d["dst"].startswith("market:"):
                api.commit(Node(id=d["dst"], kind="Market", title="m"))
    for p in props:
        if p.action == "add-edge":
            r = proposals_to_commit(api, p.agent, p.action, p.entity_id, p.detail)
            assert r.committed, r.reason


def test_investigator_empty_kept_returns_none():
    assert Investigator().run({"event_ticker": "E1"}, {"kept": []}) == []


def test_investigator_carries_typed_relation():
    kept = [{"url": "https://t.test/a1", "title": "A1"}]
    props = Investigator().run(
        {"event_ticker": "E1", "mode": "co-location"}, {"kept": kept})
    detail = json.loads(props[0].detail)
    assert detail["props"]["type"] == "co_location"
    assert detail["props"]["mode"] == "co-location"
    props = Investigator().run(
        {"event_ticker": "E1", "hypothesis_type": "shared_context"},
        {"kept": kept})
    assert json.loads(props[0].detail)["props"]["type"] == "shared_context"
    # Legacy default stays extractive-safe.
    props = Investigator().run({"event_ticker": "E1"}, {"kept": kept})
    assert json.loads(props[0].detail)["props"]["type"] == "co_mention"


def test_connector_derive_and_followups():
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
    con = Connector()
    (intent,) = con.run({"event_ticker": "E1"})
    assert intent.action == "derive-links" and intent.entity_id == "kalshi:E1"
    approved, _ = Skeptic().review([intent])
    assert approved == [intent]
    r = proposals_to_commit(api, intent.agent, intent.action,
                            intent.entity_id, intent.detail)
    assert r.committed and r.reason.startswith("derived")
    assert proposals_to_commit(api, intent.agent, intent.action,
                               intent.entity_id, intent.detail).reason == "derived 0"
    assert con.followups(api.store, "kalshi:E2") == [
        {"event_ticker": "E1", "terms": ["spacex"]}]


def test_orchestrator_fanout_scout_investigator_connector():
    sc = Scout(markets_fn=lambda t: [FakeMarket("E1-A", "Q?")])
    inv = Investigator()
    con = Connector()
    orch = Orchestrator(agents=[sc, con])
    approved = orch.investigate({"event_ticker": "E1", "terms": ["spacex"]},
                               {"markets": [FakeMarket("E1-A", "Q?")]})
    assert {p.agent for p in approved} == {"scout", "connector"}
    ctx = build_investigator_context(kept=[{"url": "https://t.test/a"}],
                                     markets=[FakeMarket("E1-A", "Q?")])
    assert Investigator().run({"event_ticker": "E1"}, ctx)
    assert inv.name == "investigator" and con.name == "connector"
