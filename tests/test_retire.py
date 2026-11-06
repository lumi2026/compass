"""Retire dead nodes as logged batches. No network."""

import json

from graph import GraphStore, GraphUpdateAPI
from graph.persist import boot, commit_batch, commit_retire
from graph.store import Edge, Node


def _node(entity_id, kind="Event", props=None):
    detail = {"kind": kind, "title": entity_id}
    if props is not None:
        detail["props"] = props
    return {"agent": "scout", "action": "add-node", "entity_id": entity_id,
            "detail": json.dumps(detail),
            "confidence": 0.9}


def test_retire_node_drops_incident_edges():
    s = GraphStore()
    s.add_node(Node(id="kalshi:A", kind="Event", title="A"))
    s.add_node(Node(id="market:T", kind="Market", title="T"))
    s.add_node(Node(id="market:U", kind="Market", title="U"))
    s.add_edge(Edge(src="market:T", dst="kalshi:A", rel="about", at="t"))
    s.add_edge(Edge(src="market:U", dst="kalshi:A", rel="about", at="t"))
    assert s.retire_node("market:T") == (True, 1)
    assert set(s.nodes) == {"kalshi:A", "market:U"}
    assert [e.src for e in s.edges] == ["market:U"]
    assert s.retire_node("market:NOPE") == (False, 0)


def test_commit_retire_logs_and_replays(tmp_path):
    log = str(tmp_path / "g.log")
    snap = str(tmp_path / "g.json")
    api = GraphUpdateAPI(GraphStore())
    commit_batch(api, [_node("kalshi:A", "Event",
                             {"markets": {"T": {"title": "T"}}})],
                 log_path=log, snapshot_path=snap)
    out = commit_retire(api, ["market:T", "market:NOPE"],
                        log_path=log, snapshot_path=snap)
    assert out["revision"] == 2
    assert out["retired"][0] == {"entity_id": "market:T", "removed": True,
                                 "dropped_edges": 0}
    assert out["retired"][1]["removed"] is False
    assert "markets" not in api.store.nodes["kalshi:A"].props
    fresh = boot(snap, log)
    assert "markets" not in fresh.store.nodes["kalshi:A"].props
    assert "kalshi:A" in fresh.store.nodes
    assert fresh.revision == 2


def _mesh_fixture(api):
    """Two events bridged by one shared article (the Mars/Mamdani shape)."""
    from graph import proposals_to_commit
    from graph.persist import commit_batch
    nodes = [
        _node("kalshi:EA", "Event"), _node("kalshi:EB", "Event"),
        _node("article:X", "Article", {"subjects": ["s"]})]
    commit_batch(api, nodes, log_path=None, snapshot_path=None)
    for src, dst, rel in [
            ("article:X", "kalshi:EA", "about"),
            ("article:X", "kalshi:EB", "about")]:
        proposals_to_commit(
            api, "news-agent", "add-edge", f"{src}{dst}",
            json.dumps({"src": src, "dst": dst, "rel": rel}))
    # Simulate a legacy snapshot: old writers persisted the mesh both ways.
    api.store.add_edge(Edge(src="kalshi:EA", dst="kalshi:EB",
                            rel="shares-subject-with", at="t"))
    api.store.add_edge(Edge(src="kalshi:EB", dst="kalshi:EA",
                            rel="shares-subject-with", at="t"))


def test_retire_prunes_orphaned_subject_mesh():
    api = GraphUpdateAPI(GraphStore())
    _mesh_fixture(api)
    out = commit_retire(api, ["article:X"], log_path=None,
                        snapshot_path=None)
    assert out["retired"][0]["removed"] is True
    assert out["pruned_derived"] == 2
    mesh = [e for e in api.store.edges
            if e.rel == "shares-subject-with"]
    assert mesh == []


def test_prune_only_touches_derived_rels():
    from graph import prune_derived_links
    api = GraphUpdateAPI(GraphStore())
    _mesh_fixture(api)
    api.store.add_edge(Edge(src="article:X", dst="kalshi:EA",
                            rel="about", at="t"))
    api.store.add_edge(Edge(src="kalshi:EA", dst="kalshi:NOPE",
                            rel="has-market", at="t"))
    assert prune_derived_links(api.store) == 1  # the dangling derived edge
    assert any(e.rel == "about" for e in api.store.edges)
    assert any(e.rel == "shares-subject-with" for e in api.store.edges)


def test_retire_survives_full_replay_from_scratch(tmp_path):
    log = str(tmp_path / "g.log")
    snap = str(tmp_path / "g.json")
    api = GraphUpdateAPI(GraphStore())
    commit_batch(api, [_node("kalshi:A", "Event",
                             {"markets": {"T": {"title": "T"}}})],
                 log_path=log, snapshot_path=snap)
    commit_retire(api, ["market:T"], log_path=log, snapshot_path=snap)
    # boot with no snapshot replays everything: markets re-created,
    # then retired again.
    fresh = boot(str(tmp_path / "nosnap.json"), log)
    assert "kalshi:A" in fresh.store.nodes
    assert "markets" not in fresh.store.nodes["kalshi:A"].props
    assert fresh.revision == 2
