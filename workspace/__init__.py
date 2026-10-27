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


def to_cytoscape(nodes: list, edges: list) -> dict:
    """Contract: serialize graph to Cytoscape.js elements dict."""
    elements = [{"data": {"id": n.id, "label": n.title, "kind": n.kind}} for n in nodes]
    elements += [
        {"data": {"source": e.src, "target": e.dst, "label": e.rel}} for e in edges
    ]
    return {"elements": elements[:MAX_VISIBLE_NODES]}


def feedback_to_task(kind: str, entity_id: str, text: str = "") -> WorkspaceTask:
    if kind not in ("explore", "ask", "challenge"):
        raise ValueError(f"unknown feedback kind: {kind}")
    return WorkspaceTask(kind=kind, entity_id=entity_id, text=text)
