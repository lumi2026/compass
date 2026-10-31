"""Graph gate: Validate | Commit | Record so agents cannot corrupt the graph.

Which proposal actions each agent role may write (role lanes). Derived
edges are never proposed directly: only the Connector derives them
(see graph.derive).
"""

import json
from dataclasses import dataclass, replace

from .store import EDGE_RELS, NODE_KINDS, Edge, GraphStore, Node


@dataclass(frozen=True)
class CommitResult:
    committed: bool
    reason: str = ""


class GraphUpdateAPI:
    """Gate contract: validate, commit idempotently, record history."""

    def __init__(self, store: GraphStore | None = None,
                 revision: int = 0) -> None:
        self.store = store or GraphStore()
        self.revision = revision

    def validate(self, node: Node) -> CommitResult:
        if not node.id or not node.kind or not node.title:
            return CommitResult(False, "invalid node: id/kind/title required")
        if node.kind not in NODE_KINDS:
            return CommitResult(False, f"invalid kind: {node.kind!r}")
        return CommitResult(True)

    def commit(self, node: Node) -> CommitResult:
        verdict = self.validate(node)
        if not verdict.committed:
            return verdict
        if not isinstance(node.props, dict):
            return CommitResult(False, "invalid node: props must be an object")
        now = self.store.now()
        if not node.recorded_at or not node.created_at:
            node = replace(node, recorded_at=node.recorded_at or now,
                           created_at=node.created_at or node.recorded_at or now)
        existing = self.store.nodes.get(node.id)
        if existing is None:
            self.store.add_node(node)
            return CommitResult(True, "created")
        merged_props = ({**existing.props, **node.props} if node.props
                        else dict(existing.props))
        if node.title == existing.title and merged_props == existing.props:
            return CommitResult(True, "duplicate: kept existing")
        self.store.nodes[node.id] = replace(
            existing, title=node.title or existing.title,
            props=merged_props, recorded_at=now)
        return CommitResult(True, "refreshed")

    def validate_edge(self, edge: Edge) -> CommitResult:
        if edge.rel not in EDGE_RELS:
            return CommitResult(False, f"invalid rel: {edge.rel!r}")
        if edge.src not in self.store.nodes:
            return CommitResult(False, f"unknown src: {edge.src!r}")
        if edge.dst not in self.store.nodes:
            return CommitResult(False, f"unknown dst: {edge.dst!r}")
        return CommitResult(True)

    def record(self, edge: Edge) -> CommitResult:
        """Idempotent on (src, dst, rel): re-laying a trail keeps
        first-seen `at` and bumps `seen_count` (trail reinforcement)."""
        verdict = self.validate_edge(edge)
        if not verdict.committed:
            return verdict
        for i, e in enumerate(self.store.edges):
            if (e.src, e.dst, e.rel) == (edge.src, edge.dst, edge.rel):
                n = e.seen_count + 1
                self.store.edges[i] = replace(
                    e, seen_count=n,
                    last_seen_at=edge.last_seen_at or self.store.now())
                return CommitResult(True, f"reinforced x{n}")
        if not edge.last_seen_at:
            edge = replace(edge, last_seen_at=edge.at)
        self.store.add_edge(edge)
        return CommitResult(True, "recorded")

    def to_snapshot(self) -> dict:
        """Checkpoint payload: revision + store (atomic write unit)."""
        return {"revision": self.revision, "store": self.store.to_json()}

    def save(self, path: str) -> str:
        """Persist the store to JSON, atomically (temp + rename)."""
        import os
        import tempfile

        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)) or ".",
            prefix=".graph_snapshot_")
        try:
            with open(tmp_fd, "w") as f:
                json.dump(self.to_snapshot(), f, indent=1)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return path

    @classmethod
    def load(cls, path: str) -> "GraphUpdateAPI":
        """Load a persisted store (missing file -> empty store).

        Accepts both the snapshot envelope {"revision", "store"} and
        the legacy plain {"nodes", "edges"} shape.
        """
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return cls()
        if isinstance(data, dict) and "store" in data and "revision" in data:
            try:
                rev = int(data.get("revision", 0))
            except (TypeError, ValueError):
                rev = 0
            try:
                return cls(GraphStore.from_json(data["store"]), revision=rev)
            except (AttributeError, TypeError, ValueError, KeyError):
                return cls()
        try:
            return cls(GraphStore.from_json(data))
        except (AttributeError, TypeError, ValueError, KeyError):
            return cls()


# -- collective authorship ----------------------------------------------

# Which proposal actions each agent role may write. Derived edges are
# never proposed directly: only the Connector derives them.
ROLE_ACTIONS = {
    "scout": ("add-node",),
    "news-agent": ("add-node", "add-edge"),
    "investigator": ("add-node", "add-edge"),
    "auditor": ("add-node", "add-edge"),
    "connector": ("derive-links",),
}


def proposals_to_commit(api: GraphUpdateAPI, agent: str, action: str,
                         entity_id: str, detail: str) -> CommitResult:
    """Translate one gated Proposal into a graph write.

    `add-node`: entity_id is the node id, detail is JSON {kind, title}.
    `add-edge`: detail is JSON {src, dst, rel}.
    `derive-links`: connector-only, recomputes derived edges.
    Anything outside the role lane (or malformed JSON) is refused.
    `news_summary` is never committable: only graph_proposals() commits.
    """
    from .derive import refresh_derived_links  # lazy: derive imports gate types

    if action not in ROLE_ACTIONS.get(agent, ()):
        return CommitResult(False, f"role {agent!r} may not {action!r}")
    if action == "derive-links":
        fresh = refresh_derived_links(api)
        return CommitResult(True, f"derived {fresh}")
    try:
        payload = json.loads(detail) if detail else {}
    except ValueError:
        return CommitResult(False, "detail is not JSON")
    if action == "add-node":
        if not isinstance(payload, dict) or "kind" not in payload:
            return CommitResult(False, "add-node needs {kind, title}")
        props = payload.get("props", {})
        if not isinstance(props, dict):
            return CommitResult(False, "add-node props must be an object")
        return api.commit(Node(id=entity_id,
                               kind=payload["kind"],
                               title=payload.get("title", entity_id),
                               created_at=api.store.now(),
                               valid_at=str(payload.get("valid_at", "")),
                               props=props))
    if action == "add-edge":
        if not isinstance(payload, dict) or not {"src", "dst", "rel"} <= set(payload):
            return CommitResult(False, "add-edge needs {src, dst, rel}")
        return api.record(Edge(src=payload["src"], dst=payload["dst"],
                               rel=payload["rel"], at=api.store.now(),
                               valid_at=str(payload.get("valid_at", ""))))
    return CommitResult(False, f"unsupported action: {action!r}")
