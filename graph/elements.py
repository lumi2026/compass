"""Graph-to-display serialization (absorbed from the old `workspace` layer).

Elements dict: nodes first, then the edges whose endpoints are shown —
so a capped payload is always a coherent subgraph, never nodes without
their links. limit=None returns everything. The ``{elements: [{data}]}``
shape predates the current force-graph view, which consumes ``el.data``.
"""

MAX_VISIBLE_NODES = 200


def to_elements(nodes: list, edges: list,
                limit: int | None = MAX_VISIBLE_NODES) -> dict:
    """Serialize graph nodes/edges to a view elements dict."""
    node_els = [{"data": {"id": n.id, "label": n.title, "kind": n.kind,
                            "props": dict(getattr(n, "props", {}) or {})}}
                for n in nodes]
    if limit is not None:
        node_els = node_els[:limit]
    kept = {e["data"]["id"] for e in node_els}
    edge_els = [{"data": {"source": e.src, "target": e.dst, "label": e.rel,
                           "props": dict(getattr(e, "props", {}) or {})}}
                for e in edges
                if e.src in kept and e.dst in kept]
    if limit is not None:
        edge_els = edge_els[:limit]
    return {"elements": node_els + edge_els}
