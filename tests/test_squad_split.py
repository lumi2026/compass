"""Focused squad-split tests. No network: fixtures only."""

import json

from graph import (
    Edge, GraphStore, GraphUpdateAPI, Node, proposals_to_commit,
)
from squad import Orchestrator
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
    (event,) = props
    assert event.entity_id == "kalshi:E1"
    detail = json.loads(event.detail)
    assert set(detail["props"]["markets"]) == {"E1-A", "E1-B"}
    api = GraphUpdateAPI(GraphStore())
    _commit_all(api, props)
    assert "kalshi:E1" in api.store.nodes


def test_scout_market_nodes_carry_odds():
    sc = Scout()
    markets = [FakeMarket("E1-A", "A wins", last_price=62, volume_24h=900)]
    (event,) = sc.run({"event_ticker": "E1"}, {"markets": markets})
    detail = json.loads(event.detail)
    market = detail["props"]["markets"]["E1-A"]
    assert market["price_cents"] == 62
    assert market["volume_24h"] == 900
    assert market["odds_at"]
    api = GraphUpdateAPI(GraphStore())
    assert proposals_to_commit(
        api, event.agent, event.action,
        event.entity_id, event.detail).committed
    assert api.store.nodes["kalshi:E1"].props["markets"]["E1-A"][
        "price_cents"] == 62


def test_scout_lane_only_add_node():
    api = GraphUpdateAPI(GraphStore())
    bad = proposals_to_commit(api, "scout", "add-edge", "x",
                              json.dumps({"src": "a", "dst": "b", "rel": "about"}))
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
    api.commit(Node(id="kalshi:E1", kind="Event", title="E1",
                    props={"markets": {"E1-A": {"title": "Q?"}}}))
    _commit_all(api, [p for p in props if p.action == "add-node"])
    for p in props:
        if p.action == "add-edge":
            d = json.loads(p.detail)
            if d["dst"].startswith("article:"):
                api.commit(Node(id=d["dst"], kind="Article", title="a"))
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


def test_connector_followups_dispatch():
    api = GraphUpdateAPI(GraphStore())
    for n in [Node(id="kalshi:E1", kind="Event", title="E1"),
              Node(id="kalshi:E2", kind="Event", title="E2"),
              Node(id="article:a", kind="Article", title="A",
                   props={"subjects": ["spacex"]})]:
        api.commit(n)
    for e in [Edge(src="article:a", dst="kalshi:E1", rel="about", at="t"),
              Edge(src="article:a", dst="kalshi:E2", rel="about", at="t")]:
        api.record(e)
    con = Connector()
    assert con.followups(api.store, "kalshi:E2") == [
        {"event_ticker": "E1", "terms": ["spacex"]}]


def test_orchestrator_fanout_gates_through_skeptic():
    sc = Scout(markets_fn=lambda t: [FakeMarket("E1-A", "Q?")])
    inv = Investigator()
    con = Connector()
    orch = Orchestrator(agents=[sc])
    approved = orch.investigate({"event_ticker": "E1", "terms": ["spacex"]},
                               {"markets": [FakeMarket("E1-A", "Q?")]})
    assert {p.agent for p in approved} == {"scout"}
    ctx = build_investigator_context(kept=[{"url": "https://t.test/a"}],
                                     markets=[FakeMarket("E1-A", "Q?")])
    assert Investigator().run({"event_ticker": "E1"}, ctx)
    assert inv.name == "investigator" and con.name == "connector"
