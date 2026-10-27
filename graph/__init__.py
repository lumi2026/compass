"""Layer 4 contracts: living event graph + update API.

Time-versioned edges (no overwrites), idempotent commits, and a
Validate | Commit | Record gate so agents cannot corrupt the graph.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # Event | Market | Article | Hypothesis
    title: str
    created_at: str = ""


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    rel: str  # relates-to | supports | challenges
    at: str  # timestamp, version marker


@dataclass
class GraphStore:
    """Minimal file-backed-shaped store contract (in-memory for now)."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node: Node) -> bool:
        if node.id in self.nodes:
            return False
        self.nodes[node.id] = node
        return True

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CommitResult:
    committed: bool
    reason: str = ""


class GraphUpdateAPI:
    """Gate contract: validate, commit idempotently, record history."""

    def __init__(self, store: GraphStore | None = None) -> None:
        self.store = store or GraphStore()

    def validate(self, node: Node) -> CommitResult:
        if not node.id or not node.kind or not node.title:
            return CommitResult(False, "invalid node: id/kind/title required")
        return CommitResult(True)

    def commit(self, node: Node) -> CommitResult:
        verdict = self.validate(node)
        if not verdict.committed:
            return verdict
        added = self.store.add_node(node)
        return CommitResult(True, "created" if added else "duplicate: kept existing")

    def record(self, edge: Edge) -> None:
        self.store.add_edge(edge)
