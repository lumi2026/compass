"""Layer 4 contracts: living event graph + update API.

Time-versioned edges (no overwrites), idempotent commits, and a
Validate | Commit | Record gate so agents cannot corrupt the graph.

ID scheme (stable URIs, shared by every agent):
  kalshi:{event_ticker}   Event    e.g. kalshi:KXSPACEXMARS-30
  market:{ticker}         Market   e.g. market:KXSPACEXMARS-30-30
  article:{url-sha1-12}   Article  one node per canonical URL
  subject:{slug}          Subject  shared bridge, e.g. subject:spacex
  hypothesis:{slug}-{n}   Hypothesis  investigator claim, numbered per subject

Edge vocabulary:
  mentions             Article -> Subject   (article covers the subject)
  about                Article -> Market | Event  (LLM relevance passed)
  shares-subject-with  Event <-> Event      (DERIVED by Connector only,
    via a shared Subject; never hand-written)
  supports             Hypothesis -> Market | Article
  challenges           Hypothesis -> Market | Article
  relates-to           fallback for anything unclassified
"""

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

NODE_KINDS = ("Event", "Market", "Article", "Subject", "Hypothesis")

EDGE_RELS = ("mentions", "about", "shares-subject-with", "supports",
             "challenges", "relates-to")

# Edges only the Connector role may write (derived, recomputed on demand).
DERIVED_RELS = ("shares-subject-with",)


def article_id(url: str) -> str:
    """Canonical Article node id for a URL (sha1-12, dedupes cleanly)."""
    import hashlib

    return "article:" + hashlib.sha1(url.encode()).hexdigest()[:12]


def subject_id(term: str) -> str:
    """Canonical Subject node id for a watch term or entity."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
    return "subject:" + slug


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
        if node.kind not in NODE_KINDS:
            return CommitResult(False, f"invalid kind: {node.kind!r}")
        return CommitResult(True)

    def commit(self, node: Node) -> CommitResult:
        verdict = self.validate(node)
        if not verdict.committed:
            return verdict
        now = self.store.now()
        if not node.recorded_at or not node.created_at:
            node = replace(node, recorded_at=node.recorded_at or now,
                           created_at=node.created_at or node.recorded_at or now)
        added = self.store.add_node(node)
        return CommitResult(True, "created" if added else "duplicate: kept existing")

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

    def save(self, path: str) -> str:
        """Persist the store to JSON. Services load on start."""
        with open(path, "w") as f:
            json.dump(self.store.to_json(), f, indent=1)
        return path

    @classmethod
    def load(cls, path: str) -> "GraphUpdateAPI":
        """Load a persisted store (missing file -> empty store)."""
        try:
            with open(path) as f:
                return cls(GraphStore.from_json(json.load(f)))
        except (OSError, ValueError):
            return cls()


# -- collective authorship ----------------------------------------------

# Which proposal actions each agent role may write. Derived edges are
# never proposed directly: only the Connector derives them (below).
ROLE_ACTIONS = {
    "scout": ("add-node",),
    "news-agent": ("add-node", "add-edge"),
    "investigator": ("add-node", "add-edge"),
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
    import json

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
        return api.commit(Node(id=entity_id,
                               kind=payload["kind"],
                               title=payload.get("title", entity_id),
                               created_at=api.store.now(),
                               valid_at=str(payload.get("valid_at", ""))))
    if action == "add-edge":
        if not isinstance(payload, dict) or not {"src", "dst", "rel"} <= set(payload):
            return CommitResult(False, "add-edge needs {src, dst, rel}")
        return api.record(Edge(src=payload["src"], dst=payload["dst"],
                               rel=payload["rel"], at=api.store.now(),
                               valid_at=str(payload.get("valid_at", ""))))
    return CommitResult(False, f"unsupported action: {action!r}")


def _subject_events(store: GraphStore) -> dict[str, set[str]]:
    """subject node id -> events, via article-mediated mentions + about."""
    mentions: dict[str, set[str]] = {}  # subject -> articles
    about: dict[str, set[str]] = {}  # article -> events
    for e in store.edges:
        if e.rel == "mentions" and store.nodes.get(e.dst, Node("", "", "")).kind == "Subject":
            mentions.setdefault(e.dst, set()).add(e.src)
        elif e.rel == "about" and store.nodes.get(e.dst, Node("", "", "")).kind == "Event":
            about.setdefault(e.src, set()).add(e.dst)
    subj_events: dict[str, set[str]] = {}
    for subj, arts in mentions.items():
        for a in arts:
            subj_events.setdefault(subj, set()).update(about.get(a, set()))
    return subj_events


def derive_shared_subject_links(store: GraphStore, at: str) -> list[Edge]:
    """Connector derivation: events sharing a Subject get linked.

    An event counts for a subject when an article that mentions the
    subject is also `about` that event. Emits both directions so the
    workspace renders the link either way. Pure: caller records them.
    """
    out = []
    subj_events = _subject_events(store)
    for subj in sorted(subj_events):
        evts = sorted(subj_events[subj])
        for i, e1 in enumerate(evts):
            for e2 in evts[i + 1:]:
                out.append(Edge(src=e1, dst=e2, rel="shares-subject-with", at=at))
                out.append(Edge(src=e2, dst=e1, rel="shares-subject-with", at=at))
    return out


def followup_tasks(store: GraphStore, event_id: str) -> list[dict]:
    """Connector dispatch: investigate tasks for events related to event_id.

    For every subject of the new event, every *other* event sharing it
    becomes a follow-up task ({event_ticker, terms}) so the news agent
    digs their past too — this closes the topic -> news -> topics loop.
    Terms come from Subject node titles. Pure: caller dispatches.
    """
    subj_events = _subject_events(store)
    mine = {s for s, evts in subj_events.items() if event_id in evts}
    tasks: dict[str, set[str]] = {}
    for subj in mine:
        title = store.nodes[subj].title
        for other in sorted(subj_events[subj]):
            if other != event_id and other.startswith("kalshi:"):
                tasks.setdefault(other, set()).add(title)
    return [{"event_ticker": eid.removeprefix("kalshi:"),
             "terms": sorted(terms)}
            for eid, terms in sorted(tasks.items())]


def refresh_derived_links(api: GraphUpdateAPI) -> int:
    """Recompute + record all derived links. Idempotent via record().

    Call after every commit batch so the graph never goes stale.
    Returns the number of newly recorded edges (reinforcements don't
    count as new).
    """
    fresh = 0
    for e in derive_shared_subject_links(api.store, api.store.now()):
        if api.record(e).reason == "recorded":
            fresh += 1
    return fresh


def subgraph_as_of(store: GraphStore, ts: str) -> GraphStore:
    """Time travel: the graph as of world-time `ts` (valid_at <= ts).

    Undated nodes/edges (valid_at == "") are always included; edges
    touching pruned nodes are dropped. Timestamps compare
    lexicographically, which matches chronological order for the
    %Y%m%dT%H%M%SZ format used throughout.
    """
    snap = GraphStore()
    for nid, n in store.nodes.items():
        if not n.valid_at or n.valid_at <= ts:
            snap.nodes[nid] = n
    for e in store.edges:
        if e.src in snap.nodes and e.dst in snap.nodes and (
                not e.valid_at or e.valid_at <= ts):
            snap.edges.append(e)
    return snap
