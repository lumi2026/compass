"""Layer 5 contracts: interactive workspace.

Thin view over the graph. Human feedback maps to typed tasks that go
back to the orchestrator queue. Rendering caps keep the demo smooth.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceTask:
    kind: str  # explore | ask | challenge
    entity_id: str
    text: str = ""


MAX_VISIBLE_NODES = 200


def to_cytoscape(nodes: list, edges: list,
                 limit: int | None = MAX_VISIBLE_NODES) -> dict:
    """Contract: serialize graph to Cytoscape.js elements dict.

    Nodes come first, then the edges whose endpoints are shown — so a
    capped payload is always a coherent subgraph, never nodes without
    their links. limit=None returns everything.
    """
    node_els = [{"data": {"id": n.id, "label": n.title, "kind": n.kind}}
                for n in nodes]
    if limit is not None:
        node_els = node_els[:limit]
    kept = {e["data"]["id"] for e in node_els}
    edge_els = [{"data": {"source": e.src, "target": e.dst, "label": e.rel}}
                for e in edges
                if e.src in kept and e.dst in kept]
    if limit is not None:
        edge_els = edge_els[:limit]
    return {"elements": node_els + edge_els}


def feedback_to_task(kind: str, entity_id: str, text: str = "") -> WorkspaceTask:
    if kind not in ("explore", "ask", "challenge"):
        raise ValueError(f"unknown feedback kind: {kind}")
    return WorkspaceTask(kind=kind, entity_id=entity_id, text=text)
