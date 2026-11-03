"""Graph store: nodes, edges, and JSON persistence.

Bitemporal pair on every record: valid_at = when it happened in the
world (article seendate, happening date; "" = unknown/always),
recorded_at / at = when an agent laid the trail. Re-laying a trail
bumps seen_count instead of duplicating (trail reinforcement).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

NODE_KINDS = ("Event", "Market", "Article", "Subject", "Hypothesis",
              "Happening", "Audit")

EDGE_RELS = ("mentions", "about", "shares-subject-with", "supports",
             "challenges", "relates-to", "has-market")

# Edges only the Connector role may write (derived, recomputed on demand).
DERIVED_RELS = ("shares-subject-with", "has-market")


def article_id(url: str) -> str:
    """Canonical Article node id for a URL (sha1-12, dedupes cleanly)."""
    import hashlib

    return "article:" + hashlib.sha1(url.encode()).hexdigest()[:12]


def subject_id(term: str) -> str:
    """Canonical Subject node id for a watch term or entity."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
    return "subject:" + slug


def happening_id(slug: str) -> str:
    """Canonical Happening node id for a dated key event."""
    import re

    clean = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    return "happening:" + clean


@dataclass(frozen=True)
class Node:
    id: str
    kind: str  # see NODE_KINDS
    title: str
    created_at: str = ""
    # Bitemporal pair: valid_at = when it happened in the world
    # (article seendate, happening date; "" = unknown/always), recorded_at
    # = when an agent laid the trail. created_at is the legacy alias.
    valid_at: str = ""
    recorded_at: str = ""
    # Live attribute payload. Market nodes carry odds here
    # (price_cents, yes_bid/yes_ask, volume_24h, odds_at); Happenings
    # carry move details. History is NOT versioned on the node — every
    # refresh is a logged batch, so graph_log.jsonl holds the series.
    props: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    rel: str  # see EDGE_RELS
    at: str  # first-recorded timestamp, version marker
    valid_at: str = ""  # world time; "" = unknown/always
    # Trail weight: re-laying an existing trail bumps seen_count instead
    # of duplicating, so repeatedly confirmed links grow heavier.
    seen_count: int = 1
    last_seen_at: str = ""


@dataclass
class GraphStore:
    """File-backed-shaped store: dict + edge log, JSON save/load."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)

    def add_node(self, node: Node) -> bool:
        if node.id in self.nodes:
            return False
        self.nodes[node.id] = node
        return True

    def add_edge(self, edge: Edge) -> None:
        self.edges.append(edge)

    def retire_node(self, node_id: str) -> tuple[bool, int]:
        """Remove a node and its incident edges (delisted markets etc.).

        Returns (removed, dropped_edges). Idempotent: unknown ids
        report (False, 0). Recorded in the commit log by
        persist.commit_retire so replay reproduces the removal.
        """
        if node_id not in self.nodes:
            return False, 0
        del self.nodes[node_id]
        kept = [e for e in self.edges
                if e.src != node_id and e.dst != node_id]
        dropped = len(self.edges) - len(kept)
        self.edges = kept
        return True, dropped

    def now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def to_json(self) -> dict:
        return {
            "nodes": [vars(n) for n in self.nodes.values()],
            "edges": [vars(e) for e in self.edges],
        }

    @classmethod
    def from_json(cls, data: dict) -> "GraphStore":
        store = cls()
        for n in data.get("nodes", []):
            store.nodes[n["id"]] = Node(**n)
        for e in data.get("edges", []):
            store.edges.append(Edge(**e))
        return store
