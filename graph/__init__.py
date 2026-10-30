"""Living event graph: store, gated update API, and derivation.

Thin package surface — every name below is importable straight from
`graph` (back-compatible with the original single-file module):

- graph.store: Node, Edge, GraphStore, id helpers, vocab constants.
- graph.gate: CommitResult, GraphUpdateAPI, ROLE_ACTIONS,
  proposals_to_commit.
- graph.derive: derive_shared_subject_links, followup_tasks,
  refresh_derived_links, subgraph_as_of.

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

from .derive import (
    derive_shared_subject_links,
    followup_tasks,
    refresh_derived_links,
    subgraph_as_of,
)
from .gate import (
    ROLE_ACTIONS,
    CommitResult,
    GraphUpdateAPI,
    proposals_to_commit,
)
from .store import (
    DERIVED_RELS,
    EDGE_RELS,
    NODE_KINDS,
    Edge,
    GraphStore,
    Node,
    article_id,
    subject_id,
)

__all__ = [
    "DERIVED_RELS",
    "EDGE_RELS",
    "NODE_KINDS",
    "CommitResult",
    "Edge",
    "GraphStore",
    "GraphUpdateAPI",
    "Node",
    "ROLE_ACTIONS",
    "article_id",
    "derive_shared_subject_links",
    "followup_tasks",
    "proposals_to_commit",
    "refresh_derived_links",
    "subgraph_as_of",
    "subject_id",
]
