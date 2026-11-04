"""Backfill core: typeless hypotheses resolve via stored mode."""

from scripts.backfill_hypothesis_type import backfill_type_proposals


def _el(i, kind, props=None, label=None):
    d = {"id": i, "kind": kind}
    if props:
        d["props"] = props
    if label:
        d["label"] = label
    return {"data": d}


def test_typeless_resolves_via_mode():
    els = [
        _el("kalshi:A", "Event"),
        _el("h1", "Hypothesis", {"mode": "co-mention"}, "A x B"),
        _el("h2", "Hypothesis", {"mode": "entity-overlap"}),
        _el("h3", "Hypothesis", {"mode": "shared-subject"}, "C x D"),
        _el("h4", "Hypothesis"),
        _el("h5", "Hypothesis", {"type": "co_outcome", "mode": "co-mention"}),
    ]
    out = backfill_type_proposals(els)
    assert out == [
        {"entity_id": "h1", "title": "A x B", "htype": "co_mention"},
        {"entity_id": "h2", "title": "h2", "htype": "co_mention"},
        {"entity_id": "h3", "title": "C x D", "htype": "shared_context"},
        {"entity_id": "h4", "title": "h4", "htype": "co_mention"},
    ]


def test_edges_and_non_hypotheses_ignored():
    els = [
        {"data": {"source": "a", "target": "b", "label": "supports"}},
        _el("s1", "Subject"),
    ]
    assert backfill_type_proposals(els) == []
