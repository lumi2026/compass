"""Living event graph: store, gated update API, and derivation.

Thin package surface — every name below is importable straight from
`graph` (back-compatible with the original single-file module):

- graph.store: Node, Edge, GraphStore, id helpers, vocab constants.
- graph.gate: CommitResult, GraphUpdateAPI, ROLE_ACTIONS,
  proposals_to_commit.
- graph.derive: derive_shared_subject_links, derive_market_links,
  materialize_derived_nodes, materialize_derived_links, followup_tasks,
  prune_derived_links, subgraph_as_of.

ID scheme (stable URIs, shared by every agent):
  kalshi:{event_ticker}   Event    e.g. kalshi:KXSPACEXMARS-30
  market:{ticker}         Market   folded in Event.props.markets
  article:{url-sha1-12}   Article  one node per canonical URL
  subject:{slug}          Subject  folded in Article.props.subjects
  hypothesis:{slug}-{n}   Hypothesis  investigator claim, numbered per subject
  hypothesis:loop-{a}-{b}-{type}   Hypothesis  ordered pair + closed type
  hypothesis:odds-{a}-{b}-{type}   Hypothesis  odds pair + closed type
  happening:{slug}      Happening key dated event (market moves, etc.)
  audit:{target}        Audit      auditor flag on a bad link (reversible)

Edge vocabulary (only primitive rels are persisted):
  about                Article -> Event             (LLM relevance passed)
  cites                Hypothesis -> Article        (evidence)
  asserts              Hypothesis -> Event | Market (claim scope)
  challenges           Audit -> Hypothesis  (auditor flags a bad link)
  experienced          Event -> Happening   (the event unfolded, e.g.
                       a market move)

`supports` (Hypothesis -> Market | Article) and `relates-to`
(fallback) are legacy rels: accepted on read/replay, refused for new
writes. Use cites/asserts for claims, experienced for unfoldings.

Folded/derived rels are reconstructed on read and never stored:
  mentions             Article -> Subject   (from Article.props.subjects)
  shares-subject-with  Event <-> Event      (via a shared Subject)
  has-market           Event -> Market      (from Event.props.markets)
"""

from .elements import MAX_VISIBLE_NODES, to_elements
from .derive import (
    derive_market_links,
    derive_shared_subject_links,
    followup_tasks,
    materialize_derived_links,
    materialize_derived_nodes,
    prune_derived_links,
    subgraph_as_of,
)
from .persist import append_finding, boot, commit_batch, replay_log
from .gate import (
    ROLE_ACTIONS,
    CommitResult,
    GraphUpdateAPI,
    proposals_to_commit,
)
from .store import (
    DERIVED_RELS,
    EDGE_RELS,
    FOLDED_NODE_KINDS,
    FOLDED_RELS,
    GENERIC_TOKENS,
    LEGACY_RELS,
    MATERIALIZED_RELS,
    NODE_KINDS,
    PRIMITIVE_RELS,
    anchored_terms,
    fold_subjects,
    is_generic_term,
    Edge,
    GraphStore,
    Node,
    article_id,
    happening_id,
    subject_id,
)

__all__ = [
    "DERIVED_RELS",
    "EDGE_RELS",
    "FOLDED_NODE_KINDS",
    "FOLDED_RELS",
    "GENERIC_TOKENS",
    "LEGACY_RELS",
    "MATERIALIZED_RELS",
    "MAX_VISIBLE_NODES",
    "NODE_KINDS",
    "PRIMITIVE_RELS",
    "CommitResult",
    "Edge",
    "anchored_terms",
    "fold_subjects",
    "is_generic_term",
    "GraphStore",
    "GraphUpdateAPI",
    "Node",
    "ROLE_ACTIONS",
    "append_finding",
    "article_id",
    "boot",
    "commit_batch",
    "derive_market_links",
    "derive_shared_subject_links",
    "followup_tasks",
    "happening_id",
    "materialize_derived_links",
    "materialize_derived_nodes",
    "proposals_to_commit",
    "prune_derived_links",
    "replay_log",
    "subgraph_as_of",
    "subject_id",
    "to_elements",
]
