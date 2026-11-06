"""Migration contract: legacy folds into props without changing the view."""

from graph import Edge, GraphStore, Node
from scripts.migrate_graph import (
    compare_views,
    migrate_store,
    validate,
)


def _legacy():
    nodes = [
        Node(id="kalshi:E1", kind="Event", title="E1"),
        Node(id="kalshi:E2", kind="Event", title="E2"),
        Node(id="market:E1-A", kind="Market", title="A [E1-A]",
             props={"event_ticker": "E1", "price_cents": 55}),
        Node(id="market:E2-B", kind="Market", title="B [E2-B]",
             props={"event_ticker": "E2", "price_cents": 60}),
        Node(id="article:a", kind="Article", title="A"),
        Node(id="article:b", kind="Article", title="B"),
        Node(id="subject:spacex", kind="Subject", title="SpaceX"),
        Node(id="subject:mars", kind="Subject", title="Mars"),
        Node(id="happening:move-1", kind="Happening", title="move"),
        Node(id="hypothesis:h1", kind="Hypothesis", title="H1"),
        Node(id="hypothesis:loop-meta-a-b", kind="Hypothesis", title="meta",
             props={"layer": 2}),
    ]
    edges = [
        Edge(src="article:a", dst="subject:spacex", rel="mentions", at="t"),
        Edge(src="article:b", dst="subject:mars", rel="mentions", at="t"),
        Edge(src="article:b", dst="subject:spacex", rel="mentions", at="t"),
        Edge(src="article:a", dst="kalshi:E1", rel="about", at="t"),
        Edge(src="article:b", dst="kalshi:E2", rel="about", at="t"),
        Edge(src="hypothesis:h1", dst="market:E1-A", rel="supports", at="t"),
        Edge(src="market:E1-A", dst="happening:move-1", rel="relates-to",
             at="t"),
        Edge(src="kalshi:E1", dst="kalshi:E2", rel="shares-subject-with",
             at="t"),
        Edge(src="kalshi:E2", dst="kalshi:E1", rel="shares-subject-with",
             at="t"),
        Edge(src="kalshi:E1", dst="market:E1-A", rel="has-market", at="t"),
        Edge(src="hypothesis:loop-meta-a-b", dst="hypothesis:h1",
             rel="supports", at="t"),
    ]
    return GraphStore(nodes={n.id: n for n in nodes}, edges=edges)


def test_migrate_folds_props_and_drops_legacy():
    original = _legacy()
    migrated, report = migrate_store(original)

    assert {n.kind for n in migrated.nodes.values()} & {"Market", "Subject"} \
        == set()
    assert "hypothesis:loop-meta-a-b" not in migrated.nodes
    assert migrated.nodes["kalshi:E1"].props["markets"]["E1-A"] == {
        "event_ticker": "E1", "price_cents": 55, "title": "A [E1-A]"}
    assert migrated.nodes["article:a"].props["subjects"] == ["SpaceX"]
    assert migrated.nodes["article:b"].props["subjects"] == ["Mars", "SpaceX"]
    assert {e.rel for e in migrated.edges} <= {
        "about", "supports", "challenges", "relates-to"}
    assert [e for e in migrated.edges if e.rel == "relates-to"][0].src == \
        "kalshi:E1"
    # Primitive claims may still target a folded market.
    assert ("hypothesis:h1", "market:E1-A", "supports") in {
        (e.src, e.dst, e.rel) for e in migrated.edges}
    assert report["folded"]["markets"] == 2
    assert report["folded"]["subjects"] == 2
    assert report["dropped"]["Market"] == 2
    assert report["dropped"]["Subject"] == 2


def test_migrate_passes_validation_and_view_comparison():
    original = _legacy()
    migrated, report = migrate_store(original)
    validate(migrated, original, report)
    check = compare_views(original, migrated)
    assert check["view_nodes_before"] - len(
        check["expected_missing_nodes"]) == check["view_nodes_after"]
    assert check["mesh_after"] == check["mesh_before"]  # one pair per event pair
    assert check["expected_missing_edges"] == 1  # the layer-2 supports edge


def test_migration_is_idempotent():
    original = _legacy()
    migrated, _ = migrate_store(original)
    again, report = migrate_store(migrated)
    assert report["folded"]["markets"] == 0
    assert report["folded"]["subjects"] == 0
    assert report["dropped"] == {}
    assert report["dropped_edges"] == {}
    assert again.to_json() == migrated.to_json()
