"""Pair-hypothesis id migration: qualify with the closed type."""

import sys

sys.path.insert(0, ".")

from graph import Edge, GraphStore, Node
from scripts.migrate_hypothesis_ids import (
    compare_views,
    migrate_store,
    validate,
)
from scripts.news_loop import loop_hypothesis_id


def test_loop_hypothesis_id_qualifies_type():
    assert loop_hypothesis_id("EA", "EB", "co_mention") == \
        "hypothesis:loop-ea-eb-co_mention"
    assert loop_hypothesis_id("EA", "EB", "co_outcome", prefix="odds") == \
        "hypothesis:odds-ea-eb-co_outcome"
    assert loop_hypothesis_id("EA", "EB", "co_mention") != \
        loop_hypothesis_id("EB", "EA", "co_mention")


def _legacy_pair(a="EA", b="EB", htype="co_mention", mode="co-mention",
                 prefix="loop"):
    hid = f"hypothesis:{prefix}-{a.lower()}-{b.lower()}"
    nodes = [
        Node(id=f"kalshi:{a}", kind="Event", title=a),
        Node(id=f"kalshi:{b}", kind="Event", title=b),
        Node(id="article:x", kind="Article", title="shared story"),
        Node(id=hid, kind="Hypothesis", title=f"{a} x {b}",
             props={"mode": mode, "type": htype, "evidence": 1}),
        Node(id=f"audit:{hid}", kind="Audit", title=f"flags {hid}",
             props={"target": hid, "verdict": "reject"}),
    ]
    edges = [
        Edge(src="article:x", dst=f"kalshi:{a}", rel="about", at="t"),
        Edge(src="article:x", dst=f"kalshi:{b}", rel="about", at="t"),
        Edge(src=hid, dst=f"kalshi:{a}", rel="asserts", at="t",
             props={"weight": 1, "confidence": 0.7}),
        Edge(src=hid, dst=f"kalshi:{b}", rel="asserts", at="t",
             props={"weight": 1, "confidence": 0.7}),
        Edge(src=hid, dst="article:x", rel="cites", at="t",
             props={"weight": 1, "confidence": 0.6}),
        Edge(src=f"audit:{hid}", dst=hid, rel="challenges", at="t"),
    ]
    return GraphStore(nodes={n.id: n for n in nodes}, edges=edges)


def test_migrate_qualifies_ids_and_rewrites_endpoints():
    original = _legacy_pair()
    migrated, report = migrate_store(original)
    hid = "hypothesis:loop-ea-eb-co_mention"
    assert hid in migrated.nodes
    assert f"audit:{hid}" in migrated.nodes
    assert migrated.nodes[f"audit:{hid}"].props["target"] == hid
    assert "hypothesis:loop-ea-eb" not in migrated.nodes
    assert {(e.src, e.dst, e.rel) for e in migrated.edges} == {
        ("article:x", "kalshi:EA", "about"),
        ("article:x", "kalshi:EB", "about"),
        (hid, "kalshi:EA", "asserts"),
        (hid, "kalshi:EB", "asserts"),
        (hid, "article:x", "cites"),
        (f"audit:{hid}", hid, "challenges"),
    }
    # Edge strength survives the rename untouched.
    weights = {(e.src, e.dst): e.props.get("weight") for e in migrated.edges}
    assert weights[(hid, "kalshi:EA")] == 1
    assert report["renamed"] == 2  # hypothesis + audit node
    assert report["renamed_edges"] == 4
    assert report["by_prefix"] == {"loop": 1}


def test_migrate_passes_validation_and_view_comparison():
    original = _legacy_pair()
    migrated, report = migrate_store(original)
    validate(migrated, original, report)
    rename = {"hypothesis:loop-ea-eb": "hypothesis:loop-ea-eb-co_mention",
              "audit:hypothesis:loop-ea-eb":
              "audit:hypothesis:loop-ea-eb-co_mention"}
    check = compare_views(original, migrated, rename)
    assert check["view_nodes"] == 5 and check["view_edges"] == 6
    assert check["renamed"] == 2


def test_migration_is_idempotent():
    original = _legacy_pair()
    migrated, _ = migrate_store(original)
    again, report = migrate_store(migrated)
    assert report["renamed"] == 0
    assert report["renamed_edges"] == 0
    assert again.to_json() == migrated.to_json()


def test_sibling_types_coexist_for_one_pair():
    from graph import GraphUpdateAPI, proposals_to_commit
    import json as _json

    api = GraphUpdateAPI(GraphStore())
    api.commit(Node(id="kalshi:EA", kind="Event", title="EA"))
    api.commit(Node(id="kalshi:EB", kind="Event", title="EB"))
    for htype, mode in (("co_mention", "co-mention"),
                        ("co_location", "co-location")):
        hid = loop_hypothesis_id("EA", "EB", htype)
        r = api.commit(Node(id=hid, kind="Hypothesis",
                            title=f"{htype} claim",
                            props={"mode": mode, "type": htype}))
        assert r.committed, r.reason
        for end in ("kalshi:EA", "kalshi:EB"):
            r = proposals_to_commit(
                api, "investigator", "add-edge", hid, _json.dumps(
                    {"src": hid, "dst": end, "rel": "asserts",
                     "props": {"weight": 2, "confidence": 0.7}}))
            assert r.committed, r.reason
    hids = [n for n in api.store.nodes if n.startswith("hypothesis:")]
    assert len(hids) == 2  # no refresh clobbering across types
