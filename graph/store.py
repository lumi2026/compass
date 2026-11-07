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

# Folded node kinds are represented as props instead of first-class nodes
# going forward: Subject -> Article.props.subjects and Market ->
# Event.props.markets. Legacy snapshots keep theirs, and the read path
# materializes them for display.
FOLDED_NODE_KINDS = ("Subject", "Market")

# Primitive edges are directly evidenced claims. `cites` is evidence
# (Hypothesis -> Article), `asserts` is claim scope (Hypothesis ->
# Event | Market), `experienced` is an event unfolding (Event ->
# Happening, e.g. a market move). `supports` is the legacy single rel
# covering cites/asserts, `relates-to` the legacy fallback; both are
# still accepted on read/replay but refused for new writes.
PRIMITIVE_RELS = ("about", "cites", "asserts", "challenges", "experienced")

# Legacy rels: accepted by the gate only with allow_folded_writes
# (snapshot/log replay). New proposals must use cites/asserts for
# claims and experienced for event unfoldings.
LEGACY_RELS = ("supports", "relates-to")

# Folded edges are implied by props (mentions -> Article.props.subjects).
FOLDED_RELS = ("mentions",)

# Derived edges are fully recoverable from evidence or props. They are
# materialized on read (graph.derive.materialize_derived_links), never
# persisted.
DERIVED_RELS = ("shares-subject-with", "has-market")

# Everything the read path can synthesize from props/evidence.
MATERIALIZED_RELS = FOLDED_RELS + DERIVED_RELS

# Full vocabulary: kept so legacy snapshots/logs still load and display.
# New writes are limited to PRIMITIVE_RELS (supports is replay-only).
EDGE_RELS = PRIMITIVE_RELS + MATERIALIZED_RELS + LEGACY_RELS


def article_id(url: str) -> str:
    """Canonical Article node id for a URL (sha1-12, dedupes cleanly)."""
    import hashlib

    return "article:" + hashlib.sha1(url.encode()).hexdigest()[:12]


def subject_id(term: str) -> str:
    """Canonical Subject node id for a watch term or entity."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
    return "subject:" + slug


# Genre vocabulary: search terms made only of these tokens cannot
# discriminate topics ("presidential election" matches every presidential
# market), so they never become folded subjects. Shared with the news
# loop via this module to avoid a squad/scripts import cycle.
GENERIC_TOKENS = frozenset("""election elections elected electoral electorate
reelection vote votes voter voters voting poll polls polling polled
race races runoff runoffs referendum referendums plebiscite plebiscites
primary primaries caucus caucuses ballot ballots
president presidents presidential presidency elect
minister ministers ministry prime mayor mayors mayoral
governor governors gubernatorial senator senators senate senatorial
representative representatives house congress congressional
parliament parliamentary party parties nominee nominees nomination nominations
candidate candidates candidacy campaign campaigns
seat seats office offices term terms ticket tickets
coalition coalitions cabinet cabinets
chairman chairwoman chair chairs chancellor chancellors premier premiers
secretary secretaries general government governments
state states united federal national
successor succession replacement appointee appointees appointed appointment
appointments confirmation confirmations incumbent incumbents outgoing
approval approvals rating ratings
winner winners wins winning won lose loses losing lost
victory victories defeat defeats lead leads leading trail trails trailing
ahead behind favorite favorites frontrunner frontrunners upset upsets
control retain retains retained gain gains flip flips sweep sweeps
landslide landslides
which whose whom whether either neither both each every other another same such
some any several various former latter current upcoming potential possible
likely expected eventual future past recent latest early main major key
big small large old young white black
make makes making become becomes becoming remain remains stay stays
hold holds holding keep keeps keeping face faces facing
call calls called announce announces announced hit hits hitting
reach reaches reached set name names named pick picks picked
choose chooses chose beat beats beating meet meets meeting""".split())


def is_generic_term(term: str) -> bool:
    """True when a search term carries no discriminating token."""
    toks = term.lower().split()
    return bool(toks) and all(t in GENERIC_TOKENS for t in toks)


def anchored_terms(terms: list[str]) -> list[str]:
    """The discriminating subset of terms, order preserved."""
    return [t for t in terms if not is_generic_term(t)]


def fold_subjects(terms: list[str]) -> list[str]:
    """Terms worth folding into Article.props.subjects, deduped.

    Anchored (discriminating) terms only; topics whose every term is
    generic keep all of them rather than going subject-less.
    """
    seen: list[str] = []
    for term in terms:
        if isinstance(term, str) and term and term not in seen:
            seen.append(term)
    anchored = [t for t in seen if not is_generic_term(t)]
    return anchored or seen


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
    # Link strength: {"weight": <evidence URLs, default 1>,
    # "confidence": <0-1>}. Missing weight reads as 1 everywhere.
    props: dict = field(default_factory=dict)


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
