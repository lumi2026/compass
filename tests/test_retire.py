"""Retire dead nodes as logged batches. No network."""

import json

from graph import GraphStore, GraphUpdateAPI
from graph.persist import boot, commit_batch, commit_retire
from graph.store import Edge, Node


def _node(entity_id, kind="Market"):
    return {"agent": "scout", "action": "add-node", "entity_id": entity_id,
            "detail": json.dumps({"kind": kind, "title": entity_id}),
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
    commit_batch(api, [_node("kalshi:A", "Event"), _node("market:T")],
                 log_path=log, snapshot_path=snap)
    out = commit_retire(api, ["market:T", "market:NOPE"],
                        log_path=log, snapshot_path=snap)
    assert out["revision"] == 2
    assert out["retired"][0] == {"entity_id": "market:T", "removed": True,
                                 "dropped_edges": 0}
    assert out["retired"][1]["removed"] is False
    assert "market:T" not in api.store.nodes
    fresh = boot(snap, log)
    assert "market:T" not in fresh.store.nodes
    assert "kalshi:A" in fresh.store.nodes
    assert fresh.revision == 2


def test_retire_survives_full_replay_from_scratch(tmp_path):
    log = str(tmp_path / "g.log")
    snap = str(tmp_path / "g.json")
    api = GraphUpdateAPI(GraphStore())
    commit_batch(api, [_node("market:T")], log_path=log, snapshot_path=snap)
    commit_retire(api, ["market:T"], log_path=log, snapshot_path=snap)
    # boot with no snapshot replays everything: re-created, then re-retired
    fresh = boot(str(tmp_path / "nosnap.json"), log)
    assert "market:T" not in fresh.store.nodes
    assert fresh.revision == 2
