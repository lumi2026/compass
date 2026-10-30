"""Unit 1: bitemporal timestamps + trail reinforcement in the graph layer."""

import json

from graph import (
    Edge, GraphStore, GraphUpdateAPI, Node, proposals_to_commit,
    refresh_derived_links, subgraph_as_of,
)


def _api():
    return GraphUpdateAPI(GraphStore())


def test_old_json_loads_with_defaults(tmp_path):
    legacy = {"nodes": [{"id": "kalshi:X", "kind": "Event",
                         "title": "X", "created_at": "t"}],
              "edges": [{"src": "a", "dst": "b", "rel": "mentions",
                         "at": "t"}]}
    p = tmp_path / "old.json"
    p.write_text(json.dumps(legacy))
    api = GraphUpdateAPI.load(str(p))
    n = api.store.nodes["kalshi:X"]
    assert (n.valid_at, n.recorded_at) == ("", "")
    e = api.store.edges[0]
    assert (e.valid_at, e.seen_count, e.last_seen_at) == ("", 1, "")


def test_commit_fills_recorded_time():
    api = _api()
    r = api.commit(Node(id="kalshi:X", kind="Event", title="X"))
    assert r.committed
    n = api.store.nodes["kalshi:X"]
    assert n.recorded_at and n.created_at == n.recorded_at


def test_relay_bumps_seen_count_keeps_first_at():
    api = _api()
    for nid in ("a:1", "a:2"):
        api.commit(Node(id=nid, kind="Article", title=nid))
    r1 = api.record(Edge(src="a:1", dst="a:2", rel="mentions", at="T1"))
    r2 = api.record(Edge(src="a:1", dst="a:2", rel="mentions", at="T2"))
    assert r1.reason == "recorded" and r2.reason == "reinforced x2"
    (e,) = api.store.edges
    assert (e.at, e.seen_count) == ("T1", 2) and e.last_seen_at


def test_derive_rerun_stays_zero_fresh():
    api = _api()
    for nid, kind in (("kalshi:A", "Event"), ("kalshi:B", "Event"),
                      ("subject:s", "Subject"), ("a:1", "Article")):
        api.commit(Node(id=nid, kind=kind, title=nid))
    api.record(Edge(src="a:1", dst="subject:s", rel="mentions", at="t"))
    api.record(Edge(src="a:1", dst="kalshi:A", rel="about", at="t"))
    api.record(Edge(src="a:1", dst="kalshi:B", rel="about", at="t"))
    assert refresh_derived_links(api) == 2
    assert refresh_derived_links(api) == 0  # reinforcements aren't fresh


def test_proposals_carry_valid_at():
    api = _api()
    api.commit(Node(id="kalshi:X", kind="Event", title="X"))
    r = proposals_to_commit(
        api, "news-agent", "add-node", "article:1",
        json.dumps({"kind": "Article", "title": "A",
                    "valid_at": "20261029T010000Z"}))
    assert r.committed
    assert api.store.nodes["article:1"].valid_at == "20261029T010000Z"


def test_as_of_filters_by_valid_time():
    api = _api()
    api.commit(Node(id="kalshi:X", kind="Event", title="X"))  # undated: kept
    api.commit(Node(id="article:old", kind="Article", title="old",
                    valid_at="20261028T010000Z"))
    api.commit(Node(id="article:new", kind="Article", title="new",
                    valid_at="20261029T010000Z"))
    api.record(Edge(src="article:old", dst="kalshi:X", rel="about",
                    at="t", valid_at="20261028T010000Z"))
    api.record(Edge(src="article:new", dst="kalshi:X", rel="about",
                    at="t", valid_at="20261029T010000Z"))
    snap = subgraph_as_of(api.store, "20261028T120000Z")
    assert set(snap.nodes) == {"kalshi:X", "article:old"}
    assert len(snap.edges) == 1 and snap.edges[0].src == "article:old"
    full = subgraph_as_of(api.store, "20261030T000000Z")
    assert len(full.nodes) == 3 and len(full.edges) == 2


def test_save_load_roundtrips_new_fields(tmp_path):
    api = _api()
    api.commit(Node(id="kalshi:X", kind="Event", title="X",
                    valid_at="20261028T010000Z"))
    p = tmp_path / "g.json"
    api.save(str(p))
    reloaded = GraphUpdateAPI.load(str(p))
    n = reloaded.store.nodes["kalshi:X"]
    assert n.valid_at == "20261028T010000Z" and n.recorded_at
